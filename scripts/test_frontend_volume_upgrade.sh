#!/usr/bin/env bash

# Verify that a frontend_node_modules volume created by an older root-run
# container still works after the frontend image switches to user `app`.
# The Compose project name isolates this test from every developer volume,
# including postgres_data.
set -euo pipefail

project_name="sourcecraft-frontend-upgrade-test"
compose_file="scripts/compose.frontend-upgrade.yaml"
log_file="$(mktemp)"

cleanup() {
  docker compose -f "$compose_file" -p "$project_name" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "$log_file"
}
trap cleanup EXIT

# This creates and seeds only ${project_name}_frontend_node_modules. The narrow
# Compose file has no backend, Redis, or PostgreSQL service or volume.
docker compose -f "$compose_file" -p "$project_name" build frontend >/dev/null
docker compose -f "$compose_file" -p "$project_name" run --rm --no-deps --user root frontend \
  sh -c 'chown -R root:root /app/node_modules && mkdir -p /app/node_modules/.vite' \
  >/dev/null

# Vite is a long-running process, so timeout is expected. Its readiness line
# proves it can write the cache even though node_modules remains root-owned.
set +e
docker compose -f "$compose_file" -p "$project_name" run --rm --no-deps frontend \
  sh -c 'timeout 10s npm run dev -- --host 127.0.0.1 --port 5173' \
  >"$log_file" 2>&1
vite_exit_code=$?
set -e

if [[ $vite_exit_code -ne 124 ]]; then
  cat "$log_file" >&2
  exit "$vite_exit_code"
fi

if grep -q "EACCES" "$log_file" || ! grep -q "Local:" "$log_file"; then
  cat "$log_file" >&2
  exit 1
fi
