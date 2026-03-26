# Provision Demo

A self-service connector onboarding application. Users authenticate via Cognito, fill out a form with connector configuration and credentials, which are encrypted client-side using age encryption, then dispatched as a GitHub Actions workflow to a platform repository. The workflow creates a pull request with the new connector configuration.

## Architecture Overview

The application is a single Lambda function behind an ALB, serving both the SPA frontend and API endpoints. Authentication is handled by Amazon Cognito with PKCE. Secrets are encrypted in the browser using age public-key encryption before being sent to the backend, which dispatches a GitHub Actions workflow via a GitHub App.

See [docs/architecture.md](docs/architecture.md) for detailed architecture documentation.

## Prerequisites

- **AWS Account** with permissions to create Lambda, Cognito, KMS, Secrets Manager, SSM, Route53, and ALB resources
- **Existing ALB** with an HTTPS listener and a Route53 hosted zone
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

### 3. Configure Terraform variables

```
cd terraform/app
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

```
cd terraform/github
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### 4. Build the Lambda layer

```
cd terraform/app/lambda
./build-layer.sh
```

### 5. Deploy the app infrastructure

```
cd terraform/app
terraform init
terraform plan
terraform apply
```

### 6. Configure GitHub secrets

```
cd terraform/github
terraform init
terraform plan
terraform apply
```

## Directory Structure

```
provision-demo/
├── README.md
├── .gitignore
├── docs/
│   └── architecture.md
└── terraform/
    ├── github/              # GitHub repo secrets and app installation
    │   ├── providers.tf
    │   ├── main.tf
    │   ├── variables.tf
    │   ├── outputs.tf
    │   └── terraform.tfvars.example
    └── app/                 # AWS infrastructure (Lambda, Cognito, KMS, etc.)
        ├── providers.tf
        ├── main.tf
        ├── cognito.tf
        ├── iam.tf
        ├── secrets.tf
        ├── ssm.tf
        ├── kms.tf
        ├── route53.tf
        ├── variables.tf
        ├── outputs.tf
        ├── terraform.tfvars.example
        └── lambda/
            ├── dispatch.py      # Lambda handler with route dispatch
            ├── index.html       # SPA frontend
            └── build-layer.sh   # Builds PyJWT/cryptography Lambda layer
```

## How It Works

1. **User visits the app** at the configured domain (e.g., `provision.example.com`)
2. **Cognito authentication** via PKCE authorization code flow redirects the user to sign in
3. **User selects a connector type** (S3, PostgreSQL, REST API, or SFTP) and fills in the configuration and secret fields
4. **Client-side encryption**: the frontend encrypts the full payload (config + secrets) using the age public key fetched from `/config`
5. **Dispatch**: the encrypted payload, connector name, and type are POSTed to `/dispatch`, which validates the Cognito JWT and triggers a `workflow_dispatch` event on the platform repository
6. **GitHub Actions workflow** in the platform repo decrypts the payload, generates connector configuration files, and opens a pull request
7. **Status polling**: the frontend polls `/run-status` every 5 seconds to track the workflow and display the resulting PR link

## Connector Types

| Type | Config Fields | Secret Fields |
|------|--------------|---------------|
| `s3` | `bucket_name`, `region` | (none) |
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
