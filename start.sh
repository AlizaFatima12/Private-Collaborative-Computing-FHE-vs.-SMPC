#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

if [ "${1:-}" = "--build" ]; then
  docker compose up -d --build
else
  docker compose up -d
fi

echo "Waiting for cluster (http://localhost:8501)..."
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8501/api/cluster >/dev/null 2>&1; then
    echo "System is READY!"
    echo "  Coordinator Console : http://localhost:8501"
    echo "  Client Node 1 Portal: http://localhost:9101"
    echo "  Client Node 2 Portal: http://localhost:9102"
    echo "  Client Node 3 Portal: http://localhost:9103"
    exit 0
  fi
  sleep 1
done

echo "Cluster started; check 'docker compose logs' if a node is still initializing."
