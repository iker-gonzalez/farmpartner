#!/usr/bin/env bash
# Download NASA GRACE/GRACE-FO mascons with an Earthdata token from the clipboard (never printed) and write
# data/grace_trends.csv. Token: https://urs.earthdata.nasa.gov → Generate Token.
set -euo pipefail
cd "$(dirname "$0")/.."
EARTHDATA_TOKEN=$(pbpaste | tr -d '[:space:]')
[ ${#EARTHDATA_TOKEN} -gt 100 ] || { echo "Clipboard doesn't look like an Earthdata token. Copy it and retry."; exit 1; }
export EARTHDATA_TOKEN
printf '' | pbcopy
uv run -q --no-project --with xarray --with netCDF4 --with numpy python scripts/precompute_grace.py
