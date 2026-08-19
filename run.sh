#!/usr/bin/env bash
# Run the job hunt and open the workbook it produces. Previous runs are never
# overwritten - every run writes its own dated, timed set of reports.
#
#   ./run.sh --query "electrician in Leeds"
#   ./run.sh --query "remote flutter developer" --days 30
#   ./run.sh --offline                          demo data, no network
#
# job_agent owns where reports are written, what they are named, and opening
# the workbook when the run ends. Nothing here second-guesses any of that.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Setting up the virtual environment (first run only)..."
  python3 -m venv .venv
  .venv/bin/pip -q install -r requirements.txt
fi

exec "$PYTHON" -m job_agent daily "$@"
