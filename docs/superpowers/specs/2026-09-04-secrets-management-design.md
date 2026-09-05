# Secrets Management Redesign

Status: proposed
Date: 2026-09-04

## Problem

Three findings, all verified against the live account and state objects.

**1. Terraform state stores every root secret in cleartext.** The pre-teardown
state version (`D5B_mjv.1BpuMSeNoe8sIVQ3TFY_Lz1V`, serial 59) holds
`secret_string` for all three secrets. The state bucket is versioned with no
lifecycle rule, so ~15 historical objects each carry a copy going back to March.
`terraform/github`'s **current** state also stores the age secret key as
`github_actions_secret.plaintext_value`.

All three were verified live: the age key derives to the expected public key, the
GitHub App key authenticates as app `3196055`, and the Anthropic key returns HTTP
200.

**2. Actions secrets are the only other copy, and they are write-only.** They
cannot be read back for review, diffed, or audited. Deleting one loses the value.

**3. The SOPS KMS key is in the wrong stack.** It lives in `terraform/app` but
encrypts the committed `connectors/*/secrets.enc.json` files in the platform
repo, so it must outlive that stack. The teardown works around this with a
`terraform state rm`, which is a workflow trick standing in for a structural fix.

## Goals

- A SOPS file, committed to this repo, is the single source of truth for root
  secrets — reviewable, diffable, with history.
- Terraform state contains zero secret material.
- The KMS key's lifecycle is independent of the app stack.
- The age keypair, the one credential that can be rotated without a vendor
  console, is rotated and the old value made dead.

## Non-goals

- Changing how connector secrets are encrypted in the platform repo. That flow
  (age in transit, SOPS/KMS at rest) is unchanged.
- Moving the Lambda off Secrets Manager at runtime.
- Encrypting Terraform state itself beyond the existing SSE-KMS on the bucket.

## Decision: the SOPS file is committed to this public repo

SOPS/KMS ciphertext in a public repository is the intended use of the tool.
Values are AES-256-GCM encrypted under a per-file data key that KMS wraps; KMS
key material cannot be exported. Possession of the ciphertext grants nothing
without an AWS principal holding `kms:Decrypt`.

The control that matters is therefore the key policy, not repository visibility.
Current policy:

| Sid | Principal | Action |
|---|---|---|
| `RootAccess` | `arn:aws:iam::762260382631:root` | `kms:*` |
| `LambdaEncrypt` | `AROA3C6SCMOT73PZMQFF3` (dangling) | `kms:Encrypt`, `kms:DescribeKey` |

`RootAccess` delegates to IAM, so the effective decryptor set is "any principal in
the account whose IAM policy allows `kms:Decrypt`" — today that is
`provision-demo-platform-ci` and account administrators. `provision-demo-ci` has
no `kms:Decrypt` at all. `AROA3C6SCMOT73PZMQFF3` is the destroyed Lambda role's
unique ID, left dangling by the teardown.

Fork PRs cannot reach either CI role: GitHub forces read-only permissions on
fork-triggered runs, so `id-token: write` is unavailable and the OIDC trust
policy is never satisfied.

## Secret inventory

Verified consumers, from the source rather than assumed:

| Value | Secret? | Actual consumer | Delivery after this change |
|---|---|---|---|
| GitHub App private key | yes | Lambda, `dispatch.py:162` | Secrets Manager, populated by CI |
| Anthropic API key | yes | Lambda, `dispatch.py:1055` | Secrets Manager, populated by CI |
| age **secret** key | yes | platform repo `onboard-connector.yml:84` only | Actions secret on platform repo, set by CI |
| age **public** key | no | Lambda `dispatch.py:836`, frontend `/config` | SSM, stays in Terraform |
| GitHub App ID / installation ID | no | Lambda | SSM, stays in Terraform |
| `SOPS_KMS_ARN`, `AWS_ROLE_ARN` | no | platform CI | Actions secrets, stay in Terraform |

**`provision-demo/age-secret-key` in Secrets Manager is dead weight.** The Lambda
only ever encrypts, with the age *public* key via `pyrage.encrypt`; it never
decrypts. Nothing has ever read that secret. It is dropped entirely.

## Design

### 1. KMS key moves to `terraform/bootstrap`, which moves to the S3 backend

Bootstrap's state is local only because it originally had to create the bucket
that would hold it. The bucket and lock table exist now, so bootstrap migrates to
`s3://provision-demo-tfstate/provision-demo/bootstrap/terraform.tfstate` first,
and the KMS key is imported into shared, versioned, encrypted state rather than
onto one machine. `prevent_destroy` goes on the bucket and lock table so a
destroy cannot orphan the state they hold.

Applying bootstrap stays manual. A CI role able to apply it could rewrite its own
trust policy, so remote state without a CI job is the deliberate split.

The key already exists and is detached from all state, so this is an import, not
a create.

- Add `aws_kms_key.sops` + `aws_kms_alias.sops` to `terraform/bootstrap`.
- `terraform import` both into bootstrap's local state.
- Delete them from `terraform/app`, along with the Lambda's `kms:Encrypt` grant
  in `iam.tf` — verified unused, since `dispatch.py` never calls KMS.
- Bootstrap gains a `sops_kms_key_arn` output; `terraform/github` consumes it in
  place of the `sops_kms_arn` variable.

Consequences: the chicken-and-egg disappears (the key exists before any stack
needs to decrypt), and `terraform-destroy.yml` loses its `detach-kms` mode
entirely, collapsing to `plan` and `destroy`.

### 2. Tighten the key policy

Replace reliance on root delegation with explicit grants:

- `RootAccess` retained — removing it can orphan a key.
- Explicit `kms:Decrypt` + `kms:DescribeKey` for `provision-demo-ci` and
  `provision-demo-platform-ci`.
- Explicit `kms:Encrypt` for `provision-demo-platform-ci` (it writes the
  connector SOPS files).
- `LambdaEncrypt` deleted — the grant is unused and its principal is dangling.

`provision-demo-ci` also gains `kms:Decrypt` on this key in its IAM policy in
`bootstrap/main.tf`. This is a deliberate widening of that role, required so CI
can decrypt the SOPS file.

### 3. `terraform/app/secrets.enc.json`

SOPS-encrypted, committed. A `.sops.yaml` creation rule at the repo root binds
the path to the KMS key so `sops` selects it automatically:

```
creation_rules:
  - path_regex: terraform/app/secrets\.enc\.json$
    kms: arn:aws:kms:us-east-1:762260382631:key/f18b83eb-42d4-4630-9674-50b3c2ea13a9
```

Plaintext shape:

```
{
  "github_app_private_key_base64": "...",
  "anthropic_api_key": "...",
  "age_secret_key": "..."
}
```

### 4. Terraform owns containers, never values

In `terraform/app`:

- Keep `aws_secretsmanager_secret` for `github-app-private-key` and
  `anthropic-api-key`.
- Delete all three `aws_secretsmanager_secret_version` resources.
- Delete `aws_secretsmanager_secret.age_secret_key` and its Lambda IAM grant.
- Delete the `github_app_private_key_base64`, `age_secret_key`, and
  `anthropic_api_key` variable blocks.

In `terraform/github`:

- Delete `github_actions_secret.age_secret_key` — this is what puts the age key
  into that stack's live state.
- Delete the `age_secret_key` variable.

### 5. CI populates values after apply

A new step in `terraform-apply.yml`, after `apply-app`:

1. Install SOPS.
2. `sops -d terraform/app/secrets.enc.json` to a shell variable.
3. `aws secretsmanager put-secret-value` for the two Lambda secrets.
4. `gh secret set AGE_SECRET_KEY --repo duality72/provision-demo-platform` for
   the age key, authenticated with the existing `GH_PAT` Actions secret (already
   present for `terraform/github`).

Removed from CI: `TF_VAR_github_app_private_key_base64`, `TF_VAR_age_secret_key`,
`TF_VAR_anthropic_api_key` on the app job, and `TF_VAR_age_secret_key` on the
github job.

There is a window between `apply` and `populate` where the secret containers
exist with no version and the Lambda would fail on `get_secret`. This is
deploy-time only and self-heals on the next step. Accepted rather than
engineered around.

### 6. Rotation

Only the age keypair is rotated as part of this change. The GitHub App private
key and the Anthropic API key are **not** rotated: neither GitHub nor Anthropic
exposes an API for minting a new key, so both are console-only, browser-driven
operations that a human has to perform out of band. Rotating them was considered
and declined on the evidence set out below.

| Value | Rotated in this change? | How (if ever done) | Notes |
|---|---|---|---|
| GitHub App private key | No — console-only, no API | App settings → Private keys → Generate | Not needed; see the access analysis below |
| Anthropic API key | No — console-only, no API | console.anthropic.com → API keys | Not needed; see the access analysis below |
| age keypair | Yes | `age-keygen` locally | New public key must reach SSM (`age_public_key` var) and the platform repo simultaneously |

The age rotation has one ordering constraint: any onboarding payload encrypted
with the old public key becomes undecryptable once the platform repo has the new
secret key. With the app stack currently destroyed there are no in-flight
payloads, so this is a non-issue if done before restore.

Purging the noncurrent state versions (see the migration sequence) is therefore
the remediation for those two secrets rather than optional hygiene: the age
key's rotation makes its own historical plaintext dead, but does nothing for the
other two. A lifecycle rule expiring noncurrent versions automatically is
proposed as a follow-up, so the purge does not have to be repeated.

### Why the two unrotated keys do not need rotating

Rotating them by hand was considered and deliberately declined. Purging the
historical state versions closes the exposure on its own, because the set of
principals that could ever have read those versions is limited to account
administrators. Verified against the live account:

- **Neither CI role can read historical object versions.** `provision-demo-ci`
  and `provision-demo-platform-ci` are granted `s3:GetObject` and
  `s3:ListBucket` on the state bucket — not `s3:GetObjectVersion`, and not
  `s3:ListBucketVersions`. Reading a specific version requires the former and
  enumerating versions the latter, so both roles can reach only the *current*
  state object, which after this change holds no secret material.
- **Both repositories have a single collaborator**, `duality72`, with admin
  rights. There is no second party who could have assumed a role and read the
  bucket.
- **The bucket blocks public access on all four settings**
  (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`,
  `RestrictPublicBuckets`).

Rotation protects against a credential having already been read by someone who
should not have it. Here there is no such party: the only principals with
`s3:GetObjectVersion` in the account are its administrators. Deleting the
versions therefore removes the last copies rather than merely invalidating
exposed ones.

This reasoning is conditional on those facts. If the account gains
administrators who should not hold these credentials, if either repository gains
collaborators, or if a CI role is later granted `s3:GetObjectVersion`, the
conclusion no longer holds and both keys should be rotated through their
consoles.

One operational consequence of the same permission gap: the purge itself needs
`s3:DeleteObjectVersion`, which neither CI role has. It must run under an
administrator identity and cannot be moved into a workflow without granting that
permission first.

## Migration sequence

Order matters; the app stack is currently destroyed, which makes this safe.

1. Rotate the age keypair; hold the new plaintext, plus the current (unrotated)
   GitHub App private key and Anthropic API key, locally.
2. Migrate bootstrap to the S3 backend, then move the KMS key into it (import),
   tighten the key policy, and add `kms:Decrypt` to `provision-demo-ci`. Applied
   **locally** by an operator with IAM and KMS permissions — the one step the
   "deploy via CI, not locally" rule does not cover.
3. Create `.sops.yaml` and `terraform/app/secrets.enc.json` with the new values.
   Commit the ciphertext.
4. Strip secret variables and version resources from `terraform/app` and
   `terraform/github`. Add the populate step to `terraform-apply.yml`.
5. Simplify `terraform-destroy.yml` (drop `detach-kms`) and update
   `docs/teardown-restore.md`.
6. Restore the app stack per the runbook. CI populates the secrets.
7. Delete the now-unused Actions secrets: `APP_PRIVATE_KEY_BASE64`,
   `ANTHROPIC_API_KEY`, `AGE_SECRET_KEY` on this repo.

Step 7 as originally planned — "revoke the old GitHub App key and Anthropic
key" — is deliberately absent, because step 1 never rotated those two values.
The GitHub App private key and Anthropic API key in
`terraform/app/secrets.enc.json` today are the same values that were live
before this change, and they stay in use. Revoking either without first
generating and installing a replacement would break the Lambda. See "Why the
two unrotated keys do not need rotating" for why no replacement is required.

## Verification

- After apply, fetch the state object and assert no `secret_string` or
  `plaintext_value` field contains secret material. This is the acceptance test
  for the whole change.
- `aws secretsmanager get-secret-value` returns the new values for both Lambda
  secrets.
- Sign in to the restored app and onboard a connector end to end, confirming the
  age-encrypt → dispatch → decrypt → SOPS-encrypt → PR path works with the
  rotated age keypair and the retained KMS key.
- Confirm the five pre-existing `connectors/*/secrets.enc.json` files still
  decrypt, proving the KMS key survived the move to bootstrap.

## Risks

- **Bootstrap becomes self-referential.** Its state lives in the bucket it
  manages. This is accepted practice and only bites at destroy time, which
  `prevent_destroy` on the bucket and lock table guards against.
- **CI gains decrypt capability.** `provision-demo-ci` can decrypt anything
  encrypted with this key, including the connector secrets. Acceptable, and
  narrower than the platform CI role's existing `Resource = "*"`.
- **`sops` becomes a CI dependency.** Pinned by version, as the platform repo
  already does.
- **Bootstrap has no CI path.** It is applied by hand, deliberately: a role with
  permission to apply bootstrap could rewrite its own trust policy. Shared state
  means a second operator can now take over, which was the real risk.
