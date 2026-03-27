# Project Instructions

## Overview

This is the Provision Demo app — a self-service connector onboarding system. See README.md for architecture details and docs/architecture.md for the full technical spec.

Two repos work together:
- **provision-demo** (this repo): Lambda + SPA frontend + all Terraform
- **provision-demo-platform**: Target repo for connector PRs (duality72/provision-demo-platform)

## Development Workflow

### Deploy via CI, not locally

Never run `terraform apply` locally — the Lambda needs SSM params and Secrets Manager secrets that the local IAM user can't read. All deployments go through CI:

1. Push to main (admin bypass is fine for iteration)
2. Watch the Terraform Apply workflow: `gh run watch <id> --repo duality72/provision-demo`
3. Verify the deploy succeeded before testing

For larger features, create a PR with `feat/` prefix so CI runs `terraform plan` first.

### E2E test every change

After every deploy, run a Playwright E2E test against the live app:
- URL: the Lambda Function URL (check `terraform output` or SSM for the current URL)
- Login: Cognito hosted UI with the demo user
- Test the specific feature that changed AND verify nothing else broke

Always test both the Onboard tab and the Connectors tab after changes to shared code (dispatch.py, index.html).

### Test edge cases

Don't just test the happy path. For any new feature, test:
- Duplicate names (active connector, pending PR)
- Invalid input (each field validator)
- Cancellation mid-flow
- Tab switching during active workflows
- Page reload to verify state persists

### Leave test data realistic

After E2E testing, leave connectors in a variety of states — some active, some pending onboard, some pending removal. Don't clean everything up. This makes the demo look realistic.

## Code Conventions

### Lambda (dispatch.py)

- All routes go through `handler()` → `normalize_event()` → route-specific handler
- JWT-protected endpoints must validate the Authorization header and call `validate_cognito_token()`
- GitHub API calls go through `github_api()` which handles App authentication
- Use `get_ssm_param()` and `get_secret()` for AWS config (they cache automatically)
- New endpoints need: auth validation, input validation, error handling with `response_json()`, and a route entry in `handler()`

### SPA (index.html)

- Vanilla JS, no build step, single HTML file
- All functions exposed to onclick handlers must be assigned to `window.*`
- Dynamic form fields are rendered by `renderDynamicFields()` based on `CONNECTOR_REGISTRY`
- Every form field should have a tooltip (? icon) — add entries to `FIELD_VALIDATORS` (config fields) or `SECRET_TOOLTIPS` (secret fields)
- Field validation happens in `submitForm()` using `FIELD_VALIDATORS`
- New fields that are dropdowns or special types: set `type: "select"` or `type: "number"` in `FIELD_VALIDATORS`
- The age-encryption library is loaded from esm.sh (not jsDelivr) because jsDelivr doesn't bundle dependencies for browser ESM imports

### Connector types

Adding a new connector type requires changes in three places:
1. `CONNECTOR_REGISTRY` + `FIELD_VALIDATORS` + `SECRET_TOOLTIPS` in index.html
2. `CONNECTOR_TYPES` in dispatch.py
3. `CONNECTOR_REGISTRY` in generate_connector.py (platform repo)

### Branch naming

All workflow branches use timestamped names: `feat/onboard-{name}-YYYYMMDD-HHMMSS` or `feat/remove-{name}-YYYYMMDD-HHMMSS`. The Lambda generates these — never hardcode branch names without timestamps.

The `_extract_connector_name()` function in dispatch.py strips the timestamp suffix using a regex. If the branch format changes, update that regex.

## Platform Repo

The platform repo (provision-demo-platform) has two workflows:
- `onboard-connector.yml`: Decrypts payload, generates config files, opens PR
- `remove-connector.yml`: Deletes connector directory, opens PR

Changes to workflow inputs or branch naming must be coordinated across both repos. Deploy the platform repo changes first since the Lambda dispatches to these workflows.

## GitHub Actions

All actions should use Node.js 24 compatible versions:
- `actions/checkout@v6`
- `actions/setup-python@v6`
- `aws-actions/configure-aws-credentials@v6`
- `hashicorp/setup-terraform@v4`
- `dorny/paths-filter@v4`
- `marocchino/sticky-pull-request-comment@v3`

## Common Gotchas

- **Lambda layer**: Must be built for Linux x86_64 (`--platform manylinux2014_x86_64` in build-layer.sh), not macOS
- **Function URL permissions**: Needs both `lambda:InvokeFunctionUrl` AND `lambda:InvokeFunction`
- **SSM DescribeParameters**: Needs account-level resource ARN, not parameter-level
- **Cognito IAM**: Needs `Resource: "*"` for cognito-idp actions
- **age-encryption CDN**: Use esm.sh, not jsDelivr (bare specifier imports don't work in browsers)
- **Cron validation regex**: Must allow `*` combined with `/` (e.g., `*/15`)
- **GitHub API 503s**: Transient — the UI should handle errors gracefully and let the user retry
