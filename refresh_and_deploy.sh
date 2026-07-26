#!/usr/bin/env bash
# refresh_and_deploy.sh — pull fresh OFAC data, rebuild, redeploy.
# Run this daily (Treasury updates the SDN list multiple times per week).
#
# Usage:  ./refresh_and_deploy.sh
# Or wire to cron / GitHub Actions for full automation.
set -euo pipefail
cd "$(dirname "$0")"

SITE_BASE="${SITE_BASE:-https://ofac-sdn-directory.vercel.app}"
echo "→ Fetching fresh OFAC data from U.S. Treasury…"
python3 src/fetch_data.py

echo "→ Rebuilding site (canonical: $SITE_BASE)…"
SITE_BASE="$SITE_BASE" python3 src/build.py

echo "→ Deploying to Vercel…"
vercel deploy dist --prod --yes --archive=tgz | tail -3

echo "→ Pinging IndexNow with key URLs…"
KEY=$(ls dist/*.txt | head -1 | xargs basename | sed 's/\.txt$//')
curl -s -o /dev/null -X POST "https://api.indexnow.org/indexnow" \
  -H "Content-Type: application/json" \
  -d "{\"host\":\"ofac-sdn-directory.vercel.app\",\"key\":\"$KEY\",\"urlList\":[\"$SITE_BASE/\"]}"

echo "✓ Done. Fresh data deployed + search engines notified."
