#!/usr/bin/env bash
# Store the Supabase connection string in Secret Manager without printing the password.
# Usage: copy your Supabase database password to the clipboard, then run ./scripts/set-db-secret.sh
set -euo pipefail
PROJECT=${PROJECT:-launchpad-all-dev-01}
SECRET=farmpartner-database-url
SA=farmpartner-run@${PROJECT}.iam.gserviceaccount.com
HOST=${SUPABASE_POOLER_HOST:-aws-0-eu-west-1.pooler.supabase.com}
USER_=${SUPABASE_USER:-postgres.pjrwkpuiudqmqlmoshkl}

URI=$(pbpaste | python3 -c "import sys, urllib.parse as u; pw=sys.stdin.read().strip(); assert pw, 'clipboard is empty'; print('postgresql://$USER_:' + u.quote(pw, safe='') + '@$HOST:5432/postgres', end='')")
if gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  printf %s "$URI" | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=- >/dev/null
else
  printf %s "$URI" | gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy automatic --data-file=- >/dev/null
fi
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" --member "serviceAccount:$SA" \
  --role roles/secretmanager.secretAccessor >/dev/null
unset URI
printf '' | pbcopy
echo "Stored $SECRET (password not shown) and cleared the clipboard."
