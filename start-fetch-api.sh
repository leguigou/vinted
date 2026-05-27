#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -z "${VINTED_FETCH_API_TOKEN:-}" ]; then
  echo "Definis VINTED_FETCH_API_TOKEN avant de lancer ce service." >&2
  exit 1
fi

export VINTED_FETCH_API_HOST="${VINTED_FETCH_API_HOST:-127.0.0.1}"
export VINTED_FETCH_API_PORT="${VINTED_FETCH_API_PORT:-8797}"

python3 vinted_fetch_api.py
