#!/bin/bash
# Build the UI simulator and run the headless kiosk contract against it.
#
#   tools/kiosk-sim/run.sh [build-dir] [fixtures-dir]
#
# build-dir defaults to ./build-sim-kiosk (created with tools/configure
# --target=ipod6g --type=s, whose keypad matches the Innioasis Y1 in the
# pictureflow plugin).  fixtures-dir defaults to a generated tiny library.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BUILD="${1:-$ROOT/build-sim-kiosk}"
FIX="${2:-$BUILD/fixtures/Music}"

if [ ! -f "$BUILD/Makefile" ]; then
    mkdir -p "$BUILD" && cd "$BUILD"
    "$ROOT/tools/configure" --target=ipod6g --type=s
    if ! command -v "$(grep '^export CC=' Makefile | cut -d= -f2)" >/dev/null 2>&1; then
        echo "==> configured compiler not found, falling back to clang (macOS recipe)"
        # configure assumes Homebrew gcc on macOS; Apple clang works with:
        #  - fortified string macros off (they clash with firmware/include/strlc*.h)
        #  - the system archiver/ranlib/preprocessor
        #  - a stand-in libgcc.a for the -lgcc in plugins.make/codecs.make
        #  - no -no_warn_duplicate_libraries (older Apple ld rejects it)
        mkdir -p fakelib && echo 'int rb_fake_libgcc_dummy;' > fakelib/empty.c \
            && clang -c fakelib/empty.c -o fakelib/empty.o && ar rcs fakelib/libgcc.a fakelib/empty.o 2>/dev/null
        sed -i.bak \
            -e 's|^export CC=.*|export CC=clang|' \
            -e 's|^export CPP=.*|export CPP=clang -E|' \
            -e 's|^export AR=.*|export AR=/usr/bin/ar|' \
            -e 's|^export RANLIB=.*|export RANLIB=/usr/bin/ranlib|' \
            -e 's|^export GCCOPTS=|export GCCOPTS=-D_FORTIFY_SOURCE=0 |' \
            -e "s|^export GLOBAL_LDOPTS=|export GLOBAL_LDOPTS=-L$PWD/fakelib |" \
            -e 's| -Wl,-no_warn_duplicate_libraries||g' Makefile
    fi
fi
cd "$BUILD"
echo "==> make (the lua plugin and the SID codec do not build with clang; neither is needed)"
make -k -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu)" >/dev/null 2>build.err || true
test -x rockboxui || { echo "rockboxui not built:"; tail -30 build.err; exit 1; }
make -k install >/dev/null 2>&1 || true
test -f simdisk/.rockbox/rocks/demos/pictureflow.rock || { echo "install failed"; exit 1; }

if [ ! -d "$FIX" ]; then
    "$HERE/make_fixtures.sh" "$FIX"
fi

echo "==> baseline profile (kiosk mode off): documents the leaks"
python3 "$HERE/simtest.py" --build-dir "$BUILD" --fixtures "$FIX" --profile baseline \
    --out "$BUILD/kiosk-sim-out" || true
echo "==> kiosk profile: must pass"
python3 "$HERE/simtest.py" --build-dir "$BUILD" --fixtures "$FIX" --profile kiosk \
    --out "$BUILD/kiosk-sim-out"
echo "==> kiosk without a database: safety valve must pass"
python3 "$HERE/simtest.py" --build-dir "$BUILD" --fixtures "$FIX" --profile kiosk-nodb \
    --out "$BUILD/kiosk-sim-out"
