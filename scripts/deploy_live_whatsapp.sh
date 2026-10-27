#!/usr/bin/env bash
# Update the existing receiver, keeping its identity, secrets, URL and capture boundary.
set -euo pipefail
D3B_PROJECT="${1:?Usage: bash scripts/deploy_live_whatsapp.sh PROJECT_ID}"
D3B_REGION="${D3B_REGION:-us-east1}"
D3B_SERVICE="d3b-whatsapp-webhook"
D3B_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
D3B_CONFIG="$(mktemp)"
trap 'rm -f "$D3B_CONFIG"' EXIT

gcloud run services describe "$D3B_SERVICE" --project="$D3B_PROJECT" --region="$D3B_REGION" --format=json > "$D3B_CONFIG"
D3B_CAPTURE_SINCE="$(python3 - "$D3B_CONFIG" <<'PY'
import json, sys
from datetime import datetime, timezone
service = json.load(open(sys.argv[1]))
env = {e['name']: e for e in service['spec']['template']['spec']['containers'][0].get('env', [])}
for key in ['META_APP_SECRET', 'META_VERIFY_TOKEN', 'CONTACT_HASH_KEY', 'REPORT_API_TOKEN', 'META_PHONE_NUMBER_ID']:
    if key not in env:
        raise SystemExit('Missing receiver configuration: ' + key)
print(env.get('LIVE_CAPTURE_SINCE', {}).get('value') or datetime.now(timezone.utc).isoformat())
PY
)"

gcloud run deploy "$D3B_SERVICE" --project="$D3B_PROJECT" --region="$D3B_REGION" \
  --source="$D3B_ROOT/whatsapp_webhook" \
  --update-env-vars="LIVE_CAPTURE_SINCE=$D3B_CAPTURE_SINCE" --quiet

D3B_URL="$(gcloud run services describe "$D3B_SERVICE" --project="$D3B_PROJECT" --region="$D3B_REGION" --format='value(status.url)')"
python3 - "$D3B_PROJECT" "$D3B_URL" <<'PY'
import json, subprocess, sys, urllib.request, urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
project, url = sys.argv[1:]
parsed = urllib.parse.urlsplit(url)
if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.run.app'):
    raise SystemExit('Unexpected receiver URL; verification stopped.')
token = subprocess.check_output(['gcloud', 'secrets', 'versions', 'access', 'latest',
    '--secret=d3b-wa-report-api-token', '--project=' + project], text=True).strip()
today = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
request = urllib.request.Request(url + '/api/live-contacts', method='POST',
    data=json.dumps({'start':today, 'end':today, 'patients':[]}).encode(),
    headers={'Authorization':'Bearer ' + token, 'Content-Type':'application/json'})
with urllib.request.urlopen(request, timeout=45) as response:
    data = json.load(response)
print('LIVE CHECK PASSED')
print('Capture since:', data['capture_since'])
print('People observed today:', len(data['contacts']))
print('Refresh the dashboard. No further daily command is required.')
PY
