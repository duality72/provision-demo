# Provision Demo

A self-service connector onboarding application with an AI chat agent. Users can onboard connectors through a traditional form or via natural language chat powered by Claude. The AI agent gathers configuration through conversation, pre-fills the form in real-time, and handles secrets securely with client-side encryption. Authentication is via Cognito, and all changes are PR-based through GitHub Actions.

## Architecture Overview

The application is a single Lambda function behind a Function URL, serving both the SPA frontend and API endpoints. Authentication is handled by Amazon Cognito with PKCE. Secrets are encrypted in the browser using age public-key encryption before being sent to the backend, which dispatches a GitHub Actions workflow via a GitHub App. The chat feature uses Claude's tool-use API to orchestrate connector operations.

See [docs/architecture.md](docs/architecture.md) for detailed architecture documentation.

## Features

- **Chat tab**: AI agent powered by Claude that manages connectors via natural language — gathers config one field at a time, pre-fills the form in real-time, and presents secure inline forms for credential entry with client-side encryption. Includes voice input (Chrome/Edge)
- **Onboard tab**: Submit connector onboarding requests with type-specific forms, field validation, and hover tooltips
- **Connectors tab**: View active connectors, pending onboarding requests, and pending removal requests
- **Connector removal**: Remove active connectors via a PR-based review flow
- **Cancel**: Cancel pending onboarding or removal requests (closes the PR and deletes the branch)
- **Duplicate detection**: Prevents submitting a connector with the same name as an existing or pending one
- **Unique branch names**: Timestamped branch names (`feat/onboard-{name}-YYYYMMDD-HHMMSS`) prevent conflicts on add/remove/re-add cycles
- **Inline info**: Each pending connector shows PR details and a copy-to-clipboard button
- **Field validation**: Type-specific validation (S3 bucket names, AWS region dropdown, port ranges, URL format, cron expressions)
- **Auto-refresh**: The connectors list auto-refreshes when a removal workflow completes

## Prerequisites

- **AWS Account** with permissions to create Lambda, Cognito, KMS, Secrets Manager, SSM, and IAM resources
- **GitHub App** created and installed on the target organization, with permissions for Actions (write), Contents (write), and Pull Requests (write)
- **age keypair** generated with `age-keygen` for client-side encryption
- **Terraform** >= 1.5.0
- **Python 3.12** (for building the Lambda layer)

## Setup

### 1. Generate an age keypair

```
age-keygen -o age-key.txt
```

Note the public key (starts with `age1...`) and the secret key (starts with `AGE-SECRET-KEY-1...`).

### 2. Create and install a GitHub App

Create a GitHub App in your organization with the following permissions:
- **Repository permissions**: Actions (Read & Write), Contents (Read & Write), Pull requests (Read & Write)
- Subscribe to no events (the app is used for API access only)

Install the app on the platform repository and note the App ID and Installation ID. Generate a private key and base64-encode it:

```
base64 -i private-key.pem
```

### 3. Bootstrap (state bucket, OIDC, CI roles)

```
cd terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init
terraform plan
terraform apply
```

### 4. Deploy the app infrastructure

```
cd terraform/app
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
./lambda/build-layer.sh
terraform init
terraform plan
terraform apply
```

### 5. Configure GitHub secrets

```
cd terraform/github
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
terraform init
terraform plan
terraform apply
```

## Directory Structure

```
provision-demo/
├── README.md
├── docs/
│   └── architecture.md
├── .github/
│   └── workflows/
│       ├── terraform-plan.yml    # PR: runs terraform plan on app + github
│       └── terraform-apply.yml   # Merge to main: applies terraform changes
└── terraform/
    ├── bootstrap/                # S3 state bucket, OIDC, CI IAM roles
    │   ├── main.tf
    │   └── variables.tf
    ├── github/                   # GitHub repo secrets and branch protection
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── terraform.tfvars.example
    └── app/                      # AWS infrastructure (Lambda, Cognito, KMS, etc.)
        ├── main.tf
        ├── cognito.tf
        ├── iam.tf
        ├── secrets.tf
        ├── ssm.tf
        ├── kms.tf
        ├── variables.tf
        ├── outputs.tf
        ├── terraform.tfvars.example
        └── lambda/
            ├── dispatch.py       # Lambda handler with route dispatch
            ├── index.html        # SPA frontend
            └── build-layer.sh    # Builds PyJWT/cryptography Lambda layer
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | Serves the SPA HTML page |
| GET | `/config` | No | Returns age public key and Cognito settings |
| POST | `/dispatch` | JWT | Validates connector, dispatches onboard workflow |
| POST | `/remove` | JWT | Dispatches connector removal workflow |
| POST | `/cancel-pr` | JWT | Closes a pending onboard/removal PR and deletes its branch |
| POST | `/chat` | JWT | AI chat with Claude tool-use loop for natural language connector management |
| GET | `/connectors` | JWT | Lists active, pending, and removing connectors |
| GET | `/run-status` | No | Checks workflow run status and finds resulting PR |

## How It Works

### Onboarding a Connector

1. User visits the app URL and authenticates via Cognito (PKCE flow)
2. User selects a connector type (S3, PostgreSQL, REST API, or SFTP) and fills in the form
3. Client-side validation checks field formats (bucket names, ports, URLs, cron expressions)
4. The frontend encrypts the full payload using the age public key fetched from `/config`
5. The encrypted payload is POSTed to `/dispatch`, which validates the Cognito JWT
6. Lambda checks for duplicate connector names (existing on main or pending PR) and rejects if found
7. Lambda generates a unique branch name with timestamp suffix and dispatches a `workflow_dispatch` event
8. The platform repo's GitHub Actions workflow decrypts the payload, generates connector files, and opens a PR
9. The frontend polls `/run-status` every 5 seconds and displays the PR link when complete
10. The connector appears in the Connectors tab as "Pending onboarding" until the PR is merged

### Removing a Connector

1. User clicks "Remove" on an active connector in the Connectors tab
2. Lambda verifies the connector exists and no removal PR is already open
3. Lambda dispatches a removal workflow with a timestamped branch name
4. The platform repo workflow deletes the connector directory and opens a PR
5. The connectors list auto-refreshes when the workflow completes
6. The connector shows as "pending removal" with an inline info box until the PR is merged

### Onboarding via Chat

1. User opens the Chat tab and describes what they want (e.g., "I need a PostgreSQL connector")
2. The AI agent asks for fields one at a time — connector type, name, then each config field
3. After each answer, the Onboard tab form is pre-filled in real-time (user can switch to check it)
4. The agent validates inputs (regions, ports, URLs) and asks again if invalid
5. Once all config is gathered, the agent shows a summary and asks for confirmation
6. For types with secrets: a secure inline form appears in the chat with password fields — secrets are encrypted client-side with age and sent directly to `/dispatch`, never passing through Claude
7. For types without secrets (S3): submission proceeds immediately after confirmation
8. The chat shows progress ("Waiting for workflow result...") with a link to the GitHub Actions run
9. On completion, a result bubble with the PR link and copy-to-clipboard appears in the chat
10. The Onboard tab shows identical progress/results — users can switch between tabs freely

### Cancelling a Request

1. User clicks "Cancel" on any pending onboarding or removal entry
2. Lambda closes the PR and deletes the branch via the GitHub API
3. The connectors list refreshes to reflect the change

## Connector Types

| Type | Config Fields | Secret Fields |
|------|--------------|---------------|
| `s3` | `bucket_name`, `region` (dropdown) | (none) |
| `postgres` | `host`, `port`, `database` | `username`, `password` |
| `rest-api` | `base_url`, `polling_schedule` | `api_key` |
| `sftp` | `host`, `port` | `username`, `ssh_private_key` |

## Security Model

- **Client-side encryption**: All connector secrets are encrypted in the browser using age before transmission. The Lambda function never sees plaintext secrets.
- **PKCE authentication**: Cognito OAuth2 with PKCE ensures no client secret is needed and prevents authorization code interception.
- **GitHub App authentication**: The Lambda authenticates to GitHub using a short-lived JWT derived from the App's private key, then exchanges it for an installation token (cached for 55 minutes).
- **Least-privilege IAM**: The Lambda role can only read specific SSM parameters, specific Secrets Manager secrets, and encrypt with the SOPS KMS key.
- **KMS key rotation**: The SOPS KMS key has automatic annual rotation enabled.
- **No secrets in environment variables**: The GitHub App private key and age secret key are stored in Secrets Manager, not in Lambda environment variables.
- **Chat security**: The AI chat agent never sees secret values. When secrets are needed, a secure inline form appears in the chat — secrets are encrypted client-side with age in the browser and sent directly to the dispatch endpoint, bypassing Claude entirely. Non-secret config fields (hosts, ports, regions) do pass through the Claude API.

## CI/CD

Both repos use GitHub Actions with OIDC authentication (no long-lived AWS keys):

- **Pull requests**: `terraform-plan.yml` runs `terraform plan` on changed directories and comments the plan on the PR
- **Merge to main**: `terraform-apply.yml` applies changes to the affected Terraform directories
- **Path filtering**: Only runs plan/apply for directories with changes (`terraform/app/` or `terraform/github/`)
- **Lambda layer**: CI builds the Lambda layer from source before plan/apply to ensure the zip hash is correct

All GitHub Actions use Node.js 24 compatible versions (checkout v6, setup-python v6, configure-aws-credentials v6, setup-terraform v4, paths-filter v4).
