#!/usr/bin/env bash
# Put Twilio credentials into terraform/terraform.tfvars (gitignored) without printing the Auth Token.
# Usage: copy the Auth Token from the Twilio console to the clipboard, then:
#   ./scripts/set-twilio.sh AC...your-account-sid...
set -euo pipefail
cd "$(dirname "$0")/.."
SID=${1:?usage: set-twilio.sh <ACCOUNT_SID>  (Auth Token is read from the clipboard)}
[[ $SID =~ ^AC[0-9a-f]{32}$ ]] || { echo "That doesn't look like an Account SID (AC + 32 hex chars)"; exit 1; }
TOKEN=$(pbpaste | tr -d '[:space:]')
[[ $TOKEN =~ ^[0-9a-f]{32}$ ]] || { echo "Clipboard doesn't hold a Twilio Auth Token (32 hex chars). Copy it and retry."; exit 1; }
F=terraform/terraform.tfvars
touch "$F"
grep -v -E '^(twilio_account_sid|twilio_auth_token)\s*=' "$F" > "$F.tmp" || true
printf '\n# Twilio (set by scripts/set-twilio.sh)\ntwilio_account_sid = "%s"\ntwilio_auth_token  = "%s"\n' "$SID" "$TOKEN" >> "$F.tmp"
mv "$F.tmp" "$F"
chmod 600 "$F"
printf '' | pbcopy
echo "Saved Twilio credentials to $F (token not shown) and cleared the clipboard. Next: ./scripts/deploy.sh"
