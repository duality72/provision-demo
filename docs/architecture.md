# Architecture

## System Overview

Provision Demo is a two-repo system for self-service connector onboarding. A web application collects connector configuration, encrypts secrets client-side, and triggers an automated pipeline that generates infrastructure-as-code and opens a pull request.

### Components

- **provision-demo** (this repo): Lambda application, frontend SPA, and all Terraform infrastructure definitions
- **provision-demo-platform**: Target repo that receives workflow dispatches and PRs with connector definitions

## Data Flow

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
│  Lambda   │ ──── JWT ─────> │ GitHub API   │
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

## Component Details

### Lambda Function

The Lambda function (`dispatch.py`) serves four routes:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves the SPA HTML page |
| GET | `/config` | Returns age public key and Cognito settings |
| POST | `/dispatch` | Validates JWT, dispatches GitHub Actions workflow |
| GET | `/run-status` | Checks workflow run status and finds resulting PR |

Configuration:
- Runtime: Python 3.12
- Handler: `dispatch.handler`
- Memory: 256 MB, Timeout: 30s
- Layer: PyJWT + cryptography (built via `build-layer.sh`)
- Invoked by ALB with host-header routing

All responses include CORS headers for browser compatibility. The Lambda reads configuration from SSM Parameter Store and secrets from Secrets Manager, with in-memory caching (5-minute TTL) to minimize API calls.

### Cognito

- User pool with email-based sign-in
- PKCE-only client (no client secret, public client)
- Hosted UI domain for the login flow
- OAuth2 scopes: openid, email, profile
- Explicit auth flows: SRP and refresh token

### GitHub App Authentication

The Lambda authenticates to GitHub as a GitHub App, not using a personal access token:

1. Load private key from Secrets Manager (base64-encoded PEM)
2. Generate JWT signed with RS256 (`iss` = App ID, 10-minute expiry)
3. Exchange JWT for an installation access token
4. Cache the installation token for 55 minutes (tokens expire at 60 minutes)
5. Use the token for workflow dispatch and status queries

### KMS / SOPS

A dedicated KMS key is provisioned for SOPS encryption:

- Used by the platform repo's GitHub Actions workflow to encrypt secrets at rest
- Lambda role has `kms:Encrypt` permission (for potential future use)
- GitHub Actions role needs both `kms:Encrypt` and `kms:Decrypt`
- Automatic key rotation is enabled

### Age Encryption

Secrets are encrypted client-side in the browser before they leave the user's machine:

1. Frontend fetches the age public key from `/config` (stored in SSM Parameter Store)
2. The `age-encryption` JavaScript library encrypts the full payload
3. Ciphertext is base64-encoded and sent as `encrypted_payload` in the dispatch request
4. The GitHub Actions workflow decrypts using the age secret key (stored as a GitHub Actions secret)

This ensures that the Lambda function and any network intermediary never see plaintext credentials.

## Security Considerations

### Encryption Layers

1. **Transit**: HTTPS (ALB TLS termination) for all browser-to-Lambda communication
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
| Cognito client ID | Lambda environment variable (not sensitive) |

## Infrastructure Dependencies

The following resources must exist before deploying:

1. **VPC** with subnets for the ALB
2. **Application Load Balancer** with an HTTPS listener (TLS certificate)
3. **Route53 hosted zone** for the application domain
4. **GitHub App** created and installed on the target organization
5. **Platform repository** (`provision-demo-platform`) with the `onboard-connector.yml` workflow

### Terraform State

Two independent Terraform state files:

1. **terraform/app/**: AWS infrastructure (Lambda, Cognito, IAM, KMS, Secrets Manager, SSM, Route53, ALB listener rule)
2. **terraform/github/**: GitHub resources (Actions secrets, App installation binding)

This separation allows GitHub configuration to be updated independently of the AWS infrastructure, and avoids needing both AWS and GitHub credentials in the same Terraform run.
