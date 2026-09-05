#!/usr/bin/env bash
# Deletes noncurrent versions of the Terraform state objects.
#
# Those versions hold the pre-redesign plaintext secrets: before Terraform
# stopped managing secret values, every apply wrote the GitHub App private key,
# the Anthropic API key and the age secret key into state in cleartext.
#
# Rotating the GitHub App and Anthropic keys was considered and declined — see
# "Why the two unrotated keys do not need rotating" in
# docs/superpowers/specs/2026-09-04-secrets-management-design.md — so deleting
# these versions is the remediation, not merely hygiene.
#
# The CURRENT version of each object is never touched.
#
# Requires s3:DeleteObjectVersion, which neither CI role has: run this under an
# administrator identity. Dry run by default; set DRY_RUN=0 to delete.
#
# Fails CLOSED: it aborts rather than proceeding whenever it cannot confirm
# something, and exits non-zero if any delete fails, so a partial purge is never
# reported as success.
set -uo pipefail

BUCKET=${BUCKET:-provision-demo-tfstate}
DRY_RUN=${DRY_RUN:-1}
HERE=$(cd "$(dirname "$0")" && pwd)
VERIFY="$HERE/verify-no-secrets-in-state.sh"
KEYS=(
  provision-demo/app/terraform.tfstate
  provision-demo/github/terraform.tfstate
)
TOTAL=0
FAILED=0

# Gate: purging history while the current state still holds secrets would be
# pointless — the next apply would write them straight back. Invoked via bash so
# a missing executable bit in a fresh checkout cannot silently skip the check.
if [ ! -f "$VERIFY" ]; then
  echo "ABORT: $VERIFY not found; refusing to purge without verifying current state." >&2
  exit 1
fi
echo "Checking current state is clean before purging history..."
if ! BUCKET="$BUCKET" bash "$VERIFY"; then
  echo "ABORT: current state is unverified or still contains secret material." >&2
  exit 1
fi
echo

for key in "${KEYS[@]}"; do
  echo "== $key"
  err=$(mktemp)
  if ! versions=$(aws s3api list-object-versions --bucket "$BUCKET" --prefix "$key" \
        --query 'Versions[?IsLatest==`false`].VersionId' --output text 2>"$err"); then
    echo "   ERROR listing versions — not purged:" >&2
    sed 's/^/      /' "$err" | head -3 >&2
    rm -f "$err"; FAILED=$((FAILED + 1)); continue
  fi
  rm -f "$err"

  # `--output text` prints the literal string "None" for an empty result.
  versions=$(printf '%s' "$versions" | tr '\t' '\n' | grep -vx 'None' | grep -v '^$' || true)
  if [ -z "$versions" ]; then echo "   no noncurrent versions"; continue; fi

  count=0
  while read -r vid; do
    [ -z "$vid" ] && continue
    count=$((count + 1))
    if [ "$DRY_RUN" = "1" ]; then
      echo "   would delete $vid"
    elif aws s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$vid" >/dev/null 2>&1; then
      echo "   deleted $vid"
    else
      echo "   FAILED $vid" >&2; FAILED=$((FAILED + 1))
    fi
  done <<< "$versions"
  echo "   $count noncurrent version(s)"
  TOTAL=$((TOTAL + count))
done

echo
echo "$TOTAL noncurrent version(s) in scope."
if [ "$DRY_RUN" = "1" ]; then
  echo "DRY RUN — re-run with DRY_RUN=0 to delete."
elif [ "$FAILED" -gt 0 ]; then
  echo "INCOMPLETE: $FAILED operation(s) failed — remediation is only partial." >&2
fi
exit $([ "$FAILED" -gt 0 ] && echo 1 || echo 0)
