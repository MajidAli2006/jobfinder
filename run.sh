#!/usr/bin/env bash
# Run the Flutter job hunt, save a timestamped workbook to the Desktop folder
# and open it. Previous runs are never overwritten.
#
#   flutterjobs                 last 7 days
#   flutterjobs --days 30       wider window
#   flutterjobs --offline       demo data, no network
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Setting up the virtual environment (first run only)..."
  python3 -m venv .venv
  .venv/bin/pip -q install -r requirements.txt
fi

# The full set of reports stays in ./reports as a working archive.
"$PYTHON" -m job_agent daily "$@"

LATEST="$(ls -t reports/*flutter_jobs_*.xlsx 2>/dev/null | head -1 || true)"
if [ -z "$LATEST" ]; then
  echo "No workbook was produced - nothing to open."
  exit 1
fi

# Every run gets its own dated + timed file, so nothing is ever overwritten.
OUTDIR="${HOME}/Desktop/job finder"
mkdir -p "$OUTDIR"
case "$LATEST" in
  *DEMO_FIXTURES*) LABEL="DEMO (fake data) " ;;
  *)               LABEL="" ;;
esac
TARGET="${OUTDIR}/${LABEL}Flutter Jobs $(date '+%Y-%m-%d %H%M').xlsx"
cp "$LATEST" "$TARGET"

echo ""
echo "Saved: ${TARGET}"
COUNT=$(ls -1 "$OUTDIR"/*.xlsx 2>/dev/null | wc -l | tr -d ' ')
echo "       (${COUNT} report(s) now in the 'job finder' folder)"
open "$TARGET"
