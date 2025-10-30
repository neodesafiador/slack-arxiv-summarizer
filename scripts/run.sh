#!/usr/bin/env bash
set -euo pipefail

# Move to repo root
cd "$(dirname "$0")/.."

# Load environment variables
if [ ! -f .env ]; then
  echo "[ERROR] .env not found. Please copy .env.example to .env and fill in your tokens." >&2
  exit 1
fi
set -a
source ./.env
set +a

echo "Starting Slack arXiv Summarizer..."
python ./src/slack_arxiv_summarizer.py
