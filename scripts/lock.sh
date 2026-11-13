#!/usr/bin/env bash
# Regenerate the hash-locked requirement files CI installs with --require-hashes:
# requirements-{lint,docs,fuzz}.txt, each compiled from its .in. Dependabot
# recompiles them on its own; this is for editing an .in by hand.
#
# Runs in the CI image so markers and wheels resolve for Linux, not the host.
set -euo pipefail

if [ "${HA_JELLYFIN_LOCK_IN_DOCKER:-}" != 1 ]; then
  cd "$(dirname "$0")/.."
  exec docker run --rm -e HA_JELLYFIN_LOCK_IN_DOCKER=1 \
    -v "$PWD:/src" -v ha-jellyfin-pip:/root/.cache/pip -w /src \
    python:3.14-bookworm bash scripts/lock.sh
fi

pip install -q pip-tools==7.6.2
for name in lint docs fuzz; do
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras \
    --output-file="requirements-$name.txt" "requirements-$name.in"
done
