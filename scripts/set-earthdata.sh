#!/usr/bin/env bash
# Store NASA Earthdata credentials for AppEEARS (SMAP + ECOSTRESS) in terraform/terraform.tfvars (gitignored).
#   ./scripts/set-earthdata.sh            # token mode: Earthdata token on the clipboard
#   ./scripts/set-earthdata.sh <username> # login mode: Earthdata PASSWORD on the clipboard
# Nothing secret is printed; the clipboard is cleared afterwards.
set -euo pipefail
cd "$(dirname "$0")/.."
API=https://appeears.earthdatacloud.nasa.gov/api
F=terraform/terraform.tfvars
SECRET=$(pbpaste | tr -d '\r\n')
[ -n "$SECRET" ] || { echo "Clipboard is empty."; exit 1; }
touch "$F"
grep -v -E '^(earthdata_token|earthdata_username|earthdata_password)\s*=' "$F" > "$F.tmp" || true

if [ $# -eq 0 ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $SECRET" "$API/task")
  echo "AppEEARS with Earthdata token -> HTTP $code"
  if [ "$code" != "200" ]; then
    rm -f "$F.tmp"; printf '' | pbcopy
    echo "Token not accepted. Use login mode: copy your Earthdata PASSWORD, then ./scripts/set-earthdata.sh <username>"
    exit 1
  fi
  printf '\n# NASA Earthdata (AppEEARS: SMAP, ECOSTRESS)\nearthdata_token = "%s"\n' "$SECRET" >> "$F.tmp"
else
  USER_=$1
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -u "$USER_:$SECRET" -H "Content-Length: 0" "$API/login")
  echo "AppEEARS login for $USER_ -> HTTP $code"
  [ "$code" = "200" ] || { rm -f "$F.tmp"; printf '' | pbcopy; echo "Login failed; check username/password."; exit 1; }
  esc=$(printf '%s' "$SECRET" | sed 's/\\/\\\\/g; s/"/\\"/g')
  printf '\n# NASA Earthdata (AppEEARS: SMAP, ECOSTRESS)\nearthdata_username = "%s"\nearthdata_password = "%s"\n' "$USER_" "$esc" >> "$F.tmp"
fi
mv "$F.tmp" "$F"; chmod 600 "$F"; printf '' | pbcopy
echo "Saved to $F (nothing secret shown). Clipboard cleared."
