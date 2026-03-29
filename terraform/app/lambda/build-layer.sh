#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAYER_DIR=$(mktemp -d)
TARGET="${LAYER_DIR}/python/lib/python3.12/site-packages"

echo "Installing dependencies for Linux x86_64 into ${TARGET}..."
pip install --target "${TARGET}" --quiet \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  "PyJWT[crypto]>=2.8.0" \
  "cryptography>=41.0.0" \
  "pyrage>=1.3.0"

echo "Creating lambda-layer.zip..."
cd "${LAYER_DIR}"
zip -r9 "${SCRIPT_DIR}/../lambda-layer.zip" python/

echo "Cleaning up..."
rm -rf "${LAYER_DIR}"

echo "Done. Layer zip created at: ${SCRIPT_DIR}/../lambda-layer.zip"
