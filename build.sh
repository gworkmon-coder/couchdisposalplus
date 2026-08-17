#!/usr/bin/env bash
# Couch Disposal Plus build. Runs locally and on Netlify.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> dependencies"
python3 -m pip install -r requirements.txt --quiet \
  || python3 -m pip install -r requirements.txt --quiet --break-system-packages

echo "==> city dataset"
python3 scripts/make_data.py
python3 scripts/make_universe.py

echo "==> generating pages + images"
python3 scripts/build.py --out dist

echo "==> validating"
python3 scripts/validate.py dist

echo
echo "Build complete."
du -sh dist
