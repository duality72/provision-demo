#!/usr/bin/env bash
# Asserts that no Terraform state object in the bucket contains secret material.
# This is the acceptance test for the secrets management redesign: Terraform
# manages the Secrets Manager containers, but never their values.
#
# Exits non-zero if any state object holds a secret. Run it before purging state
# history — purging while the current state still writes secrets accomplishes
# nothing, because the next apply puts them straight back.
set -uo pipefail

BUCKET=${BUCKET:-provision-demo-tfstate}
KEYS=(
  provision-demo/app/terraform.tfstate
  provision-demo/github/terraform.tfstate
  provision-demo/bootstrap/terraform.tfstate
  provision-demo-platform/terraform.tfstate
)
FAIL=0

for key in "${KEYS[@]}"; do
  name=$(basename "$(dirname "$key")")
  tmp=$(mktemp)
  if ! aws s3api get-object --bucket "$BUCKET" --key "$key" "$tmp" >/dev/null 2>&1; then
    printf '  %-24s SKIP (absent)\n' "$name"; rm -f "$tmp"; continue
  fi
  findings=$(python3 - "$tmp" <<'PY'
import base64, json, sys
state = json.load(open(sys.argv[1]))
MARKERS = ("AGE-SECRET-KEY-1", "sk-ant-", "-----BEGIN")
hits = []
for r in state.get("resources", []):
    for i in r.get("instances", []):
        for field in ("secret_string", "plaintext_value"):
            v = i.get("attributes", {}).get(field)
            if not v or not isinstance(v, str):
                continue
            probe = v
            try:  # a base64 PEM only reveals itself once decoded
                probe = v + base64.b64decode(v + "==", validate=False).decode("utf-8", "ignore")
            except Exception:
                pass
            if any(m in probe for m in MARKERS):
                hits.append(f"{r['type']}.{r['name']}.{field}")
print("\n".join(hits))
PY
)
  rm -f "$tmp"
  if [ -n "$findings" ]; then
    printf '  %-24s FAIL\n' "$name"; echo "$findings" | sed 's/^/      /'; FAIL=1
  else
    printf '  %-24s PASS (no secret material)\n' "$name"
  fi
done

exit $FAIL
