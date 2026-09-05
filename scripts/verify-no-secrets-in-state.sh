#!/usr/bin/env bash
# Asserts that no Terraform state object in the bucket contains secret material.
# This is the acceptance test for the secrets management redesign: Terraform
# manages the Secrets Manager containers, but never their values.
#
# Fails CLOSED. Anything that prevents a real check — a download error, a
# permissions error, unparseable JSON — is reported as ERROR and exits non-zero.
# A verifier that says PASS when it could not actually look is worse than none.
set -uo pipefail

BUCKET=${BUCKET:-provision-demo-tfstate}
KEYS=(
  provision-demo/app/terraform.tfstate
  provision-demo/github/terraform.tfstate
  provision-demo/bootstrap/terraform.tfstate
  provision-demo-platform/terraform.tfstate
)
FAIL=0

scan_one() {  # $1 = local json file; prints findings, exits non-zero on scan failure
  python3 - "$1" <<'PY'
import base64, json, sys
try:
    state = json.load(open(sys.argv[1]))
except Exception as e:
    print(f"scan error: {e}", file=sys.stderr); sys.exit(2)
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
}

for key in "${KEYS[@]}"; do
  name=$(basename "$(dirname "$key")")
  tmp=$(mktemp); err=$(mktemp)

  if ! aws s3api get-object --bucket "$BUCKET" --key "$key" "$tmp" >/dev/null 2>"$err"; then
    # Only a genuinely absent object is a legitimate skip. Anything else —
    # AccessDenied, throttling, network — means we did NOT verify this object.
    if grep -qiE 'NoSuchKey|Not Found|404' "$err"; then
      printf '  %-24s SKIP (absent)\n' "$name"
    else
      printf '  %-24s ERROR (could not read — NOT verified)\n' "$name"
      sed 's/^/      /' "$err" | head -3
      FAIL=1
    fi
    rm -f "$tmp" "$err"; continue
  fi

  findings=$(scan_one "$tmp" 2>"$err"); rc=$?
  rm -f "$tmp"
  if [ $rc -ne 0 ]; then
    printf '  %-24s ERROR (scan failed — NOT verified)\n' "$name"
    sed 's/^/      /' "$err" | head -3
    FAIL=1
  elif [ -n "$findings" ]; then
    printf '  %-24s FAIL\n' "$name"
    echo "$findings" | sed 's/^/      /'
    FAIL=1
  else
    printf '  %-24s PASS (no secret material)\n' "$name"
  fi
  rm -f "$err"
done

exit $FAIL
