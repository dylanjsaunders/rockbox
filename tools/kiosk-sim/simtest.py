#!/usr/bin/env python3
"""Headless kiosk acceptance tests for the Rockbox UI simulator.

Drives a simulator build (rockboxui) with no display and no human:
  * SDL runs on its dummy video/audio drivers,
  * button presses are injected through the RBSIM_INPUT FIFO
    (see firmware/target/hosted/sdl/button-sdl.c),
  * screen transitions are read back from the SIMTRACE lines the
    simulator prints on stderr (apps/misc.c, apps/plugin.c, apps/root_menu.c).

Usage:
  simtest.py --build-dir <dir with rockboxui + simdisk> --fixtures <Music dir>
             [--profile kiosk|baseline] [--out <report dir>]

Exit status is 0 only if every check in the kiosk contract passed.
"""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROCKBOX_ROOT = HERE.parent.parent

# --- key names are SDL key names; the iPod-style simulator map is:
#     Escape=Menu, Return=Select, Space=Play, Left/Right, Up=scroll back, Down=scroll fwd
MENU, SELECT, PLAY, LEFT, RIGHT = "Escape", "Return", "Space", "Left", "Right"

KIOSK_CONFIG = """\
# kiosk profile written by tools/kiosk-sim/simtest.py
kiosk mode: on
database autoupdate: on
warn when erasing dynamic playlist: off
shuffle: on
repeat: all
volume limit: -20
volume: -25
"""

BASELINE_CONFIG = """\
# baseline (pre-kiosk) profile written by tools/kiosk-sim/simtest.py
kiosk mode: off
start in screen: root
root menu order: shortcuts, database, wps, settings,
database autoupdate: on
warn when erasing dynamic playlist: off
shuffle: on
repeat: all
volume limit: -20
volume: -25
"""

DB_BUILD_CONFIG = """\
kiosk mode: off
start in screen: root
root menu order: database, files, wps, settings,
database autoupdate: on
"""


def activity_names():
    """Parse enum current_activity from apps/misc.h so trace ints get names."""
    text = (ROCKBOX_ROOT / "apps/misc.h").read_text()
    m = re.search(r"enum current_activity \{(.*?)\};", text, re.S)
    names = {}
    for i, tok in enumerate(re.findall(r"(ACTIVITY_[A-Z0-9_]+)", m.group(1))):
        names[i] = tok
    return names


ACT = activity_names()
ACT_ID = {v: k for k, v in ACT.items()}


class Sim:
    """One simulator process plus its trace log and input FIFO."""

    def __init__(self, build_dir: Path, out: Path, tag: str):
        self.build_dir = build_dir
        self.out = out
        self.tag = tag
        self.fifo = out / f"input-{tag}.fifo"
        self.log = out / f"trace-{tag}.log"
        self.proc = None
        self.fifo_fd = None
        self._lines = []
        self._pos = 0
        self.stack = [0]  # activity stack mirrored from traces

    def start(self):
        if self.fifo.exists():
            self.fifo.unlink()
        os.mkfifo(self.fifo)
        env = dict(os.environ)
        env.update({
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
            "RBSIM_INPUT": str(self.fifo),
        })
        self.logf = open(self.log, "wb")
        self.proc = subprocess.Popen(
            [str(self.build_dir / "rockboxui"), "--nobackground"],
            cwd=self.build_dir, env=env,
            stdout=self.logf, stderr=subprocess.STDOUT,
        )
        # opening the FIFO for writing blocks until the sim opens it for reading
        self.fifo_fd = os.open(self.fifo, os.O_WRONLY)
        # iPod-style targets power off on a held Play; the parent chord holds Play
        self.send("swpoweroff off")

    def send(self, line: str):
        try:
            os.write(self.fifo_fd, (line + "\n").encode())
        except OSError as e:  # simulator died (crash, power-off, ...)
            print(f"  !! simulator not accepting input ({e}); rc={self.proc.poll()}")

    def tap(self, key):
        self.send(f"tap {key}")

    def hold(self, key, seconds):
        self.send(f"down {key}")
        time.sleep(seconds)
        self.send(f"up {key}")

    def chord(self, first, second, seconds):
        self.send(f"down {first}")
        time.sleep(0.15)
        self.send(f"down {second}")
        time.sleep(seconds)
        self.send(f"up {second}")
        self.send(f"up {first}")

    def screenshot(self, name):
        before = set(self.build_dir.glob("simdisk/dump*.bmp"))
        self.send("dump")
        deadline = time.time() + 5
        while time.time() < deadline:
            new = set(self.build_dir.glob("simdisk/dump*.bmp")) - before
            if new:
                time.sleep(0.3)
                src = sorted(new)[-1]
                dst = self.out / f"{self.tag}-{name}.png"
                if shutil.which("sips"):
                    subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(dst)],
                                   capture_output=True)
                elif shutil.which("ffmpeg"):
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), str(dst)],
                                   capture_output=True)
                else:
                    dst = self.out / f"{self.tag}-{name}.bmp"
                    shutil.copy(src, dst)
                return dst
            time.sleep(0.1)
        return None

    # --- trace reading -------------------------------------------------
    def _refresh(self):
        self.logf.flush()
        with open(self.log, "rb") as f:
            f.seek(self._pos)
            data = f.read()
            self._pos += len(data)
        for raw in data.decode("utf-8", "replace").splitlines():
            if not raw.startswith("SIMTRACE"):
                continue
            self._lines.append(raw)
            m = re.match(r"SIMTRACE activity push (\d+) depth (\d+)", raw)
            if m:
                self.stack.append(int(m.group(1)))
                continue
            m = re.match(r"SIMTRACE activity pop -> (\d+) depth (\d+)", raw)
            if m and len(self.stack) > 1:
                self.stack.pop()

    def mark(self):
        self._refresh()
        return len(self._lines)

    def since(self, mark):
        self._refresh()
        return self._lines[mark:]

    def wait_for(self, pattern, timeout, mark=0):
        rx = re.compile(pattern)
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ln in self.since(mark):
                if rx.search(ln):
                    return ln
            if self.proc.poll() is not None:
                return None
            time.sleep(0.1)
        return None

    def current_activity(self):
        self._refresh()
        return ACT.get(self.stack[-1], str(self.stack[-1]))

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.fifo_fd is not None:
            os.close(self.fifo_fd)
        self.logf.close()


class Report:
    def __init__(self):
        self.rows = []

    def check(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        return ok

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


def prepare_simdisk(build_dir: Path, fixtures: Path):
    root = build_dir / "simdisk"
    rb = root / ".rockbox"
    pf = rb / "rocks/demos/pictureflow.rock"
    if not pf.exists():
        sys.exit(f"missing {pf}: run `make install` in {build_dir} first")
    music = root / "Music"
    if music.exists():
        shutil.rmtree(music)
    shutil.copytree(fixtures, music)
    for p in rb.glob("database_*.tcd"):
        p.unlink()
    for p in root.glob("dump*.bmp"):
        p.unlink()
    for cache in (rb / "rocks/demos/pictureflow", rb / "rocks/pictureflow"):
        if cache.exists():
            shutil.rmtree(cache)
    for stale in ("config.cfg.old", "shortcuts.txt"):
        (rb / stale).unlink(missing_ok=True)
    # settings also live in Rockbox's binary settings block; start from scratch
    for p in list(rb.glob("*nvram*")) + list(root.glob("*nvram*")):
        p.unlink()
    return root


def build_database(build_dir: Path, out: Path, report: Report):
    """Boot once with a plain profile so tagcache scans the fixture library."""
    rb = build_dir / "simdisk/.rockbox"
    (rb / "config.cfg").write_text(DB_BUILD_CONFIG)
    sim = Sim(build_dir, out, "dbbuild")
    sim.start()
    try:
        ok = sim.wait_for(r"SIMTRACE root menu shown", 30) is not None
        report.check("simulator boots headless", ok)
        # `database autoupdate` only maintains an existing database; the first
        # build needs Database -> "Initialize now?" -> Yes, exactly as on the Y1.
        time.sleep(1.5)
        sim.tap(SELECT)   # Database (first root item in DB_BUILD_CONFIG)
        time.sleep(1.5)
        sim.tap(SELECT)   # Yes, initialize
        # scan phase: wait until the temp database exists and stops growing
        deadline = time.time() + 90
        scanned = False
        last = -1
        while time.time() < deadline:
            tmp = rb / "database_tmp.tcd"
            idx = rb / "database_idx.tcd"
            if idx.exists() and not tmp.exists():
                scanned = True
                break
            if tmp.exists():
                size = tmp.stat().st_size
                if size == last and size > 0:
                    scanned = True
                    break
                last = size
            time.sleep(2)
        report.check("tagcache scanned the fixture library", scanned)
    finally:
        sim.stop()
    # commit phase: like the device, a fresh database is committed on the next boot
    built = (rb / "database_idx.tcd").exists() and not (rb / "database_tmp.tcd").exists()
    if scanned and not built:
        sim = Sim(build_dir, out, "dbcommit")
        sim.start()
        try:
            deadline = time.time() + 90
            while time.time() < deadline:
                if (rb / "database_idx.tcd").exists() and not (rb / "database_tmp.tcd").exists():
                    built = True
                    break
                time.sleep(1)
            time.sleep(2)
        finally:
            sim.stop()
    report.check("tagcache committed (database_idx.tcd present)", built,
                 ", ".join(sorted(p.name for p in rb.glob("database_*.tcd")))[:70])


def enter_coverflow_baseline(sim: Sim, report: Report):
    ok = sim.wait_for(r"SIMTRACE root menu shown", 30) is not None
    report.check("baseline boots to the main menu", ok)
    time.sleep(1.0)
    sim.tap(SELECT)          # Shortcuts (first root item)
    time.sleep(1.0)
    sim.tap(SELECT)          # CoverFlow shortcut
    ok = sim.wait_for(r"SIMTRACE plugin_load .*pictureflow", 15) is not None
    report.check("baseline reaches CoverFlow via shortcut", ok)


def run_contract(sim: Sim, report: Report, profile: str):
    """The kiosk contract. Every check here must pass on the patched build."""
    if profile == "kiosk":
        ok = sim.wait_for(r"SIMTRACE plugin_load .*pictureflow", 30) is not None
        report.check("power-on lands in CoverFlow with no taps", ok)
    else:
        enter_coverflow_baseline(sim, report)
    time.sleep(5.0)  # album-art cache build on first run
    sim.screenshot("01-coverflow")

    # 1. Menu tap inside CoverFlow must not reach any menu
    m = sim.mark()
    sim.tap(MENU)
    time.sleep(2.5)
    leaked = [l for l in sim.since(m) if "root menu shown" in l or "plugin_exit" in l]
    report.check("Menu tap in CoverFlow stays in CoverFlow",
                 not leaked and sim.current_activity() == "ACTIVITY_PLUGIN",
                 f"activity={sim.current_activity()}")
    if leaked:
        sim.screenshot("leak-menu-tap")
        return  # nothing below is meaningful once we fell out of the kiosk

    # 2. Menu hold inside CoverFlow must not open PictureFlow's own menu
    m = sim.mark()
    sim.hold(MENU, 1.2)
    time.sleep(2.0)
    bad = [l for l in sim.since(m) if "root menu shown" in l or "activity push" in l]
    report.check("Menu hold in CoverFlow opens no menu", not bad, "; ".join(bad)[:80])

    # 3. Selecting an album plays it in the themed player (WPS)
    m = sim.mark()
    sim.tap(SELECT)
    ok = sim.wait_for(rf"SIMTRACE activity push {ACT_ID['ACTIVITY_WPS']} ", 15, m) is not None
    report.check("selecting an album opens the player", ok, f"activity={sim.current_activity()}")
    time.sleep(1.5)
    sim.screenshot("02-player")

    # 4. Menu in the player returns to CoverFlow, never the main menu
    m = sim.mark()
    sim.tap(MENU)
    back = sim.wait_for(r"SIMTRACE plugin_load .*pictureflow", 15, m) is not None
    leaked = [l for l in sim.since(m) if "root menu shown" in l]
    report.check("Menu in the player returns to CoverFlow", back and not leaked,
                 f"activity={sim.current_activity()}")
    if leaked:
        sim.screenshot("leak-player-menu")
        return
    time.sleep(3.0)

    # 5. Centre-hold in the player must not open the context menu
    sim.tap(SELECT)
    if sim.wait_for(rf"SIMTRACE activity push {ACT_ID['ACTIVITY_WPS']} ", 15, sim.mark()) is None:
        report.check("re-enter player for context-menu check", False)
        return
    time.sleep(1.0)
    m = sim.mark()
    sim.hold(SELECT, 1.0)
    time.sleep(2.0)
    ctx = [l for l in sim.since(m)
           if f"activity push {ACT_ID['ACTIVITY_CONTEXTMENU']} " in l or "root menu shown" in l]
    report.check("centre-hold in the player opens nothing", not ctx, "; ".join(ctx)[:80])
    if ctx:
        sim.screenshot("leak-context-menu")
        sim.tap(MENU)  # try to back out
        time.sleep(1.5)
    sim.tap(MENU)      # back to CoverFlow
    sim.wait_for(r"SIMTRACE plugin_load .*pictureflow", 15, m)
    time.sleep(3.0)

    # 6. Only the parent chord (Play + Menu held ~3 s) reaches the main menu
    m = sim.mark()
    sim.chord(PLAY, MENU, 5.5)
    ok = sim.wait_for(r"SIMTRACE root menu shown", 8, m) is not None
    report.check("parent chord reaches the main menu", ok, f"activity={sim.current_activity()}")
    sim.screenshot("03-after-chord")


def run_nodb(sim: Sim, report: Report):
    """Safety valve: kiosk mode with no database must not trap everyone in a
    loop of PictureFlow's "Please enable database / press any button" prompt."""
    ok = sim.wait_for(r"SIMTRACE plugin_load .*pictureflow", 30) is not None
    report.check("kiosk boots into CoverFlow even without a database", ok)
    gave_up = None
    for _ in range(12):
        time.sleep(1.5)
        sim.tap(SELECT)  # dismiss "press any button to continue"
        gave_up = sim.wait_for(r"SIMTRACE kiosk gave up", 1)
        if gave_up:
            break
    report.check("kiosk gives up after repeated plugin failures", gave_up is not None)
    ok = sim.wait_for(r"SIMTRACE root menu shown", 10) is not None
    report.check("main menu is reachable after the kiosk gave up", ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", required=True, type=Path)
    ap.add_argument("--fixtures", required=True, type=Path)
    ap.add_argument("--profile", choices=["kiosk", "baseline", "kiosk-nodb"], default="kiosk")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    build_dir = args.build_dir.resolve()
    out = (args.out or (build_dir / "kiosk-sim-out")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = Report()

    print(f"== profile={args.profile} build={build_dir}")
    root = prepare_simdisk(build_dir, args.fixtures.resolve())
    rb = root / ".rockbox"

    if args.profile == "kiosk-nodb":
        print("== kiosk without a database (safety valve)")
        (rb / "config.cfg").write_text(KIOSK_CONFIG)
        sim = Sim(build_dir, out, args.profile)
        sim.start()
        try:
            run_nodb(sim, report)
        finally:
            sim.stop()
        print(f"== {len(report.rows) - len(report.failed)}/{len(report.rows)} checks passed")
        sys.exit(1 if report.failed else 0)

    print("== phase 1: build tagcache")
    build_database(build_dir, out, report)
    if report.failed:
        sys.exit(1)

    print(f"== phase 2: {args.profile} contract")
    if args.profile == "kiosk":
        (rb / "config.cfg").write_text(KIOSK_CONFIG)
    else:
        (rb / "config.cfg").write_text(BASELINE_CONFIG)
        pf_rock = "/.rockbox/rocks/demos/pictureflow.rock"
        (rb / "shortcuts.txt").write_text(
            f"[shortcut]\ntype: file\ndata: {pf_rock}\nname: CoverFlow\n")
        (rb / "rocks/pictureflow.cfg").write_text("file version: 1\nauto wps: 1\n")
    sim = Sim(build_dir, out, args.profile)
    sim.start()
    try:
        run_contract(sim, report, args.profile)
    finally:
        sim.stop()

    print(f"== {len(report.rows) - len(report.failed)}/{len(report.rows)} checks passed; "
          f"traces and screenshots in {out}")
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
