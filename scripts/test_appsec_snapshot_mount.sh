#!/usr/bin/env bash

# Проверяет AppSec hand-off на настоящем Linux bind mount. Producer запущен от
# root (это другой host identity), reader backend — UID/GID 10001. Reader обязан
# прочитать snapshot, но ни права файловой системы, ни read-only mount не должны
# позволить ему изменить файл.
set -euo pipefail

image_tag="sourcecraft-appsec-snapshot-mount-test:local"
snapshot_directory="$(mktemp -d)"

cleanup() {
  docker image rm --force "$image_tag" >/dev/null 2>&1 || true
  rm -rf -- "$snapshot_directory"
}
trap cleanup EXIT

docker build --file backend/Dockerfile --tag "$image_tag" . >/dev/null

# Root имитирует отдельную учётную запись exporter. Он меняет только группу
# приватного каталога mount и делает финальный JSON читаемым группой (0750 /
# 0640), но не доступным на запись группе или всем пользователям.
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

# Это точная непривилегированная учётная запись из backend/Dockerfile. Bind mount
# read-only, поэтому проверка также доказывает, что backend не меняет данные хоста.
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
