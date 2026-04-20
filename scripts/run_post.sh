#!/bin/bash
# Entry point invoked by launchd. Sources .env, pulls latest from git,
# then runs the daily post.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Load .env
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Pull latest (audio clips, dracula.json, code). Failures are non-fatal so a
# transient network hiccup doesn't skip the daily post.
git pull --ff-only --quiet || echo "[run_post] git pull failed (continuing with local)"

exec "$REPO/.venv/bin/python" -m src.post_today "$@"
