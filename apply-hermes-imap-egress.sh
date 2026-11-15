#!/usr/bin/env bash
set -euo pipefail

ROOT=/media/athena/chiron/hermes-output/hermes-dev-backend
STAGED=/media/athena/code/phone-hermes/.hermes-egress-inspection
ORIGINAL_SQUID_SHA=ee7f509de5784a528e4f0ca0bca71af5a6adcb65d11ed8227abe1fa01a147677
ORIGINAL_TEST_SHA=1ebadee5a374465da6a5f004735adbbf08e072d58b24d059707a74746bf01c01
BACKUP_SQUID=$ROOT/squid.conf.before-hermes-imap
BACKUP_TEST=$ROOT/tests/test_artifacts.py.before-hermes-imap
RED_LOG=$(mktemp)
trap 'rm -f "$RED_LOG"' EXIT

sha() {
    sha256sum "$1" | cut -d ' ' -f 1
}

require_sha() {
    local path=$1 expected=$2
    local actual
    actual=$(sha "$path")
    if [[ $actual != "$expected" ]]; then
        printf 'Refusing to modify changed source: %s\nExpected SHA-256: %s\nActual SHA-256:   %s\n' \
            "$path" "$expected" "$actual" >&2
        exit 1
    fi
}

cd "$ROOT"
require_sha "$ROOT/squid.conf" "$ORIGINAL_SQUID_SHA"
require_sha "$ROOT/tests/test_artifacts.py" "$ORIGINAL_TEST_SHA"

install -m 0644 "$ROOT/squid.conf" "$BACKUP_SQUID"
install -m 0644 "$ROOT/tests/test_artifacts.py" "$BACKUP_TEST"

# TDD RED: install only the focused test and prove the old ACL fails it.
install -m 0644 "$STAGED/tests/test_artifacts.py" "$ROOT/tests/test_artifacts.py"
if python3 -m unittest \
    tests.test_artifacts.BackendArtifactTests.test_proxy_allows_only_the_dev_backend_to_connect_to_hyperion_imaps \
    >"$RED_LOG" 2>&1; then
    printf 'Expected the new proxy policy test to fail before changing squid.conf.\n' >&2
    install -m 0644 "$BACKUP_TEST" "$ROOT/tests/test_artifacts.py"
    exit 1
fi
if ! grep -Fq 'acl dev_backend src 172.21.0.10/32' "$RED_LOG"; then
    printf 'The RED test failed for an unexpected reason:\n' >&2
    cat "$RED_LOG" >&2
    install -m 0644 "$BACKUP_TEST" "$ROOT/tests/test_artifacts.py"
    exit 1
fi
printf 'RED verified: the old Squid policy fails the scoped IMAP test.\n'

# GREEN: install the narrowly scoped policy and run all artifact tests.
install -m 0644 "$STAGED/squid.conf" "$ROOT/squid.conf"
python3 -m unittest discover -s tests -q

docker compose --env-file "$ROOT/.runtime.env" build egress-proxy
docker compose --env-file "$ROOT/.runtime.env" run --rm --no-deps \
    egress-proxy /usr/sbin/squid -k parse -f /etc/squid/squid.conf
docker compose --env-file "$ROOT/.runtime.env" up -d --no-deps --force-recreate egress-proxy

docker inspect hermes-dev-egress --format '{{if .State.Running}}running{{else}}not-running{{end}}' \
    | grep -Fxq running

docker exec hermes-dev-egress /usr/sbin/squid -k parse -f /etc/squid/squid.conf
printf 'Scoped Hyperion IMAPS policy deployed. Rollback copies:\n  %s\n  %s\n' \
    "$BACKUP_SQUID" "$BACKUP_TEST"
