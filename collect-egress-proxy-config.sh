#!/bin/sh
set -eu

SOURCE=/media/athena/chiron/hermes-output/hermes-dev-backend
DEST=/media/athena/code/phone-hermes/.hermes-egress-inspection

umask 077
mkdir -p "$DEST/tests"

for file in compose.yaml Dockerfile.proxy squid.conf verify.sh rollback.sh README.md; do
    if [ -f "$SOURCE/$file" ]; then
        cp "$SOURCE/$file" "$DEST/$file"
    fi
done

if [ -f "$SOURCE/tests/test_artifacts.py" ]; then
    cp "$SOURCE/tests/test_artifacts.py" "$DEST/tests/test_artifacts.py"
fi

printf 'Collected non-secret egress proxy build and test files in %s\n' "$DEST"
