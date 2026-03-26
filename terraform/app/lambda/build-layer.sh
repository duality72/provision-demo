#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAYER_DIR=$(mktemp -d)
TARGET="${LAYER_DIR}/python/lib/python3.12/site-packages"

echo "Installing dependencies into ${TARGET}..."
pip install --target "${TARGET}" --quiet \
  "PyJWT[crypto]>=2.8.0" \
  "cryptography>=41.0.0"

echo "Creating lambda-layer.zip..."
cd "${LAYER_DIR}"
zip -r9 "${SCRIPT_DIR}/../lambda-layer.zip" python/

echo "Cleaning up..."
rm -rf "${LAYER_DIR}"

echo "Done. Layer zip created at: ${SCRIPT_DIR}/../lambda-layer.zip"
