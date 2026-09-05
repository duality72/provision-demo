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
set -uo pipefail

BUCKET=${BUCKET:-provision-demo-tfstate}
DRY_RUN=${DRY_RUN:-1}
KEYS=(
  provision-demo/app/terraform.tfstate
  provision-demo/github/terraform.tfstate
)
TOTAL=0

# Gate: purging history while the current state still holds secrets would be
# pointless — the next apply would write them straight back.
if [ -x "$(dirname "$0")/verify-no-secrets-in-state.sh" ]; then
  echo "Checking current state is clean before purging history..."
  if ! "$(dirname "$0")/verify-no-secrets-in-state.sh"; then
    echo "ABORT: current state still contains secret material. Fix that first." >&2
    exit 1
  fi
  echo
fi

for key in "${KEYS[@]}"; do
  echo "== $key"
  versions=$(aws s3api list-object-versions --bucket "$BUCKET" --prefix "$key" \
    --query 'Versions[?IsLatest==`false`].VersionId' --output text 2>/dev/null | tr '\t' '\n')
  [ -z "$versions" ] && { echo "   no noncurrent versions"; continue; }
  count=0
  while read -r vid; do
    [ -z "$vid" ] && continue
    count=$((count + 1))
    if [ "$DRY_RUN" = "1" ]; then
      echo "   would delete $vid"
    else
      aws s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$vid" >/dev/null \
        && echo "   deleted $vid" || echo "   FAILED $vid"
    fi
  done <<< "$versions"
  echo "   $count noncurrent version(s)"
  TOTAL=$((TOTAL + count))
done

echo
echo "$TOTAL noncurrent version(s) in scope."
[ "$DRY_RUN" = "1" ] && echo "DRY RUN — re-run with DRY_RUN=0 to delete."
exit 0
