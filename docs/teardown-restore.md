# Teardown and Restore

How to take the running demo down and bring it back. Only the `terraform/app`
stack is torn down. Bootstrap (state bucket, lock table, OIDC provider, CI IAM
roles), the `terraform/github` stack, both repos, and all GitHub Actions secrets
are left in place — they are what makes the restore a single workflow run.

## What survives a teardown

| Thing | Where it lives | Why it matters |
|---|---|---|
| Terraform remote state | `s3://provision-demo-tfstate` (bootstrap) | Restore re-applies into the same state key |
| CI OIDC role | `provision-demo-ci` (bootstrap) | Restore authenticates with it |
| `terraform/app/secrets.enc.json` | Committed, SOPS-encrypted, in this repo | Source of the secret values CI populates into Secrets Manager after apply |
| **SOPS KMS key** | `alias/provision-demo-sops` — belongs in `terraform/bootstrap`, not yet applied there | Decrypts committed connector secrets (see below) |
| Platform repo connectors | `duality72/provision-demo-platform` `main` | Unaffected; its Terraform makes no AWS resources |
| Lambda log group | `/aws/lambda/provision-demo` | Not Terraform-managed; Lambda reuses it on restore |

### Why the KMS key is not destroyed

The committed `connectors/*/secrets.enc.json` files in the platform repo are
SOPS-encrypted directly against this key:

```
arn:aws:kms:us-east-1:762260382631:key/f18b83eb-42d4-4630-9674-50b3c2ea13a9
```

Destroying it schedules deletion with a 14-day window; after that the key
material is unrecoverable and all five encrypted connector files become
permanently undecryptable. A restore would create a *new* key with a *new* ARN,
which cannot decrypt them. `terraform/app` no longer declares the key at all —
it belongs in `terraform/bootstrap`, which will manage it once that relocation
is applied — so tearing down `terraform/app` cannot reach it either way.

Retaining the key costs about $1/month. That is the whole reason the teardown
saves ~$1.20/month rather than ~$2.20/month.

## Snapshot at teardown

Captured 2026-09-04. Account `762260382631`, region `us-east-1`.

| Identifier | Value | Stable across restore? |
|---|---|---|
| Function URL | `https://io7tkyl2yawk3ut2wfklgibpgi0rzwlu.lambda-url.us-east-1.on.aws/` | **No** — new URL |
| Cognito user pool | `us-east-1_o9swB8ulm` | **No** — new pool |
| Cognito client ID | `60dbkp0dnqo1ofqmugebg8at73` | **No** — new client |
| Cognito hosted UI domain | `provision-demo.auth.us-east-1.amazoncognito.com` | Yes — prefix is reusable |
| KMS key ID | `f18b83eb-42d4-4630-9674-50b3c2ea13a9` | Yes — retained |
| KMS alias | `alias/provision-demo-sops` | Yes — retained |
| age public key | `age1julah9rcl5zdy9xcfuscsgp6vkdngm6nmu3a9a6zcefd9yjftu8s9mmk78` | Yes — from Actions secret |
| GitHub App ID | `3196055` | Yes |
| GitHub App installation ID | `119274471` | Yes |
| Platform repo | `duality72/provision-demo-platform` | Yes |

Cognito users at teardown (these are **lost** and must be recreated):

| Email | Status |
|---|---|
| `demo@dctank.com` | CONFIRMED |
| `test@dctank.com` | UNCONFIRMED |

Connectors on the platform repo's `main` at teardown: `analytics-warehouse`,
`billing-db`, `customer-data-lake`, `salesforce-sync`, `vendor-invoices`,
`vendor-reports`.

## Teardown

Run the **Terraform Destroy (app)** workflow twice, in order:

```
gh workflow run terraform-destroy.yml --repo duality72/provision-demo -f mode=plan
gh workflow run terraform-destroy.yml --repo duality72/provision-demo -f mode=destroy -f confirm=provision-demo
```

1. `plan` runs `terraform plan -destroy` so the destroy list can be reviewed.
   Read it before step 2.
2. `destroy` tears the stack down. The `confirm` input must be exactly
   `provision-demo` or the run fails before touching anything.

After teardown the app URL returns `404`/`403` — the Lambda and its Function URL
are gone.

## Restore

**Apply `terraform/bootstrap` before starting step 1.** The `Populate secrets`
step in `terraform-apply.yml` runs `sops -d` against the committed
`terraform/app/secrets.enc.json`, which needs `kms:Decrypt` on the SOPS KMS
key. `provision-demo-ci` only gets that permission once the bootstrap work
that moves the key into `terraform/bootstrap` has been applied. Skip it and
`terraform apply` still succeeds, but `Populate secrets` then fails with an
AccessDenied from KMS — leaving the stack up with two empty secret containers
and a non-functional Lambda.

### 1. Re-apply the app stack

Push any change under `terraform/app/**` to `main`, or re-run the Terraform Apply
workflow. It rebuilds the Lambda, layer, Function URL, Cognito pool/client/domain,
the two Secrets Manager secrets, the six SSM parameters, and the IAM role.
Everything except the Secrets Manager values comes from Actions secrets; those
two secrets are instead populated by the `Populate secrets` step, which
decrypts the committed `terraform/app/secrets.enc.json` with `sops` and writes
the plaintext straight into Secrets Manager.

```
gh run watch <id> --repo duality72/provision-demo
```

### 2. Recreate the demo user

The Cognito pool is new, so it has no users. Using the new pool ID from
`terraform output cognito_user_pool_id`:

```
aws cognito-idp admin-create-user \
  --user-pool-id <new-pool-id> \
  --username demo@dctank.com \
  --user-attributes Name=email,Value=demo@dctank.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region us-east-1

aws cognito-idp admin-set-user-password \
  --user-pool-id <new-pool-id> \
  --username demo@dctank.com \
  --password '<choose-one>' \
  --permanent \
  --region us-east-1
```

The pool requires at least 8 characters with an uppercase, a lowercase, and a
digit.

### 3. Pick up the new app URL

The Function URL changes. Terraform writes the new one to
`/provision-demo/app-url` and wires it into the Cognito client's callback and
logout URLs automatically, so nothing else needs editing:

```
aws ssm get-parameter --name /provision-demo/app-url \
  --region us-east-1 --query Parameter.Value --output text
```

### 4. Verify

Sign in at the new URL as `demo@dctank.com` and check all three tabs (Onboard,
Connectors, Chat). The Connectors tab should list the six connectors from the
platform repo's `main`. Onboard one connector end to end to confirm the
age-encrypt → dispatch → SOPS-encrypt → PR path still works against the retained
KMS key.

## Notes

- `terraform/github` is untouched by the teardown, so `SOPS_KMS_ARN` on the
  platform repo stays valid — the key ARN does not change.
- The two Secrets Manager secrets use `recovery_window_in_days = 0`, so they
  are deleted immediately rather than held for 30 days. Without this a restore
  inside the recovery window fails with "a secret with this name is already
  scheduled for deletion". Their values are repopulated by CI from
  `terraform/app/secrets.enc.json`, not from Actions secrets.
- Nothing in `dispatch.py` calls KMS; the Lambda's `kms:Encrypt` grant is unused.
  SOPS encryption happens in the platform repo's workflow, not in the Lambda.
- The KMS key and its `prevent_destroy` lifecycle belong in
  `terraform/bootstrap`, once that stack picks up the key (see the Restore
  prerequisite above). Either way, app-stack teardown and restore never touch
  that resource, so there is no import or state cleanup step here.
