"""
Provision Demo - Lambda dispatch handler.

Routes:
  GET  /           -> Serve the SPA (index.html)
  GET  /config     -> Return app configuration (age public key, Cognito settings)
  POST /dispatch   -> Validate JWT, dispatch GitHub Actions workflow
  POST /remove     -> Validate JWT, dispatch connector removal workflow
  POST /cancel-pr  -> Validate JWT, close a pending PR and delete its branch
  POST /chat       -> Validate JWT, AI chat with Claude tool use
  GET  /connectors -> List active and pending connectors
  GET  /run-status -> Check workflow run status and find resulting PR
"""

import datetime
import re

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
CONNECTOR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]$")


def validate_connector_name(name):
    """Validate connector name format. Returns error string or None."""
    if not name:
        return "connector_name is required"
    if len(name) < 2 or not CONNECTOR_NAME_RE.match(name):
        return "Connector name must be 2-63 characters, lowercase letters, numbers, and hyphens only."
    return None

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

    name_error = validate_connector_name(connector_name)
    if name_error:
        return response_json(400, {"error": name_error})
    if connector_type not in CONNECTOR_TYPES:
        return response_json(400, {
            "error": f"Invalid connector_type. Must be one of: {', '.join(sorted(CONNECTOR_TYPES))}"
        })
    if not encrypted_payload:
        return response_json(400, {"error": "encrypted_payload is required"})

    # Check for existing connector
    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    # Check if connector already exists on main
    try:
        github_api("GET", f"/repos/{platform_repo}/contents/connectors/{connector_name}?ref=main")
        return response_json(409, {
            "error": f"Connector '{connector_name}' already exists. Remove it first before re-adding."
        })
    except urllib.error.HTTPError as e:
        if e.code != 404:
            error_body = e.read().decode() if e.fp else str(e)
            return response_json(502, {"error": f"GitHub API error: {e.code} {error_body}"})

    # Check for open onboard PRs for this connector
    try:
        prs = github_api("GET", f"/repos/{platform_repo}/pulls?state=open&per_page=100")
        for pr in (prs or []):
            head_ref = pr.get("head", {}).get("ref", "")
            if head_ref.startswith(f"feat/onboard-{connector_name}-"):
                return response_json(409, {
                    "error": f"Connector '{connector_name}' already has a pending onboarding request (PR #{pr['number']})."
                })
    except Exception:
        pass

    # Generate unique branch name
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch_name = f"feat/onboard-{connector_name}-{timestamp}"

    # Dispatch workflow
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
                    "branch_name": branch_name,
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
        "branch_name": branch_name,
    })


def handle_remove(normalized):
    """Validate auth, dispatch a connector removal workflow."""
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
    connector_name = payload.get("connector_name", "").strip()

    name_error = validate_connector_name(connector_name)
    if name_error:
        return response_json(400, {"error": name_error})

    # Verify connector exists on main
    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")
    try:
        github_api("GET", f"/repos/{platform_repo}/contents/connectors/{connector_name}?ref=main")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return response_json(404, {"error": f"Connector '{connector_name}' not found."})
        error_body = e.read().decode() if e.fp else str(e)
        return response_json(502, {"error": f"GitHub API error: {e.code} {error_body}"})

    # Check for existing open removal PR
    try:
        prs = github_api("GET", f"/repos/{platform_repo}/pulls?state=open&per_page=100")
        for pr in (prs or []):
            head_ref = pr.get("head", {}).get("ref", "")
            if head_ref.startswith(f"feat/remove-{connector_name}-"):
                return response_json(409, {
                    "error": f"Connector '{connector_name}' already has a pending removal request (PR #{pr['number']})."
                })
    except Exception:
        pass

    # Generate unique branch name
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch_name = f"feat/remove-{connector_name}-{timestamp}"

    # Dispatch removal workflow
    try:
        github_api(
            "POST",
            f"/repos/{platform_repo}/actions/workflows/remove-connector.yml/dispatches",
            {
                "ref": "main",
                "inputs": {
                    "connector_name": connector_name,
                    "requested_by": email,
                    "branch_name": branch_name,
                },
            },
        )
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return response_json(502, {"error": f"GitHub API error: {e.code} {error_body}"})

    # Poll for run ID
    time.sleep(2)
    run_id = None
    try:
        runs = github_api(
            "GET",
            f"/repos/{platform_repo}/actions/workflows/remove-connector.yml/runs?per_page=1",
        )
        if runs and runs.get("workflow_runs"):
            run_id = runs["workflow_runs"][0]["id"]
    except Exception:
        pass

    return response_json(200, {
        "message": "Removal workflow dispatched successfully",
        "connector_name": connector_name,
        "requested_by": email,
        "run_id": run_id,
        "run_url": f"https://github.com/{platform_repo}/actions/runs/{run_id}" if run_id else None,
        "branch_name": branch_name,
    })


def handle_cancel_pr(normalized):
    """Close a PR and delete its branch."""
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
    pr_number = payload.get("pr_number")
    if not pr_number:
        return response_json(400, {"error": "pr_number is required"})

    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    try:
        # Get PR details to find the branch
        pr = github_api("GET", f"/repos/{platform_repo}/pulls/{pr_number}")
        head_ref = pr.get("head", {}).get("ref", "")

        # Only allow cancelling onboard/remove PRs
        if not head_ref.startswith("feat/onboard-") and not head_ref.startswith("feat/remove-"):
            return response_json(403, {"error": "Cannot cancel this PR"})

        # Verify the caller is the one who requested this connector
        pr_body = pr.get("body", "") or ""
        requested_by = None
        for line in pr_body.split("\n"):
            if "**Requested by:**" in line:
                requested_by = line.split("**Requested by:**")[-1].strip()
                break
        if not requested_by or requested_by != email:
            return response_json(403, {"error": "You can only cancel your own connector requests."})

        # Close the PR
        github_api("PATCH", f"/repos/{platform_repo}/pulls/{pr_number}", {"state": "closed"})

        # Delete the branch
        try:
            github_api("DELETE", f"/repos/{platform_repo}/git/refs/heads/{head_ref}")
        except Exception:
            pass  # Branch may already be deleted

    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return response_json(502, {"error": f"GitHub API error: {e.code} {error_body}"})

    return response_json(200, {"message": "PR closed and branch deleted", "pr_number": pr_number})


def handle_connectors(normalized):
    """List existing connectors (on main) and pending connector PRs."""
    # Require authentication to prevent unauthenticated API rate limit abuse
    headers = normalized["headers"]
    auth_header = None
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth_header = value
            break
    if not auth_header or not auth_header.startswith("Bearer "):
        return response_json(401, {"error": "Authentication required"})
    try:
        validate_cognito_token(auth_header[7:])
    except Exception:
        return response_json(401, {"error": "Invalid or expired token"})

    return response_json(200, _list_connectors_internal())


def _extract_connector_name(branch_ref, prefix):
    """Extract connector name from branch ref, stripping prefix and timestamp suffix."""
    remainder = branch_ref[len(prefix):]
    match = re.match(r"^(.+)-\d{8}-\d{6}$", remainder)
    if match:
        return match.group(1)
    return remainder


# ---------------------------------------------------------------------------
# Internal functions (shared by HTTP handlers and chat tools)
# ---------------------------------------------------------------------------

def _list_connectors_internal():
    """List connectors — returns a dict (not an HTTP response)."""
    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")
    connectors = []

    try:
        contents = github_api("GET", f"/repos/{platform_repo}/contents/connectors?ref=main")
        if isinstance(contents, list):
            for item in contents:
                if item.get("type") == "dir":
                    name = item["name"]
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
                    connectors.append({"name": name, "connector_type": connector_type, "status": "active"})
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    active_names = {c["name"] for c in connectors}

    try:
        prs = github_api("GET", f"/repos/{platform_repo}/pulls?state=open&per_page=100")
        if prs:
            for pr in prs:
                head_ref = pr.get("head", {}).get("ref", "")
                body = pr.get("body", "") or ""

                if head_ref.startswith("feat/onboard-"):
                    name = _extract_connector_name(head_ref, "feat/onboard-")
                    if name in active_names:
                        continue
                    status = "pending"
                elif head_ref.startswith("feat/remove-"):
                    name = _extract_connector_name(head_ref, "feat/remove-")
                    status = "removing"
                else:
                    continue

                connector_type = None
                requested_by = None
                for line in body.split("\n"):
                    if "**Type:**" in line:
                        connector_type = line.split("**Type:**")[-1].strip()
                    if "**Requested by:**" in line:
                        requested_by = line.split("**Requested by:**")[-1].strip()

                connectors.append({
                    "name": name,
                    "connector_type": connector_type,
                    "status": status,
                    "pr_number": pr.get("number"),
                    "pr_url": pr.get("html_url"),
                    "requested_by": requested_by,
                })
    except Exception:
        pass

    return {"connectors": connectors}


def _update_form_internal(params):
    """Update the onboard form with fields collected so far. Returns a handoff."""
    return {
        "handoff": "onboard_form",
        "message": "Form updated on the Onboard tab. Continue asking for the next field.",
        "connector_name": params.get("connector_name", ""),
        "connector_type": params.get("connector_type", ""),
        "config": params.get("config", {}),
    }


CONNECTOR_SECRET_FIELDS = {
    "s3": [],
    "postgres": ["username", "password"],
    "rest-api": ["api_key"],
    "sftp": ["username", "ssh_private_key"],
}


def _submit_onboard_internal(params):
    """Submit a connector for onboarding. Returns a handoff for secure secret input if needed."""
    connector_name = params.get("connector_name", "").strip()
    connector_type = params.get("connector_type", "").strip()
    config = params.get("config", {})

    if not connector_name or connector_type not in CONNECTOR_TYPES:
        return {"error": "Invalid connector name or type."}

    secret_fields = CONNECTOR_SECRET_FIELDS.get(connector_type, [])

    if secret_fields:
        # Return a handoff — frontend will show a secure inline form for secrets
        return {
            "handoff": "secure_secrets",
            "message": f"A secure input form will appear for you to enter the required secrets ({', '.join(secret_fields)}). Secrets are encrypted in your browser and never pass through the chat.",
            "connector_name": connector_name,
            "connector_type": connector_type,
            "config": config,
            "secret_fields": secret_fields,
        }
    else:
        # No secrets needed (e.g., S3) — submit directly via form
        return {
            "handoff": "auto_submit",
            "message": f"Submitting {connector_name} for onboarding...",
            "connector_name": connector_name,
            "connector_type": connector_type,
            "config": config,
        }


def _prepare_onboard_internal(params):
    """Validate config for onboarding and return handoff data."""
    connector_name = params.get("connector_name", "").strip()
    connector_type = params.get("connector_type", "").strip()
    config = params.get("config", {})

    if not connector_name:
        return {"error": "connector_name is required"}
    if connector_type not in CONNECTOR_TYPES:
        return {"error": f"Invalid connector_type. Must be one of: {', '.join(sorted(CONNECTOR_TYPES))}"}

    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    # Check if connector already exists
    try:
        github_api("GET", f"/repos/{platform_repo}/contents/connectors/{connector_name}?ref=main")
        return {"error": f"Connector '{connector_name}' already exists. Remove it first before re-adding."}
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return {"error": f"GitHub API error: {e.code}"}

    # Check for open onboard PRs
    try:
        prs = github_api("GET", f"/repos/{platform_repo}/pulls?state=open&per_page=100")
        for pr in (prs or []):
            head_ref = pr.get("head", {}).get("ref", "")
            if head_ref.startswith(f"feat/onboard-{connector_name}-"):
                return {"error": f"Connector '{connector_name}' already has a pending onboarding request (PR #{pr['number']})."}
    except Exception:
        pass

    return {
        "handoff": "onboard_form",
        "message": f"Switching to the secure onboarding form with pre-filled config for '{connector_name}'. Please enter the required secrets in the form — they will be encrypted client-side before submission.",
        "connector_name": connector_name,
        "connector_type": connector_type,
        "config": config,
    }


def _onboard_connector_internal(params, email):
    """Onboard a connector with server-side age encryption. Returns a dict."""
    import pyrage

    connector_name = params.get("connector_name", "").strip()
    connector_type = params.get("connector_type", "").strip()
    config = params.get("config", {})
    secrets = params.get("secrets", {})

    if not connector_name:
        return {"error": "connector_name is required"}
    if connector_type not in CONNECTOR_TYPES:
        return {"error": f"Invalid connector_type. Must be one of: {', '.join(sorted(CONNECTOR_TYPES))}"}

    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    # Check if connector already exists on main
    try:
        github_api("GET", f"/repos/{platform_repo}/contents/connectors/{connector_name}?ref=main")
        return {"error": f"Connector '{connector_name}' already exists. Remove it first before re-adding."}
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return {"error": f"GitHub API error: {e.code}"}

    # Check for open onboard PRs
    try:
        prs = github_api("GET", f"/repos/{platform_repo}/pulls?state=open&per_page=100")
        for pr in (prs or []):
            head_ref = pr.get("head", {}).get("ref", "")
            if head_ref.startswith(f"feat/onboard-{connector_name}-"):
                return {"error": f"Connector '{connector_name}' already has a pending onboarding request (PR #{pr['number']})."}
    except Exception:
        pass

    # Validate config fields
    AWS_REGIONS = {
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "af-south-1", "ap-east-1", "ap-south-1", "ap-south-2",
        "ap-southeast-1", "ap-southeast-2", "ap-southeast-3", "ap-southeast-4",
        "ap-northeast-1", "ap-northeast-2", "ap-northeast-3",
        "ca-central-1", "ca-west-1",
        "eu-central-1", "eu-central-2", "eu-west-1", "eu-west-2", "eu-west-3",
        "eu-north-1", "eu-south-1", "eu-south-2",
        "il-central-1", "me-central-1", "me-south-1", "sa-east-1",
    }
    FIELD_RULES = {
        "bucket_name": (lambda v: bool(re.match(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$", v)),
                        "Bucket name must be 3-63 characters, lowercase letters, numbers, hyphens, and periods."),
        "region": (lambda v: v in AWS_REGIONS,
                   f"Invalid AWS region. Must be one of: {', '.join(sorted(list(AWS_REGIONS)[:5]))}..."),
        "host": (lambda v: bool(re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$", v)),
                 "Host must be a valid hostname or IP address."),
        "port": (lambda v: v.isdigit() and 1 <= int(v) <= 65535,
                 "Port must be a number between 1 and 65535."),
        "database": (lambda v: bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v)),
                     "Database name must start with a letter or underscore."),
        "base_url": (lambda v: v.startswith("http://") or v.startswith("https://"),
                     "Base URL must start with http:// or https://."),
        "polling_schedule": (lambda v: bool(re.match(r"^[*0-9,\-/]+\s+[*0-9,\-/]+\s+[*0-9,\-/]+\s+[*0-9,\-/]+\s+[*0-9,\-/]+$", v.strip())),
                             "Polling schedule must be a valid cron expression."),
    }
    for field, value in config.items():
        if field in FIELD_RULES:
            validator, message = FIELD_RULES[field]
            if not validator(str(value)):
                return {"error": f"Invalid {field}: {message}"}

    # Build and encrypt payload
    payload_obj = {
        "connector_name": connector_name,
        "connector_type": connector_type,
        "config": config,
        "secrets": secrets,
    }
    age_pub_key = get_ssm_param(f"/{APP_NAME}/age-public-key")
    recipient = pyrage.x25519.Recipient.from_str(age_pub_key)
    encrypted = pyrage.encrypt(json.dumps(payload_obj).encode(), [recipient])
    encrypted_payload = base64.b64encode(encrypted).decode()

    # Generate unique branch name and dispatch
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch_name = f"feat/onboard-{connector_name}-{timestamp}"

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
                    "branch_name": branch_name,
                },
            },
        )
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {"error": f"GitHub API error: {e.code} {error_body}"}

    # Poll for run ID
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

    return {
        "message": f"Onboarding workflow dispatched for connector '{connector_name}'. A PR will be created shortly.",
        "connector_name": connector_name,
        "connector_type": connector_type,
        "run_id": run_id,
        "run_url": f"https://github.com/{platform_repo}/actions/runs/{run_id}" if run_id else None,
    }


def _remove_connector_internal(params, email):
    """Remove a connector. Returns a dict."""
    connector_name = params.get("connector_name", "").strip()

    if not connector_name:
        return {"error": "connector_name is required"}

    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    # Verify connector exists
    try:
        github_api("GET", f"/repos/{platform_repo}/contents/connectors/{connector_name}?ref=main")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"Connector '{connector_name}' not found."}
        return {"error": f"GitHub API error: {e.code}"}

    # Check for existing removal PR
    try:
        prs = github_api("GET", f"/repos/{platform_repo}/pulls?state=open&per_page=100")
        for pr in (prs or []):
            head_ref = pr.get("head", {}).get("ref", "")
            if head_ref.startswith(f"feat/remove-{connector_name}-"):
                return {"error": f"Connector '{connector_name}' already has a pending removal request (PR #{pr['number']})."}
    except Exception:
        pass

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    branch_name = f"feat/remove-{connector_name}-{timestamp}"

    try:
        github_api(
            "POST",
            f"/repos/{platform_repo}/actions/workflows/remove-connector.yml/dispatches",
            {
                "ref": "main",
                "inputs": {
                    "connector_name": connector_name,
                    "requested_by": email,
                    "branch_name": branch_name,
                },
            },
        )
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {"error": f"GitHub API error: {e.code} {error_body}"}

    return {"message": f"Removal workflow dispatched for connector '{connector_name}'. A PR will be created shortly."}


def _cancel_pr_internal(params):
    """Cancel a pending PR. Returns a dict."""
    pr_number = params.get("pr_number")
    if not pr_number:
        return {"error": "pr_number is required"}

    platform_repo = get_ssm_param(f"/{APP_NAME}/platform-repo")

    try:
        pr = github_api("GET", f"/repos/{platform_repo}/pulls/{pr_number}")
        head_ref = pr.get("head", {}).get("ref", "")

        if not head_ref.startswith("feat/onboard-") and not head_ref.startswith("feat/remove-"):
            return {"error": "Cannot cancel this PR — it's not a connector onboarding or removal request."}

        github_api("PATCH", f"/repos/{platform_repo}/pulls/{pr_number}", {"state": "closed"})

        try:
            github_api("DELETE", f"/repos/{platform_repo}/git/refs/heads/{head_ref}")
        except Exception:
            pass

    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {"error": f"GitHub API error: {e.code} {error_body}"}

    return {"message": f"PR #{pr_number} closed and branch deleted."}


# ---------------------------------------------------------------------------
# Chat (Claude AI with tool use)
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """You are Provision, an AI assistant for managing data connectors. You help users onboard, list, remove, and manage connectors through natural language.

## Available Connector Types

- **S3**: Config: bucket_name (3-63 chars, lowercase, hyphens, periods), region (AWS region like us-east-1, us-west-2, eu-west-1). No secrets.
- **PostgreSQL**: Config: host (hostname/IP), port (1-65535, default 5432), database (letters/numbers/underscores). Secrets: username, password.
- **REST API**: Config: base_url (valid HTTP/HTTPS URL), polling_schedule (cron expression, e.g. "*/15 * * * *"). Secrets: api_key.
- **SFTP**: Config: host (hostname/IP), port (1-65535, default 22). Secrets: username, ssh_private_key (PEM format).

## Behavior

1. When onboarding a connector, ask for one field at a time. After each answer, call update_form with all fields known so far. This pre-fills the Onboard tab form in real-time so the user can switch to it at any point.
2. Ask in this order: connector type, connector name, then each config field for that type one by one.
3. Validate values as you go — for example, region must be a valid AWS region (us-east-1, us-west-2, etc.), port must be 1-65535, URLs must start with http/https. If a value is invalid, explain why and ask again.
4. Once all config fields are gathered, show a summary and ask the user to confirm before submitting. Mention they can also check or edit the pre-filled form on the Onboard tab.
5. For connector types with secrets: after user confirms, call submit_onboard. A secure input form will appear inline in the chat for the user to enter secrets — secrets are encrypted client-side and never pass through this chat.
6. For connector types with no secrets (e.g., S3): call submit_onboard after user confirms. Submission proceeds immediately.
7. NEVER ask users to type passwords, API keys, SSH keys, or other secrets in the chat. Always use submit_onboard which handles secrets securely.
8. Before destructive actions (remove, cancel), confirm with the user first.
9. If the user provides multiple fields at once, that's fine — call update_form with all of them.
4. Present connector lists in a readable format. Statuses: "active" = merged and live, "pending" = awaiting PR review, "removing" = removal PR open.
5. If a tool call fails, explain the error in plain language.
6. Connector names must be lowercase letters, numbers, and hyphens only.
7. Keep responses concise."""

CHAT_TOOLS = [
    {
        "name": "list_connectors",
        "description": "List all connectors: active (merged on main), pending onboarding (open PR), and pending removal (open PR). Returns each connector's name, type, status, PR number, and PR URL.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "update_form",
        "description": "Update the Onboard form with field values collected so far. Call this every time you learn new field values from the user — the form updates in real-time so the user can switch to the Onboard tab at any point. Include all known fields each time (not just new ones). After calling this, always ask the user for the next required field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "connector_name": {"type": "string", "description": "Connector name if known."},
                "connector_type": {"type": "string", "enum": ["s3", "postgres", "rest-api", "sftp"], "description": "Connector type if known."},
                "config": {"type": "object", "description": "Config fields known so far."}
            },
            "required": []
        }
    },
    {
        "name": "submit_onboard",
        "description": "Submit a connector for onboarding after the user confirms the config summary. For connector types with secrets, a secure inline form will appear in the chat for the user to enter secrets with client-side encryption. For types with no secrets (S3), submission proceeds immediately. Always call update_form first to ensure the form is pre-filled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "connector_name": {"type": "string", "description": "The connector name."},
                "connector_type": {"type": "string", "enum": ["s3", "postgres", "rest-api", "sftp"]},
                "config": {"type": "object", "description": "Non-secret configuration fields."}
            },
            "required": ["connector_name", "connector_type", "config"]
        }
    },
    {
        "name": "remove_connector",
        "description": "Remove an active connector by dispatching a removal workflow that creates a PR to delete the connector directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "connector_name": {"type": "string", "description": "Name of the active connector to remove."}
            },
            "required": ["connector_name"]
        }
    },
    {
        "name": "cancel_pr",
        "description": "Cancel a pending onboarding or removal request by closing its PR and deleting the branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "description": "The GitHub PR number to cancel."}
            },
            "required": ["pr_number"]
        }
    }
]


def call_claude_api(messages, form_state=None):
    """Call Claude API with tool use via raw HTTP."""
    api_key = get_secret(f"{APP_NAME}/anthropic-api-key")
    system = CHAT_SYSTEM_PROMPT
    if form_state:
        form_context = "\n\n## Current Onboard Form State\nThe user may have edited these values directly on the Onboard tab form:\n"
        if form_state.get("connector_type"):
            form_context += f"- Connector Type: {form_state['connector_type']}\n"
        if form_state.get("connector_name"):
            form_context += f"- Connector Name: {form_state['connector_name']}\n"
        if form_state.get("config"):
            for k, v in form_state["config"].items():
                if v:
                    form_context += f"- {k}: {v}\n"
        form_context += "\nUse these values as the current state. If the user changed the connector type or fields on the form, acknowledge the changes and continue from the updated state."
        system = system + form_context
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": system,
        "tools": CHAT_TOOLS,
        "messages": messages,
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=25)
    return json.loads(resp.read())


def execute_tool(tool_name, tool_input, email):
    """Execute a chat tool and return the result as a dict."""
    try:
        if tool_name == "list_connectors":
            return _list_connectors_internal()
        elif tool_name == "update_form":
            return _update_form_internal(tool_input)
        elif tool_name == "submit_onboard":
            return _submit_onboard_internal(tool_input)
        elif tool_name == "remove_connector":
            return _remove_connector_internal(tool_input, email)
        elif tool_name == "cancel_pr":
            return _cancel_pr_internal(tool_input)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        return {"error": str(e)}


def handle_chat(normalized):
    """AI chat endpoint with Claude tool use loop."""
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
    messages = payload.get("messages", [])
    form_state = payload.get("form_state")

    if not messages:
        return response_json(400, {"error": "messages array is required"})

    # Tool-use agentic loop
    MAX_TOOL_ROUNDS = 5
    handoff = None
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = call_claude_api(messages, form_state)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else str(e)
            return response_json(502, {"error": f"Claude API error: {e.code} {error_body}"})
        except Exception as e:
            return response_json(502, {"error": f"Claude API error: {str(e)}"})

        # Append assistant response to messages
        messages.append({"role": "assistant", "content": response["content"]})

        # Check for tool_use blocks
        tool_uses = [b for b in response["content"] if b.get("type") == "tool_use"]
        if not tool_uses:
            break  # Pure text response, done

        # Execute each tool and collect results
        tool_results = []
        for tool_use in tool_uses:
            result = execute_tool(tool_use["name"], tool_use["input"], email)
            if result.get("handoff"):
                handoff = result
            if result.get("run_id"):
                handoff = handoff or {}
                handoff["dispatch"] = {
                    "run_id": result["run_id"],
                    "run_url": result.get("run_url"),
                    "connector_name": result.get("connector_name"),
                    "connector_type": result.get("connector_type"),
                }
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    # Extract text from the last assistant message
    assistant_text = ""
    if messages and messages[-1].get("role") == "assistant":
        for block in messages[-1]["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                assistant_text += block["text"]

    # If the loop ended with no text (e.g., after a tool call), make one more call
    if not assistant_text.strip() and messages:
        try:
            response = call_claude_api(messages, form_state)
            messages.append({"role": "assistant", "content": response["content"]})
            for block in response["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    assistant_text += block["text"]
        except Exception:
            pass

    resp_body = {
        "reply": assistant_text,
        "messages": messages,
    }
    if handoff:
        resp_body["handoff"] = handoff

    return response_json(200, resp_body)


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
        try:
            prs = github_api(
                "GET",
                f"/repos/{platform_repo}/pulls?state=open&per_page=100",
            )
            for pr in (prs or []):
                head_ref = pr.get("head", {}).get("ref", "")
                if head_ref.startswith(f"feat/onboard-{connector_name}-") or \
                   head_ref.startswith(f"feat/remove-{connector_name}-"):
                    result["pr_url"] = pr.get("html_url")
                    result["pr_number"] = pr.get("number")
                    break
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
    elif method == "POST" and path == "/remove":
        return handle_remove(normalized)
    elif method == "POST" and path == "/cancel-pr":
        return handle_cancel_pr(normalized)
    elif method == "POST" and path == "/chat":
        return handle_chat(normalized)
    elif method == "GET" and path == "/connectors":
        return handle_connectors(normalized)
    elif method == "GET" and path == "/run-status":
        return handle_run_status(normalized)
    else:
        return response_json(404, {"error": "Not found"})
