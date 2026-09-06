#!/usr/bin/env python3
"""Claude Notch — a screen-edge notch for Claude Code on KDE Plasma (Wayland).

Rest:  a thin sliver on the screen edge, coloured by how much of your plan is used.
Hover: it grows into a bubble with a usage ring and a details panel.
Click: the bubble unfolds into a terminal running your agent; click the strip to fold it back.

The window is one fixed-size transparent Qt Quick surface; all morphing happens
inside it (60 fps QML animations). The compositor cannot let a Wayland client
position itself, so the notch and the terminal are placed through KWin's
scripting API. The input region (window mask) follows the visible shape, so a
resting notch only reacts to the sliver.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:          # Python < 3.11
    tomllib = None

from PySide6.QtCore import ClassInfo, Property, QLocale, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import QDBusConnection
from PySide6.QtGui import QGuiApplication, QRegion
from PySide6.QtQml import QQmlApplicationEngine

APP_DIR = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "claude-notch"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))
CONFIG_FILE = CONFIG_DIR / "config.toml"
USAGE_FILE = CACHE_DIR / "claude-usage.json"
KWIN_SCRIPT = CACHE_DIR / "claude-notch-kwin.js"

APP_ID = "claude-notch"                     # Wayland app_id of the notch window
TERM_CLASS = "claude-notch-terminal"        # app_id of the chat terminal
DBUS_SERVICE = "org.claudenotch.Notch"
DBUS_PATH = "/Notch"

DEFAULTS = {
    "terminal": {
        "launch": "alacritty --config-file {theme} --class {class} -T {title} -e {shell} -c {command}",
        "theme": str(CONFIG_DIR / "alacritty.toml"),
    },
    "agent": {"command": "claude", "workdir": "~/Projects", "keep_shell": True},
    "screen": {"name": "auto", "height_ratio": 0.94},
    "layout": {
        "width": 720, "strip": 46, "pad": 10, "inset": 26,
        "sliver_w": 5, "sliver_h": 132, "sliver_hot": 14,
        "bubble_w": 78, "bubble_h": 118, "panel_w": 300,
    },
    "timing": {
        "fade_ms": 130, "collapse_delay_ms": 100,
        "stale_after_s": 1200, "data_refresh_ms": 15000,
    },
    "colors": {"background": "#0d0d0f", "ok": "#32d74b", "warn": "#ffd426",
               "crit": "#ff453a", "none": "#6b6b6b"},
    "ui": {"language": "auto"},
}

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def log(msg: str) -> None:
    if VERBOSE:
        print(f"[claude-notch] {msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    print(f"[claude-notch] {msg}", file=sys.stderr, flush=True)


# ── configuration ──────────────────────────────────────────────────────────
def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict:
    cfg = DEFAULTS
    if CONFIG_FILE.exists():
        if tomllib is None:
            warn("config.toml found but Python lacks tomllib (need 3.11+); using defaults")
        else:
            try:
                cfg = _merge(DEFAULTS, tomllib.loads(CONFIG_FILE.read_text()))
            except Exception as e:                       # noqa: BLE001
                warn(f"cannot parse {CONFIG_FILE}: {e}; using defaults")
    lang = cfg["ui"]["language"]
    if lang == "auto":
        lang = (QLocale.system().name() or os.environ.get("LANG") or "en")[:2].lower()
    cfg["ui"]["language"] = lang if lang in ("en", "cs") else "en"
    return cfg


# ── screens ────────────────────────────────────────────────────────────────
def primary_geometry(name: str = "auto") -> tuple[int, int, int, int]:
    """Geometry of the target output. 'auto' picks KDE's primary (priority 1),
    which is what the user set in System Settings — Qt's primaryScreen() often
    disagrees on multi-monitor setups."""
    try:
        out = subprocess.run(["kscreen-doctor", "-o"], capture_output=True,
                             text=True, timeout=8).stdout
        out = re.sub(r"\x1b\[[0-9;]*m", "", out)
        blocks: list[dict] = []
        cur: dict | None = None
        for line in out.splitlines():
            if line.startswith("Output:"):
                cur = {"name": line.split()[2] if len(line.split()) > 2 else "", "prio": None, "geo": None}
                blocks.append(cur)
            elif cur is not None:
                m = re.search(r"priority (\d+)", line)
                if m:
                    cur["prio"] = int(m.group(1))
                m = re.search(r"Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)", line)
                if m:
                    cur["geo"] = tuple(int(g) for g in m.groups())
        for b in blocks:
            if b["geo"] and ((name == "auto" and b["prio"] == 1) or b["name"] == name):
                return b["geo"]
        if name != "auto":
            warn(f"output '{name}' not found; falling back to primary")
    except Exception as e:                               # noqa: BLE001
        log(f"kscreen-doctor unavailable ({e}); using Qt primary screen")
    g = QGuiApplication.primaryScreen().geometry()
    return (g.x(), g.y(), g.width(), g.height())


# ── KWin scripting ─────────────────────────────────────────────────────────
_seq = 0


_kwin_lock = threading.Lock()
_kwin_jobs: list[str] = []


def run_kwin(js: str) -> None:
    """Queue a one-shot KWin script. Scripts run in order on a worker thread so
    the two D-Bus round trips (~50-100 ms) never stall the QML animation.
    KWin caches scripts by name and never re-reads the file, hence a fresh
    name (and a fresh file) per call."""
    with _kwin_lock:
        _kwin_jobs.append(js)
        if len(_kwin_jobs) == 1:
            threading.Thread(target=_kwin_worker, daemon=True).start()


def _kwin_worker() -> None:
    global _seq
    while True:
        with _kwin_lock:
            if not _kwin_jobs:
                return
            js = _kwin_jobs[0]
        _seq += 1
        path = KWIN_SCRIPT.with_name(f"claude-notch-kwin-{_seq}.js")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(js)
        name = f"claudenotch{os.getpid()}x{_seq}"
        try:
            sid = subprocess.run(["qdbus6", "org.kde.KWin", "/Scripting",
                                  "org.kde.kwin.Scripting.loadScript", str(path), name],
                                 capture_output=True, text=True, timeout=8).stdout.strip()
            if sid:
                subprocess.run(["qdbus6", "org.kde.KWin", f"/Scripting/Script{sid}",
                                "org.kde.kwin.Script.run"], capture_output=True, timeout=8)
            else:
                warn("KWin refused the script (is this a KDE Plasma Wayland session?)")
        except Exception as e:                           # noqa: BLE001
            warn(f"cannot talk to KWin: {e}")
        finally:
            path.unlink(missing_ok=True)
            with _kwin_lock:
                _kwin_jobs.pop(0)


_PIN = ("win.keepAbove = true; win.skipTaskbar = true; "
        "win.skipPager = true; win.skipSwitcher = true;")


def _fade_js(ms: int) -> str:
    steps = max(3, round(ms / 16))
    return f"""
    var t = new QTimer(); t.interval = 16; var step = 0;
    t.timeout.connect(function() {{
        step++; var p = Math.min(1, step / {steps});
        win.opacity = p * p * (3 - 2 * p);
        if (p >= 1) t.stop();
    }});
    t.start();"""


def place(cls: str, x: int, y: int, w: int, h: int, *, raise_it=False,
          hidden=False, fade_ms=0) -> None:
    """Position a window by app_id. hidden=True parks it fully transparent
    (so KWin's un-minimize flight is never seen) until reveal() is called;
    fade_ms>0 fades it in right away instead."""
    run_kwin(f"""
workspace.windowList().forEach(function(win) {{
    if (win.resourceClass !== "{cls}") return;
    {_PIN}
    {"win.opacity = 0;" if (hidden or fade_ms) else "win.opacity = 1;"}
    if (win.minimized) win.minimized = false;
    win.frameGeometry = {{ x: {x}, y: {y}, width: {w}, height: {h} }};
    {"workspace.activeWindow = win;" if raise_it else ""}
    {_fade_js(fade_ms) if fade_ms else ""}
}});
""")


def reveal(cls: str, fade_ms: int) -> None:
    """Fade a parked window in. Called by QML the moment the container fully
    covers the terminal's rectangle, so the terminal never shows over the desktop."""
    run_kwin(f"""
workspace.windowList().forEach(function(win) {{
    if (win.resourceClass !== "{cls}") return;
    if (win.minimized) win.minimized = false;
    {_fade_js(fade_ms)}
}});
""")


def hide_window(cls: str) -> None:
    """Fade out quickly, then minimize — the minimize animation runs on a
    fully transparent window, i.e. invisibly."""
    run_kwin(f"""
workspace.windowList().forEach(function(win) {{
    if (win.resourceClass !== "{cls}") return;
    var t = new QTimer(); t.interval = 16; var step = 0;
    t.timeout.connect(function() {{
        step++; var p = Math.min(1, step / 5);
        win.opacity = 1 - p;
        if (p >= 1) {{ t.stop(); win.minimized = true; }}
    }});
    t.start();
}});
""")


# ── the bridge between Python and QML (also exported on D-Bus) ─────────────
@ClassInfo({"D-Bus Interface": DBUS_SERVICE})   # explicit: the app name has a hyphen, invalid in a derived interface name
class Bridge(QObject):
    usageChanged = Signal()
    chatOpenChanged = Signal()

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.L = cfg["layout"]
        self.T = cfg["timing"]
        self._usage = self._empty()
        self._window = None
        self._chat = False
        self._proc: subprocess.Popen | None = None
        self._geo = primary_geometry(cfg["screen"]["name"])
        self._win_h = int(self._geo[3] * cfg["screen"]["height_ratio"])

        self._data_timer = QTimer(self)
        self._data_timer.timeout.connect(self.reload)
        self._data_timer.start(int(self.T["data_refresh_ms"]))
        self.reload()

    # ── usage feed ──────────────────────────────────────────────────────
    @staticmethod
    def _empty() -> dict:
        return {"fiveHour": -1, "sevenDay": -1, "fiveReset": 0, "sevenReset": 0,
                "writtenAt": 0, "model": "", "now": time.time()}

    @Property("QVariant", notify=usageChanged)
    def usage(self):
        return self._usage

    @Slot()
    def reload(self) -> None:
        d = self._empty()
        try:
            raw = json.loads(USAGE_FILE.read_text())
            rl = raw.get("rate_limits") or {}
            fh, sd = rl.get("five_hour") or {}, rl.get("seven_day") or {}
            if "used_percentage" in fh:
                d["fiveHour"] = round(fh["used_percentage"]); d["fiveReset"] = fh.get("resets_at", 0)
            if "used_percentage" in sd:
                d["sevenDay"] = round(sd["used_percentage"]); d["sevenReset"] = sd.get("resets_at", 0)
            d["writtenAt"] = raw.get("_at", 0)
            d["model"] = (raw.get("model") or {}).get("display_name", "")
        except FileNotFoundError:
            log(f"no usage feed yet at {USAGE_FILE} — is the status line hook installed?")
        except Exception as e:                           # noqa: BLE001
            log(f"usage feed unreadable ({e}); keeping empty")
        self._usage = d
        self.usageChanged.emit()

    # ── chat state ──────────────────────────────────────────────────────
    @Property(bool, notify=chatOpenChanged)
    def chatOpen(self):
        return self._chat

    @Property(int, constant=True)
    def winH(self):
        return self._win_h

    @Slot()
    def toggle(self) -> None:
        self.show() if not self._chat else self.hide()

    @Slot()
    def show(self) -> None:
        if self._chat:
            return
        self._chat = True
        self.chatOpenChanged.emit()
        self.setExpanded(True)
        self._show_terminal()

    @Slot()
    def hide(self) -> None:
        if not self._chat:
            return
        self._chat = False
        self.chatOpenChanged.emit()
        self.setExpanded(False)
        hide_window(TERM_CLASS)

    @Slot()
    def quit(self) -> None:
        QGuiApplication.quit()

    # ── terminal ────────────────────────────────────────────────────────
    def _terminal_running(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        # The notch may have been restarted while the terminal survived: look for
        # a process whose command line *starts* with the terminal binary and
        # carries our class. Anchored, so an editor with the theme file open
        # does not count.
        term = shlex.split(self.cfg["terminal"]["launch"])[0]
        r = subprocess.run(["pgrep", "-f", f"^{re.escape(os.path.basename(term))}\\b.*{TERM_CLASS}"],
                           capture_output=True)
        return r.returncode == 0

    def _terminal_rect(self) -> tuple[int, int, int, int]:
        self._geo = primary_geometry(self.cfg["screen"]["name"])   # monitors may have changed
        gx, gy, gw, gh = self._geo
        L = self.L
        wx, wy = gx + gw - L["width"], gy + (gh - self._win_h) // 2
        return (wx + L["pad"], wy + L["inset"] + L["pad"],
                L["width"] - L["strip"] - L["pad"], self._win_h - 2 * (L["inset"] + L["pad"]))

    def _launch_terminal(self) -> bool:
        a, t = self.cfg["agent"], self.cfg["terminal"]
        shell = os.environ.get("SHELL")
        if not shell:
            import pwd
            shell = pwd.getpwuid(os.getuid()).pw_shell or shutil.which("bash") or "/bin/sh"
        agent = a["command"]
        if shutil.which(shlex.split(agent)[0]) is None:
            warn(f"agent command '{agent}' not found on PATH")
        command = f"{agent}; exec {shell}" if a.get("keep_shell", True) else agent
        subst = {"{theme}": t["theme"], "{class}": TERM_CLASS, "{title}": "Claude Notch",
                 "{shell}": shell, "{command}": command}
        argv = [subst.get(tok, tok) for tok in shlex.split(t["launch"])]
        if shutil.which(argv[0]) is None:
            warn(f"terminal '{argv[0]}' not found — set [terminal].launch in {CONFIG_FILE}")
            self._notify("Claude Notch", f"Terminal '{argv[0]}' not found. Check your config.")
            return False
        workdir = Path(os.path.expanduser(a["workdir"]))
        if not workdir.is_dir():
            workdir = HOME
        log(f"launching terminal: {argv} in {workdir}")
        self._proc = subprocess.Popen(argv, cwd=str(workdir), start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True

    def _show_terminal(self) -> None:
        x, y, w, h = self._terminal_rect()
        fade = int(self.T["fade_ms"])
        if self._terminal_running():
            # Park it in place, invisible. QML calls revealTerminal() as soon as
            # the unfolding container covers this rectangle.
            place(TERM_CLASS, x, y, w, h, raise_it=True, hidden=True)
            return
        if not self._launch_terminal():
            self.hide()
            return
        # A fresh terminal maps its window after the container has long finished
        # unfolding, so it can fade in immediately; place it a few times in case
        # of a slow start.
        for ms in (1200, 2600, 5000):
            QTimer.singleShot(ms, lambda: self._chat and place(
                TERM_CLASS, x, y, w, h, raise_it=True, fade_ms=fade))

    @Slot()
    def revealTerminal(self) -> None:
        if self._chat:
            reveal(TERM_CLASS, int(self.T["fade_ms"]))

    @staticmethod
    def _notify(title: str, body: str) -> None:
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "Claude Notch", "-i", "dialog-warning", title, body])

    # ── own window ──────────────────────────────────────────────────────
    def attach_window(self, w) -> None:
        self._window = w
        self.setExpanded(False)

    def pin(self) -> None:
        gx, gy, gw, gh = self._geo
        place(APP_ID, gx + gw - self.L["width"], gy + (gh - self._win_h) // 2,
              self.L["width"], self._win_h)

    @Slot(bool)
    def setExpanded(self, expanded: bool) -> None:
        """Input region: resting → only the sliver's hover zone; hovering → the
        bubble and its details panel; chat → only the side strip (everything
        else belongs to the terminal on top)."""
        w = self._window
        if w is None:
            return
        L, W, H = self.L, self.L["width"], self._win_h
        if self._chat:
            region = QRegion(W - L["strip"], 0, L["strip"], H)
        elif expanded:
            hw = L["bubble_w"] + 10 + L["panel_w"] + 12
            hh = max(L["bubble_h"], 200) + 60
            region = QRegion(W - hw, (H - hh) // 2, hw, hh)
        else:
            hh = L["sliver_h"] + 28
            region = QRegion(W - L["sliver_hot"], (H - hh) // 2, L["sliver_hot"], hh)
        w.setMask(region)


# ── main ───────────────────────────────────────────────────────────────────
def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_ID)

    bus = QDBusConnection.sessionBus()
    if not bus.registerService(DBUS_SERVICE):
        warn("already running (D-Bus service taken); use `claude-notch toggle`")
        return 0

    cfg = load_config()
    bridge = Bridge(cfg)
    bus.registerObject(DBUS_PATH, bridge, QDBusConnection.RegisterOption.ExportAllSlots)

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("cfg", cfg)
    # The installer extracts the real mark from a locally installed Claude app
    # icon (never shipped in the repo); without it QML draws a procedural starburst.
    glyph = APP_DIR / "glyph.png"
    ctx.setContextProperty("glyphUrl", QUrl.fromLocalFile(str(glyph)).toString() if glyph.exists() else "")
    engine.load(QUrl.fromLocalFile(str(APP_DIR / "notch.qml")))
    if not engine.rootObjects():
        warn("failed to load notch.qml")
        return 1
    bridge.attach_window(engine.rootObjects()[0])

    # The compositor decides where a Wayland window goes; pin it (twice, in
    # case the first placement races the window mapping).
    QTimer.singleShot(1200, bridge.pin)
    QTimer.singleShot(3000, bridge.pin)
    # A terminal left behind by a previous instance would sit over the folded notch.
    QTimer.singleShot(1500, lambda: hide_window(TERM_CLASS))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
