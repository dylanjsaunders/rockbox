#!/bin/bash
# Run the whole kiosk simulator loop inside the y1-kiosk-sim Docker image.
#   tools/kiosk-sim/run-docker.sh [build-dir-on-host]
# The source tree is bind-mounted read-write; the build lives in build-dir.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BUILD="${1:-$ROOT/build-sim-kiosk}"
mkdir -p "$BUILD"
docker image inspect y1-kiosk-sim >/dev/null 2>&1 || docker build -t y1-kiosk-sim "$HERE"
docker run --rm -v "$ROOT":/src -v "$BUILD":/build y1-kiosk-sim \
    bash -lc "/src/tools/kiosk-sim/run.sh /build /build/fixtures/Music"
