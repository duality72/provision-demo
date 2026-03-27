"""
Provision Demo - Lambda dispatch handler.

Routes:
  GET  /           -> Serve the SPA (index.html)
  GET  /config     -> Return app configuration (age public key, Cognito settings)
  POST /dispatch   -> Validate JWT, dispatch GitHub Actions workflow
  GET  /run-status -> Check workflow run status and find resulting PR
"""

import json
import os
import time
import base64
import urllib.request
import urllib.parse

import jwt  # PyJWT from Lambda layer

import boto3

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_REGION = os.environ.get("AWS_REGION", "us-east-1")
APP_NAME = os.environ.get("APP_NAME", "provision-demo")

CONNECTOR_TYPES = {"s3", "postgres", "rest-api", "sftp"}

# In-memory caches (persist across warm invocations)
_jwks_cache = None
_jwks_cache_time = 0
JWKS_CACHE_TTL = 3600

_ssm_cache = {}
SSM_CACHE_TTL = 300

_secrets_cache = {}
SECRETS_CACHE_TTL = 300

_installation_token = None
_installation_token_expiry = 0

# AWS SDK clients
ssm_client = boto3.client("ssm")
secrets_client = boto3.client("secretsmanager")


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

def get_ssm_param(name):
    """Get SSM parameter value with caching."""
    now = time.time()
    if name in _ssm_cache and now - _ssm_cache[name]["time"] < SSM_CACHE_TTL:
        return _ssm_cache[name]["value"]
    resp = ssm_client.get_parameter(Name=name)
    value = resp["Parameter"]["Value"]
    _ssm_cache[name] = {"value": value, "time": now}
    return value


def get_secret(name):
    """Get Secrets Manager secret value with caching."""
    now = time.time()
    if name in _secrets_cache and now - _secrets_cache[name]["time"] < SECRETS_CACHE_TTL:
        return _secrets_cache[name]["value"]
    resp = secrets_client.get_secret_value(SecretId=name)
    value = resp["SecretString"]
    _secrets_cache[name] = {"value": value, "time": now}
    return value


def get_cognito_client_id():
    """Get Cognito client ID from SSM (avoids circular dependency with Function URL)."""
    return get_ssm_param(f"/{APP_NAME}/cognito-client-id")


def get_app_url():
    """Get the application base URL from SSM."""
    return get_ssm_param(f"/{APP_NAME}/app-url")


# ---------------------------------------------------------------------------
# Cognito JWT validation
# ---------------------------------------------------------------------------

def get_jwks():
    """Fetch and cache Cognito JWKS."""
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and now - _jwks_cache_time < JWKS_CACHE_TTL:
        return _jwks_cache
    url = (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
        f"{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    )
    resp = urllib.request.urlopen(url, timeout=5)
    _jwks_cache = json.loads(resp.read())
    _jwks_cache_time = now
    return _jwks_cache


def validate_cognito_token(token):
    """Validate a Cognito ID token and return claims."""
    client_id = get_cognito_client_id()
    jwks = get_jwks()
    header = jwt.get_unverified_header(token)
    kid = header["kid"]

    key_data = None
    for key in jwks["keys"]:
        if key["kid"] == kid:
            key_data = key
            break
    if not key_data:
        raise ValueError("Token key ID not found in JWKS")

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience=client_id,
        issuer=f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}",
        options={"require": ["exp", "iss", "aud", "token_use"]},
    )

    if claims.get("token_use") != "id":
        raise ValueError("Token is not an ID token")

    return claims


# ---------------------------------------------------------------------------
# GitHub App authentication
# ---------------------------------------------------------------------------

def generate_github_jwt():
    """Generate a JWT for GitHub App authentication."""
    app_id = get_ssm_param(f"/{APP_NAME}/github-app-id")
    private_key_b64 = get_secret(f"{APP_NAME}/github-app-private-key")
    private_key = base64.b64decode(private_key_b64)

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token():
    """Get or refresh a GitHub App installation token (cached 55 min)."""
    global _installation_token, _installation_token_expiry
    now = time.time()
    if _installation_token and now < _installation_token_expiry:
        return _installation_token

    installation_id = get_ssm_param(f"/{APP_NAME}/github-app-installation-id")
    app_jwt = generate_github_jwt()

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())

    _installation_token = data["token"]
    _installation_token_expiry = now + (55 * 60)
    return _installation_token


def github_api(method, path, body=None):
    """Make a GitHub API request using the installation token."""
    token = get_installation_token()
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=10)
    if resp.status == 204:
        return None
    return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Event normalization (Function URL vs ALB)
# ---------------------------------------------------------------------------

def normalize_event(event):
    """Extract method, path, headers, query params from Function URL or ALB event."""
    if "requestContext" in event and "http" in event.get("requestContext", {}):
        # Lambda Function URL (API Gateway v2 format)
        http = event["requestContext"]["http"]
        return {
            "method": http.get("method", "GET"),
            "path": event.get("rawPath", "/"),
            "headers": event.get("headers", {}),
            "queryStringParameters": event.get("queryStringParameters") or {},
            "body": event.get("body", ""),
            "isBase64Encoded": event.get("isBase64Encoded", False),
        }
    else:
        # ALB format
        return {
            "method": event.get("httpMethod", "GET"),
            "path": event.get("path", "/"),
            "headers": event.get("headers", {}),
            "queryStringParameters": event.get("queryStringParameters") or {},
            "body": event.get("body", ""),
            "isBase64Encoded": event.get("isBase64Encoded", False),
        }


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_index(event):
    """Serve the SPA HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r") as f:
        html = f.read()
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": html,
    }


def handle_config(event):
    """Return application configuration for the frontend."""
    age_pub_key = get_ssm_param(f"/{APP_NAME}/age-public-key")
    client_id = get_cognito_client_id()
    app_url = get_app_url()
    return response_json(200, {
        "age_public_key": age_pub_key,
        "cognito_client_id": client_id,
        "cognito_domain": f"https://{os.environ.get('COGNITO_DOMAIN', '')}",
        "cognito_region": COGNITO_REGION,
        "redirect_uri": app_url,
    })


def handle_dispatch(normalized):
    """Validate auth, dispatch a GitHub Actions workflow."""
    body = normalized["body"]
    if normalized["isBase64Encoded"]:
        body = base64.b64decode(body).decode()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return response_json(400, {"error": "Invalid JSON body"})

    # Validate authorization
    headers = normalized["headers"]
    auth_header = None
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth_header = value
            break
    if not auth_header or not auth_header.startswith("Bearer "):
        return response_json(401, {"error": "Missing or invalid Authorization header"})

    token = auth_header[7:]
    try:
        claims = validate_cognito_token(token)
    except Exception as e:
        return response_json(401, {"error": f"Token validation failed: {str(e)}"})

    email = claims.get("email", "unknown")

    # Validate payload
    connector_name = payload.get("connector_name", "").strip()
    connector_type = payload.get("connector_type", "").strip()
    encrypted_payload = payload.get("encrypted_payload", "").strip()

    if not connector_name:
        return response_json(400, {"error": "connector_name is required"})
    if connector_type not in CONNECTOR_TYPES:
        return response_json(400, {
            "error": f"Invalid connector_type. Must be one of: {', '.join(sorted(CONNECTOR_TYPES))}"
        })
    if not encrypted_payload:
        return response_json(400, {"error": "encrypted_payload is required"})

    # Dispatch workflow
    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")
    try:
        github_api(
            "POST",
            f"/repos/{platform_repo}/actions/workflows/onboard-connector.yml/dispatches",
            {
                "ref": "main",
                "inputs": {
                    "connector_name": connector_name,
                    "connector_type": connector_type,
                    "encrypted_payload": encrypted_payload,
                    "requested_by": email,
                },
            },
        )
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return response_json(502, {"error": f"GitHub API error: {e.code} {error_body}"})

    # workflow_dispatch doesn't return a run ID, so poll briefly
    time.sleep(2)
    run_id = None
    try:
        runs = github_api(
            "GET",
            f"/repos/{platform_repo}/actions/workflows/onboard-connector.yml/runs?per_page=1",
        )
        if runs and runs.get("workflow_runs"):
            run_id = runs["workflow_runs"][0]["id"]
    except Exception:
        pass

    return response_json(200, {
        "message": "Workflow dispatched successfully",
        "connector_name": connector_name,
        "connector_type": connector_type,
        "requested_by": email,
        "run_id": run_id,
        "run_url": f"https://github.com/{platform_repo}/actions/runs/{run_id}" if run_id else None,
    })


def handle_connectors(normalized):
    """List existing connectors (on main) and pending connector PRs."""
    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    connectors = []

    # Existing connectors: list directories under connectors/ on main
    try:
        contents = github_api(
            "GET",
            f"/repos/{platform_repo}/contents/connectors?ref=main",
        )
        if isinstance(contents, list):
            for item in contents:
                if item.get("type") == "dir":
                    name = item["name"]
                    # Try to read config.json for connector type
                    connector_type = None
                    try:
                        config_resp = github_api(
                            "GET",
                            f"/repos/{platform_repo}/contents/connectors/{name}/config.json?ref=main",
                        )
                        if config_resp and config_resp.get("content"):
                            config_data = json.loads(base64.b64decode(config_resp["content"]))
                            connector_type = config_data.get("connector_type")
                    except Exception:
                        pass
                    connectors.append({
                        "name": name,
                        "connector_type": connector_type,
                        "status": "active",
                    })
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    # Pending connectors: open PRs with feat/onboard- branch prefix
    try:
        prs = github_api(
            "GET",
            f"/repos/{platform_repo}/pulls?state=open&per_page=100",
        )
        if prs:
            for pr in prs:
                head_ref = pr.get("head", {}).get("ref", "")
                if head_ref.startswith("feat/onboard-"):
                    name = head_ref.replace("feat/onboard-", "", 1)
                    # Extract connector type from PR body if possible
                    connector_type = None
                    body = pr.get("body", "") or ""
                    for line in body.split("\n"):
                        if "**Type:**" in line:
                            connector_type = line.split("**Type:**")[-1].strip()
                            break
                    connectors.append({
                        "name": name,
                        "connector_type": connector_type,
                        "status": "pending",
                        "pr_number": pr.get("number"),
                        "pr_url": pr.get("html_url"),
                        "requested_by": None,
                    })
                    # Extract requested_by from body
                    for line in body.split("\n"):
                        if "**Requested by:**" in line:
                            connectors[-1]["requested_by"] = line.split("**Requested by:**")[-1].strip()
                            break
    except Exception:
        pass

    return response_json(200, {"connectors": connectors})


def handle_run_status(normalized):
    """Check the status of a workflow run and find the resulting PR."""
    params = normalized["queryStringParameters"]
    run_id = params.get("run_id")
    connector_name = params.get("connector_name")

    if not run_id:
        return response_json(400, {"error": "run_id query parameter is required"})

    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    try:
        run = github_api("GET", f"/repos/{platform_repo}/actions/runs/{run_id}")
    except urllib.error.HTTPError as e:
        return response_json(502, {"error": f"GitHub API error: {e.code}"})

    result = {
        "run_id": run_id,
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "run_url": run.get("html_url"),
    }

    if run.get("status") == "completed" and run.get("conclusion") == "success" and connector_name:
        branch = f"feat/onboard-{connector_name}"
        try:
            owner, repo = platform_repo.split("/", 1)
            prs = github_api(
                "GET",
                f"/repos/{platform_repo}/pulls?head={owner}:{branch}&state=open",
            )
            if prs:
                result["pr_url"] = prs[0].get("html_url")
                result["pr_number"] = prs[0].get("number")
        except Exception:
            pass

    return response_json(200, result)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def response_json(status_code, body):
    """Create a JSON API response."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    """Main Lambda handler - route dispatcher."""
    normalized = normalize_event(event)
    method = normalized["method"]
    path = normalized["path"]

    if method == "OPTIONS":
        return response_json(200, {})

    if method == "GET" and path == "/":
        return handle_index(event)
    elif method == "GET" and path == "/config":
        return handle_config(event)
    elif method == "POST" and path == "/dispatch":
        return handle_dispatch(normalized)
    elif method == "GET" and path == "/connectors":
        return handle_connectors(normalized)
    elif method == "GET" and path == "/run-status":
        return handle_run_status(normalized)
    else:
        return response_json(404, {"error": "Not found"})
