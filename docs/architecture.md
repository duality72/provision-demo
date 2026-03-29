# Architecture

## System Overview

Provision Demo is a two-repo system for self-service connector onboarding. A web application collects connector configuration, encrypts secrets client-side, and triggers an automated pipeline that generates infrastructure-as-code and opens a pull request.

### Components

- **provision-demo** (this repo): Lambda application, frontend SPA, and all Terraform infrastructure definitions
- **provision-demo-platform**: Target repo that receives workflow dispatches and PRs with connector definitions

## Data Flow

### Onboarding

```
┌─────────┐     PKCE Auth     ┌─────────┐
│ Browser  │ ───────────────> │ Cognito  │
│  (SPA)   │ <─────────────── │          │
└────┬─────┘   ID Token       └──────────┘
     │
     │ age-encrypt(payload)
     │ POST /dispatch + Bearer token
     │
┌────▼─────┐                  ┌──────────────┐
│  Lambda   │ ── check dup ─> │ GitHub API   │
│ dispatch  │    (App auth)   │              │
│           │ workflow_dispatch│              │
└────┬─────┘                  └──────┬───────┘
     │                               │
     │ poll /run-status              │ triggers
     │                               │
     │                        ┌──────▼───────┐
     │                        │ GitHub       │
     │                        │ Actions      │
     │                        │              │
     │                        │ - decrypt    │
     │                        │ - generate   │
     │                        │ - commit     │
     │                        │ - open PR    │
     │                        └──────┬───────┘
     │                               │
     │ PR URL                        │ PR
     │ <─────────────────────────────┘
```

### Removal

```
┌─────────┐   POST /remove    ┌──────────┐
│ Browser  │ ───────────────> │  Lambda   │
│  (SPA)   │                  │          │
└────┬─────┘                  └────┬─────┘
     │                             │
     │                             │ verify exists
     │                             │ check no dup removal PR
     │                             │ workflow_dispatch
     │                             │
     │                        ┌────▼─────────┐
     │                        │ GitHub       │
     │                        │ Actions      │
     │                        │              │
     │                        │ - git rm -r  │
     │                        │ - commit     │
     │                        │ - open PR    │
     │  auto-refresh          └──────┬───────┘
     │ <─────────────────────────────┘
```

## Chat Agent

The Chat tab provides an AI agent powered by Claude's tool-use API. The agent manages connectors through natural language conversation.

### Tools

| Tool | Description |
|------|-------------|
| `list_connectors` | Lists all connectors with their status |
| `update_form` | Pre-fills the Onboard form incrementally as fields are gathered |
| `submit_onboard` | Triggers secure submission — inline secret form or auto-submit for no-secret types |
| `remove_connector` | Dispatches a connector removal workflow |
| `cancel_pr` | Closes a pending PR and deletes its branch |

### Security Model

Secrets never pass through the Claude API. When the agent calls `submit_onboard` for a connector type with secrets, the Lambda returns a `secure_secrets` handoff. The frontend renders an inline form with password fields in the chat. When the user fills in the secrets and clicks "Encrypt & Submit":

1. Secrets are encrypted client-side with age in the browser
2. The encrypted payload is sent directly to `/dispatch` (not `/chat`)
3. Claude only sees non-secret config fields (hosts, ports, regions)

For connector types without secrets (S3), submission proceeds automatically after the user confirms.

### Voice Input

The chat includes a microphone button for voice-to-text input using the browser's SpeechRecognition API. Available in Chrome and Edge only — the button is hidden in unsupported browsers. Voice input is transcribed to text and auto-sent. Responses are text only (no text-to-speech).

## Lambda Function

The Lambda function (`dispatch.py`) serves the following routes:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | Serves the SPA HTML page |
| GET | `/config` | No | Returns age public key and Cognito settings |
| POST | `/dispatch` | JWT | Validates connector, checks for duplicates, dispatches onboard workflow |
| POST | `/remove` | JWT | Validates connector exists, dispatches removal workflow |
| POST | `/cancel-pr` | JWT | Closes a pending PR and deletes its branch |
| POST | `/chat` | JWT | AI chat with Claude tool-use loop (up to 5 rounds) |
| GET | `/connectors` | No | Lists active, pending onboard, and pending removal connectors |
| GET | `/run-status` | No | Checks workflow run status and finds resulting PR |

Configuration:
- Runtime: Python 3.12
- Handler: `dispatch.handler`
- Memory: 256 MB, Timeout: 30s
- Layer: PyJWT + cryptography (built via `build-layer.sh` for Linux x86_64)
- Invoked via Lambda Function URL

All responses include CORS headers for browser compatibility. The Lambda reads configuration from SSM Parameter Store and secrets from Secrets Manager, with in-memory caching (5-minute TTL) to minimize API calls.

### Duplicate Detection

Before dispatching an onboard workflow, the Lambda checks:
1. Whether a connector directory already exists on main (`GET /repos/.../contents/connectors/{name}`)
2. Whether any open PR has a branch matching `feat/onboard-{name}-*`

Before dispatching a removal workflow, the Lambda checks:
1. Whether the connector directory exists on main (404 = not found error)
2. Whether any open PR has a branch matching `feat/remove-{name}-*`

### Branch Naming

All branches include a UTC timestamp suffix to prevent conflicts:
- Onboarding: `feat/onboard-{connector_name}-YYYYMMDD-HHMMSS`
- Removal: `feat/remove-{connector_name}-YYYYMMDD-HHMMSS`

This allows a connector to be added, removed, and re-added without branch name collisions.

### Connector Name Extraction

The `/connectors` endpoint parses branch names to extract the connector name by stripping the prefix and timestamp suffix using a regex: `^(.+)-\d{8}-\d{6}$`. This supports connector names with hyphens (e.g., `data-lake-raw`).

## Frontend SPA

The SPA (`index.html`) is a vanilla JavaScript application with three tabs:

### Onboard Tab
- Connector type dropdown (S3, PostgreSQL, REST API, SFTP)
- Dynamic form fields based on type selection
- Hover tooltips (?) on every field with format guidance
- Client-side validation per field type:
  - S3: bucket name format (3-63 chars, lowercase), region dropdown
  - PostgreSQL/SFTP: port as number input (1-65535), hostname format
  - REST API: URL format validation, cron expression validation
- age encryption of the full payload in the browser
- Workflow status polling with PR link on completion
- Error display with run URL link for failed workflows

### Connectors Tab
- **Active Connectors**: Merged connectors with Remove buttons
- **Pending Removal**: Connectors with open removal PRs, shown with "pending removal" badge, Cancel button, and inline info box
- **Pending Connectors**: Open onboard PRs with type badge, Cancel button, and inline info box
- Each pending item shows PR link, requester, and a Copy to Clipboard button
- Deduplication: if a connector appears as both active and pending removal, only the removal entry is shown
- Auto-refresh: the list refreshes when a removal workflow completes

### Chat Tab
- Conversation UI with message bubbles (user in blue, assistant in gray)
- Voice input via microphone button (Chrome/Edge only, hidden elsewhere)
- Markdown rendering in assistant messages (links, bold)
- Incremental form pre-fill: each field gathered in chat updates the Onboard tab form
- Secure inline secret entry: password fields rendered in chat, encrypted client-side
- Progress tracking: "Waiting for workflow result..." with link to GitHub Actions run
- Result display: PR link and copy-to-clipboard on completion
- Session persistence: conversation history maintained in memory, auth token in sessionStorage
- Expired token handling: detects 401 errors and prompts re-login

## Cognito

- User pool with email-based sign-in
- PKCE-only client (no client secret, public client)
- Hosted UI domain for the login flow
- OAuth2 scopes: openid, email, profile
- Explicit auth flows: SRP and refresh token

## GitHub App Authentication

The Lambda authenticates to GitHub as a GitHub App, not using a personal access token:

1. Load private key from Secrets Manager (base64-encoded PEM)
2. Generate JWT signed with RS256 (`iss` = App ID, 10-minute expiry)
3. Exchange JWT for an installation access token
4. Cache the installation token for 55 minutes (tokens expire at 60 minutes)
5. Use the token for workflow dispatch, status queries, PR management, and content listing

## KMS / SOPS

A dedicated KMS key is provisioned for SOPS encryption:

- Used by the platform repo's GitHub Actions workflow to encrypt secrets at rest
- Lambda role has `kms:Encrypt` permission (for potential future use)
- GitHub Actions role needs both `kms:Encrypt` and `kms:Decrypt`
- Automatic key rotation is enabled

## Age Encryption

Secrets are encrypted client-side in the browser before they leave the user's machine:

1. Frontend fetches the age public key from `/config` (stored in SSM Parameter Store)
2. The `age-encryption` JavaScript library (loaded from esm.sh CDN) encrypts the full payload
3. Ciphertext is base64-encoded and sent as `encrypted_payload` in the dispatch request
4. The GitHub Actions workflow decrypts using the age secret key (stored as a GitHub Actions secret)

This ensures that the Lambda function and any network intermediary never see plaintext credentials.

## Security Considerations

### Encryption Layers

1. **Transit**: HTTPS for all browser-to-Lambda communication (Lambda Function URL)
2. **Application**: Age public-key encryption for connector secrets, applied in the browser
3. **At rest**: SOPS + KMS encryption for secrets stored in the platform repository
4. **AWS**: Secrets Manager encryption for the GitHub App private key and age secret key

### Least-Privilege IAM

The Lambda execution role is scoped to:
- `ssm:GetParameter` on `/{app_name}/*` parameters only
- `secretsmanager:GetSecretValue` on exactly two secrets (GitHub App key, age secret key)
- `kms:Encrypt` on the SOPS KMS key only
- CloudWatch Logs (via `AWSLambdaBasicExecutionRole`)

### Authentication Flow

The PKCE flow prevents authorization code interception attacks:
1. Browser generates a random 64-byte `code_verifier`
2. SHA-256 hash is sent as `code_challenge` to Cognito
3. After user authenticates, the authorization code is exchanged with the original `code_verifier`
4. Cognito verifies the challenge match before issuing tokens

### Secret Storage

| Secret | Storage Location |
|--------|-----------------|
| GitHub App private key | AWS Secrets Manager |
| Age secret key | AWS Secrets Manager + GitHub Actions secret |
| Age public key | SSM Parameter Store (not sensitive) |
| GitHub App ID | SSM Parameter Store (not sensitive) |
| Anthropic API key | AWS Secrets Manager |
| Cognito client ID | SSM Parameter Store (not sensitive) |

## Infrastructure

### Terraform State

Three independent Terraform configurations:

1. **terraform/bootstrap/**: S3 state bucket, DynamoDB lock table, OIDC provider, CI IAM roles
2. **terraform/app/**: AWS infrastructure (Lambda, Cognito, IAM, KMS, Secrets Manager, SSM)
3. **terraform/github/**: GitHub resources (Actions secrets, branch protection)

This separation allows each layer to be updated independently and avoids needing all credentials in the same Terraform run.

### CI/CD

Both repos use GitHub Actions with OIDC authentication (no long-lived AWS keys):

- **Pull requests**: `terraform-plan.yml` runs plan on changed directories and comments the result on the PR
- **Merge to main**: `terraform-apply.yml` applies changes
- **Path filtering**: Only runs for directories with changes (`terraform/app/` or `terraform/github/`)
- **Lambda layer**: CI builds the layer from source before plan/apply

All actions use Node.js 24 compatible versions.
