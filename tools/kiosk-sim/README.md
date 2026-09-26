# Kiosk simulator loop

Closed-loop, human-free acceptance test for **kiosk mode** (the kids'
CoverFlow appliance mode used on the Innioasis Y1).

It builds Rockbox's own UI simulator, runs it with no display (SDL dummy
video/audio drivers), injects button presses through a FIFO, and reads the
screen transitions back from trace lines the simulator prints. The kiosk
contract it checks:

| # | check | what a leak looks like |
|---|-------|------------------------|
| 1 | power-on lands in CoverFlow with no taps | main menu at boot |
| 2 | Menu tap in CoverFlow stays in CoverFlow | PictureFlow quits to the main menu |
| 3 | Menu hold in CoverFlow opens no menu | PictureFlow's own menu (Settings, Quit...) |
| 4 | selecting an album opens the player | track list / nothing |
| 5 | Menu in the player returns to CoverFlow | main menu |
| 6 | centre-hold in the player opens nothing | WPS context menu (delete, playlist...) |
| 7 | parent chord (hold Play, add Menu, ~5 s) reaches the main menu | nothing happens |

The `baseline` profile (kiosk mode off, CoverFlow via a shortcut) is expected
to **fail** checks 2, 5, 6 and 7: that run documents the leaks. The `kiosk`
profile must pass everything.

## Run it

Docker (reproducible, Linux gcc + SDL2):

    tools/kiosk-sim/run-docker.sh            # build dir: ./build-sim-kiosk

Natively (needs the compiler `tools/configure` picks, sdl2, ffmpeg):

    tools/kiosk-sim/run.sh [build-dir] [fixtures-dir]

Outputs land in `<build-dir>/kiosk-sim-out/`: `trace-*.log` (full simulator
stderr incl. `SIMTRACE` lines) and PNG screenshots taken at each step.

## Why the iPod 6G simulator

The Y1 target is an Android-hosted build and has no SDL simulator. Its keypad
(`INNIOASIS_Y1_PAD`) shares the PictureFlow key block with the iPod pads and
uses the same Menu/Select/Play/Left/Right/wheel buttons, so the iPod 6G
simulator exercises the identical core and plugin code paths. The Y1-specific
core keymap is covered by the Android build in `rockbox-y1-build/` (toolkit
repo), which compiles the same patches.

## Moving parts

* `firmware/target/hosted/sdl/button-sdl.c` — `RBSIM_INPUT=<fifo>` scripted
  input (`down/up/tap <SDL key name>`, `dump` for a screenshot).
* `apps/misc.c`, `apps/plugin.c`, `apps/root_menu.c` — `SIMTRACE` lines
  (simulator builds only).
* `simtest.py` — the contract; `make_fixtures.sh` — tiny tagged library.
