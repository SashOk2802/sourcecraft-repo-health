#!/usr/bin/env bash

# Proves the AppSec hand-off on a real Linux bind mount. The producer is root
# (a different host identity) and the backend reader is UID/GID 10001. The
# reader must be able to read the snapshot, but neither filesystem permissions
# nor the read-only mount may let it alter it.
set -euo pipefail

image_tag="sourcecraft-appsec-snapshot-mount-test:local"
snapshot_directory="$(mktemp -d)"

cleanup() {
  docker image rm --force "$image_tag" >/dev/null 2>&1 || true
  rm -rf -- "$snapshot_directory"
}
trap cleanup EXIT

docker build --file backend/Dockerfile --tag "$image_tag" . >/dev/null

# Root simulates a separate exporter identity. It changes only the group of
# the private mount directory and makes the final JSON group-readable (0750 /
# 0640), never group-writable or world-readable.
docker run --rm --user 0:0 \
  --volume "$snapshot_directory:/snapshots" \
  "$image_tag" \
  python -c '
from datetime import UTC, datetime
from pathlib import Path
from backend.app.integrations.sourcecraft_appsec_probe import AppSecProbeResult
from backend.app.integrations.sourcecraft_appsec_snapshot import write_snapshot

commit = "a" * 40
results = tuple(
    AppSecProbeResult(
        engine,
        "available",
        0,
        finding_groups=(),
        completeness="complete",
        scan_commit_sha=commit,
    )
    for engine in ("SAST", "SCA", "SECRETS")
)
write_snapshot(
    Path("/snapshots"),
    repository_id="repository-id-redacted",
    collected_at=datetime.now(UTC),
    results=results,
    commit_sha=commit,
    reader_gid=10001,
)
'

# This is the exact non-root identity from backend/Dockerfile. The bind mount
# is read-only, so this also proves that backend code cannot modify host data.
docker run --rm --user 10001:10001 \
  --read-only \
  --volume "$snapshot_directory:/snapshots:ro" \
  "$image_tag" \
  python -c '
from pathlib import Path

snapshot = next(Path("/snapshots").glob("*.json"))
assert snapshot.read_text(encoding="utf-8")
try:
    snapshot.write_text("must-not-write", encoding="utf-8")
except OSError:
    pass
else:
    raise AssertionError("backend user unexpectedly wrote the read-only snapshot")
'

printf 'AppSec snapshot different-user mount test passed.\n'
