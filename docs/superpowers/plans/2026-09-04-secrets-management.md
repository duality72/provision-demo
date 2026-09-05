# Secrets Management Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a committed SOPS file the single source of truth for the three root secrets, so that Terraform state contains no secret material and the historical plaintext is purged.

**Architecture:** The SOPS KMS key moves to `terraform/bootstrap`, where its lifecycle is independent of the app stack. `terraform/app` keeps the Secrets Manager *containers* but no longer manages their values; a CI step decrypts `terraform/app/secrets.enc.json` after apply and pushes values to Secrets Manager and to the platform repo's Actions secret. The age keypair is rotated (the only one automatable); the historical plaintext for the other two is purged from S3 rather than rotated dead.

**Tech Stack:** Terraform 1.5+, AWS (KMS, Secrets Manager, SSM, Lambda, Cognito), SOPS 3.8.1, age, GitHub Actions with OIDC.

**Spec:** `docs/superpowers/specs/2026-09-04-secrets-management-design.md`

## Global Constraints

- Branch names are prefixed `feat/`.
- Never `git commit -A`. Every commit lists explicit paths.
- No mention of Claude in commit messages or PR descriptions.
- Terraform is applied through CI, never locally — **except** `terraform/bootstrap`, which has no CI job by design (Tasks 2–3).
- Markdown fences use ``` with no language tag for Terraform/HCL.
- AWS account `762260382631`, region `us-east-1`.
- KMS key: `f18b83eb-42d4-4630-9674-50b3c2ea13a9`, alias `alias/provision-demo-sops`.
- GitHub Actions versions: `actions/checkout@v6`, `actions/setup-python@v6`, `aws-actions/configure-aws-credentials@v6`, `hashicorp/setup-terraform@v4`, `dorny/paths-filter@v4`.
- The app stack is currently **destroyed**. Tasks 2–8 are config-only; Task 9 restores it.
- PRs must pass checks and have Copilot findings addressed before merge.

---

### Task 1: Rotate the age keypair and assemble the secret values

Only the age keypair can be rotated without a vendor console. GitHub exposes no
API for minting App private keys, and Anthropic's Admin API can list and disable
keys but not create them. The existing GitHub App and Anthropic keys are
therefore carried forward unchanged — both were verified live — and their
historical exposure is remediated by the state-version purge in Task 10 instead.

**Files:** none — outputs are consumed by Task 5.

**Interfaces:**
- Produces: `NEW_AGE_PUBLIC` (begins `age1`) for `terraform/app/ci.tfvars`, and a
  plaintext secret bundle piped directly into `sops` in Task 5 with keys
  `github_app_private_key_base64`, `anthropic_api_key`, `age_secret_key`.

- [ ] **Step 1: Generate the new age keypair**

```bash
age-keygen -o /tmp/age-new.txt 2>/dev/null
chmod 600 /tmp/age-new.txt
grep -o 'age1[a-z0-9]*' /tmp/age-new.txt
```

Record the printed `age1...` value — it is `NEW_AGE_PUBLIC`, needed in Task 6.

- [ ] **Step 2: Verify the keypair is internally consistent**

```bash
age-keygen -y /tmp/age-new.txt
```

Expected: the same `age1...` value as Step 1. If they differ, regenerate.

- [ ] **Step 3: Confirm the carried-forward secrets are still live**

The GitHub App and Anthropic keys come from the last pre-teardown state version.
Confirm that version is still retrievable before depending on it:

```bash
aws s3api list-object-versions --bucket provision-demo-tfstate \
  --prefix provision-demo/app/terraform.tfstate \
  --query 'Versions[?Size>`40000`].VersionId' --output text | head -1
```

Expected: a version id. This is the source Task 5 reads.

- [ ] **Step 4: No commit**

This task produces no repository changes. `/tmp/age-new.txt` is consumed by
Task 5 and shredded there.

---

### Task 2: Migrate bootstrap to the S3 backend — OPERATOR APPLIES LOCALLY

Bootstrap's state is local purely for historical reasons: it created the bucket
and lock table, so on the very first run there was nowhere remote to put it. Both
now exist, which makes this a one-command migration. Doing it before the KMS key
import means the key lands in shared, versioned, encrypted state rather than on
one laptop.

The stack becomes self-referential — the bucket holds its own state. That is
accepted practice and only bites at destroy time, which is why `prevent_destroy`
goes on the bucket in Step 2.

**Files:**
- Modify: `terraform/bootstrap/providers.tf`
- Modify: `terraform/bootstrap/main.tf`

**Interfaces:**
- Consumes: nothing.
- Produces: bootstrap state at `s3://provision-demo-tfstate/provision-demo/bootstrap/terraform.tfstate`,
  matching the `provision-demo/<stack>/terraform.tfstate` convention the app and
  github stacks already use.

- [ ] **Step 1: Confirm local state is present and healthy before touching it**

```bash
cd terraform/bootstrap
terraform init
terraform state list
```

Expected: 10 resources, including `aws_s3_bucket.state`, `aws_dynamodb_table.lock`,
`aws_iam_openid_connect_provider.github`, and both `aws_iam_role` entries. If this
is empty, stop — there is nothing to migrate and the resources are unmanaged.

- [ ] **Step 2: Guard the state bucket and lock table against destroy**

Once the bucket holds its own state, destroying it would orphan everything. In
`terraform/bootstrap/main.tf`, add a lifecycle block to both resources:

```
resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}
```

```
resource "aws_dynamodb_table" "lock" {
  name         = var.state_lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  lifecycle {
    prevent_destroy = true
  }
}
```

- [ ] **Step 3: Back up the local state before migrating**

```bash
cp terraform.tfstate ~/bootstrap-tfstate-backup-$(date +%Y%m%d).json
ls -la ~/bootstrap-tfstate-backup-*.json
```

Keep this until Step 6 confirms the migration worked.

- [ ] **Step 4: Add the backend block**

In `terraform/bootstrap/providers.tf`, replace the comment
`# Bootstrap state is local — this creates the remote backend for everything else`
with:

```
  # State lives in the bucket this stack manages. Safe because the bucket and
  # lock table already exist; prevent_destroy on both stops a destroy from
  # orphaning this state.
  backend "s3" {
    bucket         = "provision-demo-tfstate"
    key            = "provision-demo/bootstrap/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "provision-demo-tflock"
    encrypt        = true
  }
```

- [ ] **Step 5: Migrate**

```bash
terraform init -migrate-state
```

Terraform prompts to copy the existing state to the new backend. Answer `yes`.

- [ ] **Step 6: Verify the state moved intact**

```bash
terraform state list | wc -l
aws s3api head-object --bucket provision-demo-tfstate \
  --key provision-demo/bootstrap/terraform.tfstate --query ContentLength
```

Expected: the same resource count as Step 1, and a non-zero object size. Now
confirm a plan is clean:

```bash
terraform plan -no-color | grep -E "Plan:|No changes"
```

Expected: `No changes.` — `prevent_destroy` is a lifecycle meta-argument and
never appears in a plan diff, so anything else is real drift worth reading.
Bootstrap has no committed tfvars by default; `ci.tfvars` supplies
`github_owner`, the only variable without a default.

- [ ] **Step 7: Remove the stale local state files**

```bash
rm -f terraform.tfstate terraform.tfstate.backup
```

They are gitignored, so this is not a commit. The backup from Step 3 remains.

- [ ] **Step 8: Commit**

```bash
git add terraform/bootstrap/providers.tf terraform/bootstrap/main.tf
git commit -m "Move bootstrap state to the S3 backend

Bootstrap kept local state only because it originally had to create the bucket
that holds it. The bucket and lock table exist now, so its state can live
alongside the other stacks: versioned, encrypted, and not dependent on one
machine. prevent_destroy on the bucket and lock table stops a destroy from
orphaning the state they now hold."
```

---

### Task 3: Move the KMS key into bootstrap — OPERATOR APPLIES LOCALLY

The key exists in AWS and is in no state file (the teardown detached it), so this
is an import. Bootstrap has no CI job — a role able to apply bootstrap could
rewrite its own trust policy — so this is applied by hand, but Task 2 has already
moved its state to S3.

**Files:**
- Modify: `terraform/bootstrap/main.tf`
- Modify: `terraform/bootstrap/outputs.tf`

**Interfaces:**
- Consumes: nothing.
- Produces: `aws_kms_key.sops` and `aws_kms_alias.sops` managed in bootstrap
  state; output `sops_kms_key_arn`; `provision-demo-ci` holding `kms:Decrypt`.

- [ ] **Step 1: Confirm the key is currently unmanaged and healthy**

```bash
aws kms describe-key --key-id alias/provision-demo-sops --region us-east-1 \
  --query 'KeyMetadata.{State:KeyState,Deletion:DeletionDate}'
```

Expected: `{"State": "Enabled", "Deletion": null}`.

- [ ] **Step 2: Add the key, alias, and decrypt grant to bootstrap**

Append to `terraform/bootstrap/main.tf`:

```
# ---------------------------------------------------------------------------
# SOPS KMS key
#
# Lives here, not in terraform/app, because it encrypts the committed
# connectors/*/secrets.enc.json files in the platform repo and must outlive any
# teardown of the app stack.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "sops" {
  description             = "KMS key for SOPS encryption in provision-demo"
  deletion_window_in_days = 14
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RootAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "CIDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = [
            aws_iam_role.ci_provision_demo.arn,
            aws_iam_role.ci_platform.arn
          ]
        }
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
      },
      {
        Sid    = "PlatformEncrypt"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.ci_platform.arn
        }
        Action   = "kms:Encrypt"
        Resource = "*"
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "sops" {
  name          = "alias/provision-demo-sops"
  target_key_id = aws_kms_key.sops.key_id
}
```

The old `LambdaEncrypt` statement is intentionally absent: its grant was never
used (`dispatch.py` never calls KMS) and its principal `AROA3C6SCMOT73PZMQFF3` is
the destroyed Lambda role, now dangling.

- [ ] **Step 3: Grant the demo CI role decrypt in its IAM policy**

In `terraform/bootstrap/main.tf`, inside `aws_iam_role_policy.ci_provision_demo`,
add a statement after the existing `KMSManagement` block:

```
      {
        Sid    = "KMSDecryptForSOPS"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = aws_kms_key.sops.arn
      },
```

- [ ] **Step 4: Add the output**

Append to `terraform/bootstrap/outputs.tf`:

```
output "sops_kms_key_arn" {
  description = "ARN of the SOPS KMS key"
  value       = aws_kms_key.sops.arn
}
```

This output is for operator reference only. `terraform/github` keeps its own
`sops_kms_arn` variable — bootstrap's state is local, so no other stack can read
it via `terraform_remote_state`.

- [ ] **Step 5: Import the existing key and alias**

```bash
cd terraform/bootstrap
terraform init
terraform import aws_kms_key.sops f18b83eb-42d4-4630-9674-50b3c2ea13a9
terraform import aws_kms_alias.sops alias/provision-demo-sops
```

Expected: two `Import successful!` messages.

- [ ] **Step 6: Plan and confirm it is an update, not a replacement**

```bash
terraform plan -no-color | grep -E "must be replaced|will be destroyed|Plan:"
```

Expected: `Plan: 0 to add, 2 to change, 0 to destroy.` — the key policy
(swapping `LambdaEncrypt` for `CIDecrypt`/`PlatformEncrypt`) and the
`provision-demo-ci` role policy (gaining `KMSDecryptForSOPS`).
**If anything says "must be replaced" or "will be destroyed", stop.** Replacing
this key destroys the ability to decrypt the committed connector secrets.

- [ ] **Step 7: Apply**

```bash
terraform apply
```

- [ ] **Step 8: Verify the policy took and the key still decrypts**

```bash
aws kms get-key-policy --key-id f18b83eb-42d4-4630-9674-50b3c2ea13a9 \
  --policy-name default --region us-east-1 --output json \
  | python3 -c "import json,sys; [print(s['Sid']) for s in json.loads(json.load(sys.stdin)['Policy'])['Statement']]"
```

Expected: `RootAccess`, `CIDecrypt`, `PlatformEncrypt`. No `LambdaEncrypt`.

- [ ] **Step 9: Commit**

```bash
git add terraform/bootstrap/main.tf terraform/bootstrap/outputs.tf
git commit -m "Move SOPS KMS key into bootstrap

The key encrypts committed connector secrets in the platform repo and must
outlive the app stack. Managing it here makes that structural instead of a
state rm in the destroy workflow. Grants the demo CI role decrypt so it can
read the SOPS file, and drops the unused LambdaEncrypt grant whose principal
was left dangling by the teardown."
```

---

### Task 4: Remove the KMS key from terraform/app

The app stack is destroyed, so its state is empty — this deletes configuration
only, with no state surgery.

**Files:**
- Delete: `terraform/app/kms.tf`
- Modify: `terraform/app/iam.tf`
- Modify: `terraform/app/outputs.tf`

**Interfaces:**
- Consumes: Task 3's bootstrap-managed key.
- Produces: an app stack with no KMS resources or grants.

- [ ] **Step 1: Confirm app state is empty**

```bash
aws s3api get-object --bucket provision-demo-tfstate \
  --key provision-demo/app/terraform.tfstate /tmp/appstate.json >/dev/null
python3 -c "import json; print('resources:', len(json.load(open('/tmp/appstate.json')).get('resources',[])))"
rm -f /tmp/appstate.json
```

Expected: `resources: 0`.

- [ ] **Step 2: Delete the KMS config**

```bash
git rm terraform/app/kms.tf
```

- [ ] **Step 3: Remove the unused KMS grant from the Lambda policy**

In `terraform/app/iam.tf`, delete this statement from
`aws_iam_role_policy.lambda_app` — verified unused, since `dispatch.py` never
calls KMS:

```
      {
        Effect = "Allow"
        Action = [
          "kms:Encrypt"
        ]
        Resource = aws_kms_key.sops.arn
      }
```

Remove the trailing comma from the statement now last in the list.

- [ ] **Step 4: Remove the KMS output**

In `terraform/app/outputs.tf`, delete:

```
output "kms_key_arn" {
  description = "KMS key ARN for SOPS encryption"
  value       = aws_kms_key.sops.arn
}
```

- [ ] **Step 5: Verify no dangling references**

```bash
grep -rn "aws_kms" terraform/app/ || echo "clean"
```

Expected: `clean`.

- [ ] **Step 6: Commit**

```bash
git add terraform/app/iam.tf terraform/app/outputs.tf
git commit -m "Remove KMS resources from the app stack

The key now lives in bootstrap. The Lambda's kms:Encrypt grant went with it —
dispatch.py never calls KMS, so the grant was dead."
```

---

### Task 5: Create the SOPS file

**Files:**
- Create: `.sops.yaml`
- Create: `terraform/app/secrets.enc.json`

**Interfaces:**
- Consumes: Task 1's `/tmp/age-new.txt`, plus the GitHub App and Anthropic keys
  read from the last pre-teardown state version.
- Produces: `terraform/app/secrets.enc.json` with top-level keys
  `github_app_private_key_base64`, `anthropic_api_key`, `age_secret_key` — the
  exact names Task 6's CI step reads with `jq`.

- [ ] **Step 1: Create the SOPS creation rule**

Create `.sops.yaml` at the repo root:

```yaml
creation_rules:
  - path_regex: terraform/app/secrets\.enc\.json$
    kms: arn:aws:kms:us-east-1:762260382631:key/f18b83eb-42d4-4630-9674-50b3c2ea13a9
```

- [ ] **Step 2: Assemble and encrypt in one pipeline**

The GitHub App and Anthropic keys are read from the last pre-teardown state
version; the age secret key comes from Task 1. Plaintext is never written to
disk — it goes straight into `sops` on stdin.

```bash
VER=$(aws s3api list-object-versions --bucket provision-demo-tfstate \
  --prefix provision-demo/app/terraform.tfstate \
  --query 'Versions[?Size>`40000`]|[0].VersionId' --output text)
export VER

# Streamed to stdout, never written to disk: the state object holds all three
# secrets in cleartext, so materialising it as a temp file is an avoidable
# exposure even if the file is shredded afterwards.
python3 - <<'INNER' | sops --encrypt --input-type json --output-type json \
      --filename-override terraform/app/secrets.enc.json /dev/stdin \
      > terraform/app/secrets.enc.json
import json, re, subprocess, os
raw = subprocess.run([
    "aws", "s3api", "get-object",
    "--bucket", "provision-demo-tfstate",
    "--key", "provision-demo/app/terraform.tfstate",
    "--version-id", os.environ["VER"], "/dev/stdout",
], capture_output=True, check=True).stdout
state = json.loads(raw[:raw.rfind(b"}")+1])
vals = {}
for r in state.get('resources', []):
    if r['type'] == 'aws_secretsmanager_secret_version':
        vals[r['name']] = r['instances'][0]['attributes']['secret_string']
age = re.search(r'AGE-SECRET-KEY-1[A-Z0-9]+', open('/tmp/age-new.txt').read()).group(0)
print(json.dumps({
    "github_app_private_key_base64": vals['github_app_key'],
    "anthropic_api_key": vals['anthropic_api_key'],
    "age_secret_key": age,
}))
INNER

shred -u /tmp/age-new.txt 2>/dev/null || rm -f /tmp/age-new.txt
```

- [ ] **Step 3: Verify it round-trips and holds no plaintext**

```bash
sops -d terraform/app/secrets.enc.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(sorted(d.keys())); print({k: len(v) for k,v in d.items()})"
```

Expected: keys `['age_secret_key', 'anthropic_api_key', 'github_app_private_key_base64']`
with lengths 74, 108, and 2240.

```bash
grep -cE "AGE-SECRET-KEY-1|sk-ant-|BEGIN RSA" terraform/app/secrets.enc.json \
  && echo "PLAINTEXT LEAK - do not commit" || echo "no plaintext markers: good"
```

Expected: `no plaintext markers: good`.

- [ ] **Step 4: Verify the encrypted App key still authenticates**

Proves the value survived the state round-trip intact:

```bash
sops -d terraform/app/secrets.enc.json | python3 -c "
import base64, json, sys, time, urllib.request, jwt
pem = base64.b64decode(json.load(sys.stdin)['github_app_private_key_base64']).decode()
now = int(time.time())
tok = jwt.encode({'iat': now-60, 'exp': now+300, 'iss': '3196055'}, pem, algorithm='RS256')
req = urllib.request.Request('https://api.github.com/app', headers={
    'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json',
    'User-Agent': 'sops-verify'})
print(json.load(urllib.request.urlopen(req, timeout=20))['slug'])
"
```

Expected: `provision-demo`.

- [ ] **Step 5: Commit**

```bash
git add .sops.yaml terraform/app/secrets.enc.json
git commit -m "Add SOPS-encrypted root secrets

Source of truth for the three root secrets, encrypted against the bootstrap
KMS key. The age key is newly generated; the GitHub App and Anthropic keys are
carried forward, since neither vendor allows minting a key through an API."
```

---

### Task 6: Stop Terraform managing secret values

**Files:**
- Modify: `terraform/app/secrets.tf`
- Modify: `terraform/app/variables.tf`
- Modify: `terraform/app/iam.tf`
- Modify: `terraform/app/ci.tfvars`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: an app stack whose only Secrets Manager resources are two empty
  containers named `provision-demo/github-app-private-key` and
  `provision-demo/anthropic-api-key`.

- [ ] **Step 1: Reduce secrets.tf to containers only**

Replace the entire contents of `terraform/app/secrets.tf` with:

```
# Containers only. Values are populated by the Populate secrets step in
# terraform-apply.yml, decrypted from terraform/app/secrets.enc.json, so no
# secret material enters Terraform state.
#
# recovery_window_in_days = 0 so a teardown can be reversed immediately rather
# than being blocked for 30 days. See docs/teardown-restore.md.

resource "aws_secretsmanager_secret" "github_app_key" {
  name        = "${var.app_name}/github-app-private-key"
  description = "GitHub App private key for ${var.app_name}"

  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.app_name}/anthropic-api-key"
  description = "Anthropic API key for Claude chat feature"

  recovery_window_in_days = 0
}
```

This drops all three `aws_secretsmanager_secret_version` resources and the
`age_secret_key` secret, which nothing has ever read.

- [ ] **Step 2: Delete the three sensitive variables**

In `terraform/app/variables.tf`, delete the `github_app_private_key_base64`,
`age_secret_key`, and `anthropic_api_key` variable blocks. Leave
`age_public_key`, `github_app_id`, `github_app_installation_id`,
`platform_repo_full_name`, `cognito_domain_prefix`, `aws_region`, `app_name`,
and `environment` untouched.

- [ ] **Step 3: Drop the age secret from the Lambda IAM policy**

In `terraform/app/iam.tf`, the `secretsmanager:GetSecretValue` resource list
becomes:

```
        Resource = [
          aws_secretsmanager_secret.github_app_key.arn,
          aws_secretsmanager_secret.anthropic_api_key.arn
        ]
```

- [ ] **Step 4: Put the new age public key in ci.tfvars**

`age_public_key` is not secret, so it lives in `ci.tfvars` rather than an Actions
secret. Append to `terraform/app/ci.tfvars`:

```
age_public_key          = "<NEW_AGE_PUBLIC>"
```

Use the `NEW_AGE_PUBLIC` value printed by Task 1 Step 1.

- [ ] **Step 5: Verify no secret variables remain**

```bash
grep -nE "age_secret_key|github_app_private_key_base64|anthropic_api_key" terraform/app/*.tf
```

Expected: only the two `aws_secretsmanager_secret.anthropic_api_key` resource
references in `secrets.tf` and `iam.tf`. No `variable` blocks, no
`secret_string`.

- [ ] **Step 6: Commit**

```bash
git add terraform/app/secrets.tf terraform/app/variables.tf terraform/app/iam.tf terraform/app/ci.tfvars
git commit -m "Stop Terraform managing secret values

Terraform kept secret_string in state, which is why the root secrets sat in
cleartext across every historical state object. It now manages only the
Secrets Manager containers; CI populates the values from the SOPS file.

Drops the age-secret-key secret entirely — the Lambda only ever encrypts with
the age public key via pyrage, so nothing ever read it."
```

---

### Task 7: Populate secrets from CI

**Files:**
- Modify: `.github/workflows/terraform-apply.yml`
- Modify: `terraform/github/main.tf`
- Modify: `terraform/github/variables.tf`

**Interfaces:**
- Consumes: `terraform/app/secrets.enc.json` key names from Task 5; the
  containers from Task 6.
- Produces: populated Secrets Manager values and the platform repo's
  `AGE_SECRET_KEY` Actions secret.

- [ ] **Step 1: Remove the secret TF_VARs from the app apply**

In `.github/workflows/terraform-apply.yml`, the `Terraform Apply` step of
`apply-app` keeps only the non-secret variables:

```yaml
        env:
          TF_VAR_github_app_id: ${{ secrets.APP_ID }}
          TF_VAR_github_app_installation_id: ${{ secrets.APP_INSTALLATION_ID }}
```

`TF_VAR_github_app_private_key_base64`, `TF_VAR_age_public_key`,
`TF_VAR_age_secret_key`, and `TF_VAR_anthropic_api_key` are all removed —
`age_public_key` now comes from `ci.tfvars`.

- [ ] **Step 2: Add the populate step**

Append to the `apply-app` job, after `Terraform Apply`:

```yaml
      - name: Install SOPS
        run: |
          SOPS_VERSION=3.11.0
          curl -sLO "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"
          chmod +x "sops-v${SOPS_VERSION}.linux.amd64"
          sudo mv "sops-v${SOPS_VERSION}.linux.amd64" /usr/local/bin/sops

      - name: Populate secrets
        env:
          GH_TOKEN: ${{ secrets.GH_PAT }}
        run: |
          set -euo pipefail
          # Decrypted plaintext stays in this step only; never written to disk
          # and never echoed. put-secret-value reads from stdin via file://-.
          plain=$(sops -d terraform/app/secrets.enc.json)

          for pair in \
            "github_app_private_key_base64:provision-demo/github-app-private-key" \
            "anthropic_api_key:provision-demo/anthropic-api-key"; do
            field="${pair%%:*}"; secret_id="${pair#*:}"
            printf '%s' "$plain" | jq -r ".${field}" \
              | aws secretsmanager put-secret-value \
                  --secret-id "$secret_id" \
                  --secret-string file:///dev/stdin \
                  --region us-east-1 >/dev/null
            echo "populated $secret_id"
          done

          printf '%s' "$plain" | jq -r '.age_secret_key' \
            | gh secret set AGE_SECRET_KEY --repo duality72/provision-demo-platform
          echo "set AGE_SECRET_KEY on the platform repo"
```

- [ ] **Step 3: Stop Terraform writing the age key to the platform repo**

In `terraform/github/main.tf`, delete the whole
`resource "github_actions_secret" "age_secret_key"` block. This is what put the
age key into that stack's live state as `plaintext_value`.

In `terraform/github/variables.tf`, delete the `age_secret_key` variable block.

- [ ] **Step 4: Remove its TF_VAR from the github apply**

In `.github/workflows/terraform-apply.yml`, delete this line from the
`apply-github` job's env:

```yaml
          TF_VAR_age_secret_key: ${{ secrets.AGE_SECRET_KEY }}
```

- [ ] **Step 5: Validate the workflow parses**

```bash
python3 -c "
import yaml
d = yaml.safe_load(open('.github/workflows/terraform-apply.yml'))
steps = [s.get('name') or s.get('uses') for s in d['jobs']['apply-app']['steps']]
print(steps)
assert 'Populate secrets' in steps, 'populate step missing'
print('workflow OK')
"
```

Expected: the step list ending with `Install SOPS`, `Populate secrets`, and
`workflow OK`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/terraform-apply.yml terraform/github/main.tf terraform/github/variables.tf
git commit -m "Populate secrets from the SOPS file in CI

Values are decrypted at deploy time and pushed to Secrets Manager and the
platform repo, so they never pass through Terraform. Also drops the age secret
from terraform/github, which was storing it in state as plaintext_value."
```

---

### Task 8: Simplify the destroy workflow and update the runbook

Moving the key to bootstrap makes the `detach-kms` mode obsolete — it existed
only because the key was in the wrong stack.

**Files:**
- Modify: `.github/workflows/terraform-destroy.yml`
- Modify: `docs/teardown-restore.md`

**Interfaces:**
- Consumes: Task 3's relocation.
- Produces: a two-mode destroy workflow (`plan`, `destroy`).

- [ ] **Step 1: Drop the detach-kms mode**

In `.github/workflows/terraform-destroy.yml`:
- Remove `detach-kms` from the `mode` input's `options` list, leaving
  `plan` and `destroy`.
- Delete the entire `Detach SOPS KMS key from state` step.
- Delete the `if: inputs.mode != 'detach-kms'` condition from the
  `Build Lambda layer` step, so it always runs.
- Update the header comment to describe two modes rather than three.

- [ ] **Step 2: Remove the secret TF_VARs from both destroy modes**

The `Terraform Plan (destroy)` and `Terraform Destroy` steps keep only:

```yaml
        env:
          TF_VAR_github_app_id: ${{ secrets.APP_ID }}
          TF_VAR_github_app_installation_id: ${{ secrets.APP_INSTALLATION_ID }}
```

- [ ] **Step 3: Rewrite the runbook's teardown and restore sections**

In `docs/teardown-restore.md`:
- Teardown becomes two steps, `plan` then `destroy`.
- Delete the "Why the KMS key is not destroyed" rationale about `state rm`, and
  replace it with: the key lives in `terraform/bootstrap` and is not part of the
  app stack at all.
- Delete restore step 1 (the `terraform import` of the key and alias) — no longer
  needed — and renumber the remaining steps.
- Delete the paragraph beginning "The order is enforced, not just recommended".
- In the "What survives" table, change the KMS row's location from
  "detached from state, left running" to "managed by `terraform/bootstrap`".
- Add a row for `terraform/app/secrets.enc.json` as the source of the secret
  values, replacing the "TF_VAR_* input values / Actions secrets" row.

- [ ] **Step 4: Verify no stale references remain**

```bash
grep -n "detach-kms\|terraform import aws_kms" .github/workflows/terraform-destroy.yml docs/teardown-restore.md || echo "clean"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/terraform-destroy.yml')); print('workflow OK')"
```

Expected: `clean` and `workflow OK`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/terraform-destroy.yml docs/teardown-restore.md
git commit -m "Drop detach-kms mode from the destroy workflow

The mode existed to protect a KMS key that should never have been in the app
stack. Now that it lives in bootstrap, a destroy cannot reach it and the
workflow collapses to plan and destroy."
```

---

### Task 9: Restore the app stack and prove state is clean

**Files:**
- Create: `scripts/verify-no-secrets-in-state.sh`

**Interfaces:**
- Consumes: everything above.
- Produces: a running app stack and a repeatable acceptance test.

- [ ] **Step 1: Write the acceptance test first**

Create `scripts/verify-no-secrets-in-state.sh`:

```bash
#!/usr/bin/env bash
# Asserts that no Terraform state object in the bucket contains secret material.
# This is the acceptance test for the secrets management redesign.
set -euo pipefail

BUCKET=provision-demo-tfstate
FAIL=0

for key in provision-demo/app/terraform.tfstate \
           provision-demo/github/terraform.tfstate \
           provision-demo/bootstrap/terraform.tfstate; do
  tmp=$(mktemp)
  if ! aws s3api get-object --bucket "$BUCKET" --key "$key" "$tmp" >/dev/null 2>&1; then
    echo "SKIP $key (absent)"; rm -f "$tmp"; continue
  fi
  findings=$(python3 - "$tmp" <<'PY'
import json, sys
state = json.load(open(sys.argv[1]))
markers = ("AGE-SECRET-KEY-1", "sk-ant-", "-----BEGIN")
hits = []
for r in state.get("resources", []):
    for i in r.get("instances", []):
        for field in ("secret_string", "plaintext_value"):
            v = i.get("attributes", {}).get(field)
            if not v:
                continue
            # base64 PEMs decode to -----BEGIN
            probe = v
            try:
                import base64
                probe = v + base64.b64decode(v + "==", validate=False).decode("utf-8", "ignore")
            except Exception:
                pass
            if any(m in probe for m in markers):
                hits.append(f"{r['type']}.{r['name']}.{field}")
print("\n".join(hits))
PY
)
  if [ -n "$findings" ]; then
    echo "FAIL $key:"; echo "$findings" | sed 's/^/    /'; FAIL=1
  else
    echo "PASS $key — no secret material"
  fi
  rm -f "$tmp"
done

exit $FAIL
```

```bash
chmod +x scripts/verify-no-secrets-in-state.sh
```

- [ ] **Step 2: Run it against current state to see it pass trivially**

```bash
./scripts/verify-no-secrets-in-state.sh
```

Expected: `SKIP` or `PASS` for app (destroyed), and **`FAIL`** for github — it
still holds the old age key until Task 7's change is applied. A failing github
line here proves the test actually detects secrets rather than passing vacuously.

- [ ] **Step 3: Open the PR**

```bash
git push -u origin feat/secrets-management
gh pr create --repo duality72/provision-demo --base main \
  --title "Move root secrets to SOPS, out of Terraform state" \
  --body "See docs/superpowers/specs/2026-09-04-secrets-management-design.md"
```

Include a reviewer guide in the PR body per the repo convention.

- [ ] **Step 4: Wait for checks and address Copilot**

```bash
gh pr checks <number> --repo duality72/provision-demo --watch
gh api repos/duality72/provision-demo/pulls/<number>/comments \
  --jq '.[] | "[\(.user.login)] \(.path):\(.line) — \(.body[0:300])"'
```

Resolve every thread before merging; branch protection requires it.

- [ ] **Step 5: Merge and watch the apply**

```bash
gh pr merge <number> --repo duality72/provision-demo --squash --delete-branch --admin
gh run watch <id> --repo duality72/provision-demo
```

Expected: `apply-app` succeeds, then `Populate secrets` prints
`populated provision-demo/github-app-private-key`,
`populated provision-demo/anthropic-api-key`, and
`set AGE_SECRET_KEY on the platform repo`.

- [ ] **Step 6: Run the acceptance test again**

```bash
./scripts/verify-no-secrets-in-state.sh
```

Expected: `PASS` for **both** state objects. This is the acceptance criterion for
the whole change.

- [ ] **Step 7: Confirm the secrets are readable and correct**

```bash
aws secretsmanager get-secret-value --secret-id provision-demo/anthropic-api-key \
  --region us-east-1 --query 'SecretString' --output text | head -c 8
```

Expected: `sk-ant-`.

- [ ] **Step 8: End-to-end test the restored app**

Get the new URL, then sign in and exercise the app:

```bash
aws ssm get-parameter --name /provision-demo/app-url --region us-east-1 \
  --query Parameter.Value --output text
```

Recreate the demo user first (the Cognito pool is new — see
`docs/teardown-restore.md`), then check all three tabs and onboard one connector
end to end. This proves the rotated age keypair and the relocated KMS key both
work: the payload is age-encrypted with the new public key, decrypted by the
platform workflow with the new secret key, and re-encrypted with SOPS against the
bootstrap-managed KMS key.

- [ ] **Step 9: Confirm the pre-existing connector secrets still decrypt**

```bash
gh repo clone duality72/provision-demo-platform /tmp/plat -- --depth 1
sops -d /tmp/plat/connectors/billing-db/secrets.enc.json | python3 -c "import json,sys; print(sorted(json.load(sys.stdin).keys()))"
rm -rf /tmp/plat
```

Expected: `['password', 'username']`. This proves the KMS key survived the move
to bootstrap with its material intact.

- [ ] **Step 10: Commit the verification script**

```bash
git add scripts/verify-no-secrets-in-state.sh
git commit -m "Add acceptance test for secret-free Terraform state"
```

---

### Task 10: Purge historical plaintext and clean up Actions secrets

Rotation of the GitHub App and Anthropic keys was scoped out, so the plaintext in
historical state objects is remediated by deleting those objects instead. Run
this only after Task 9 proves the new design writes no secrets to state —
otherwise the purge just makes room for fresh copies.

**Files:**
- Create: `scripts/purge-state-history.sh`

**Interfaces:**
- Consumes: a verified-clean current state from Task 9.

- [ ] **Step 1: Confirm current state is clean before purging history**

```bash
./scripts/verify-no-secrets-in-state.sh
```

Expected: `PASS` for all three state objects. **If any line says FAIL, stop** —
purging history while the current state still holds secrets accomplishes nothing.

- [ ] **Step 2: Write the purge script**

Create `scripts/purge-state-history.sh`:

```bash
#!/usr/bin/env bash
# Deletes noncurrent versions of the Terraform state objects. Those versions
# hold the pre-redesign plaintext secrets. The current version is never touched.
set -euo pipefail

BUCKET=provision-demo-tfstate
DRY_RUN=${DRY_RUN:-1}

for key in provision-demo/app/terraform.tfstate \
           provision-demo/github/terraform.tfstate; do
  echo "== $key"
  aws s3api list-object-versions --bucket "$BUCKET" --prefix "$key" \
    --query 'Versions[?IsLatest==`false`].[VersionId]' --output text \
  | while read -r vid; do
      [ -z "$vid" ] && continue
      if [ "$DRY_RUN" = "1" ]; then
        echo "   would delete $vid"
      else
        aws s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$vid" >/dev/null
        echo "   deleted $vid"
      fi
    done
done

[ "$DRY_RUN" = "1" ] && echo "DRY RUN — re-run with DRY_RUN=0 to delete"
exit 0
```

```bash
chmod +x scripts/purge-state-history.sh
```

- [ ] **Step 3: Dry-run it**

```bash
./scripts/purge-state-history.sh
```

Expected: a list of `would delete <version>` lines, roughly 15 for the app state.
The latest version of each object must **not** appear.

- [ ] **Step 4: Execute the purge**

```bash
DRY_RUN=0 ./scripts/purge-state-history.sh
```

- [ ] **Step 5: Verify the plaintext is gone**

```bash
aws s3api list-object-versions --bucket provision-demo-tfstate \
  --prefix provision-demo/app/terraform.tfstate \
  --query 'length(Versions)'
./scripts/verify-no-secrets-in-state.sh
```

Expected: `1` (current version only), and `PASS` on every state object.

- [ ] **Step 6: Delete the now-unused Actions secrets**

```bash
gh secret delete APP_PRIVATE_KEY_BASE64 --repo duality72/provision-demo
gh secret delete ANTHROPIC_API_KEY --repo duality72/provision-demo
gh secret delete AGE_SECRET_KEY --repo duality72/provision-demo
gh secret delete AGE_PUBLIC_KEY --repo duality72/provision-demo
```

`AGE_SECRET_KEY` on the **platform** repo stays — CI sets it each deploy.
`APP_ID`, `APP_INSTALLATION_ID`, `AWS_ROLE_ARN`, `GH_PAT`, `SOPS_KMS_ARN`,
`PLATFORM_AWS_ROLE_ARN`, and `CLAUDE_CODE_OAUTH_TOKEN` all stay.

- [ ] **Step 7: Commit**

```bash
git add scripts/purge-state-history.sh
git commit -m "Add state-history purge script

Deletes noncurrent state versions holding pre-redesign plaintext secrets.
Substitutes for rotating the GitHub App and Anthropic keys, neither of which
can be minted through an API."
```

---

## Follow-ups (not in scope)

- **Rotating the GitHub App private key and the Anthropic API key by hand was
  considered and deliberately declined.** Both need a vendor console, and the
  access analysis in the spec shows the purge in Task 10 fully closes the
  exposure on its own. See "Why the two unrotated keys do not need rotating"
  in the spec for the evidence.
- **Task 10's purge must run under an admin identity, not in CI.** Deleting a
  specific object version needs `s3:DeleteObjectVersion`, which neither CI role
  has. If the purge is ever automated into a workflow, that permission has to be
  granted first — and granting it would also widen what CI can reach.
- Add an S3 lifecycle rule expiring noncurrent state versions automatically, so
  the purge does not have to be repeated.
- Consider a CI job for bootstrap. Deliberately omitted: a role able to apply
  bootstrap could rewrite its own trust policy, so manual apply is the safer
  default even now that state is shared.
