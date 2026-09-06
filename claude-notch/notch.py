#!/usr/bin/env python3
"""Claude Notch — a screen-edge notch for Claude Code on KDE Plasma (Wayland).

Rest:  a thin sliver on the screen edge, coloured by how much of your plan is used.
Hover: it grows into a bubble with a usage ring and a details panel.
Click: the bubble unfolds into a terminal running your agent; click the strip to fold it back.
Orb:   the small arc below the notch opens a menu (sessions, project, monitor, width …).

The window is one fixed-size transparent Qt Quick surface; all morphing happens
inside it (60 fps QML animations). The compositor cannot let a Wayland client
position itself, so the notch and the terminal are placed through KWin's
scripting API. The input region (window mask) follows the visible shape, so a
resting notch only reacts to the sliver and the orb.
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

__version__ = "0.1.0"
REPO_URL = "https://github.com/Salic99/claude-notch"

APP_DIR = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "claude-notch"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))
CONFIG_FILE = CONFIG_DIR / "config.toml"
USAGE_FILE = CACHE_DIR / "claude-usage.json"
ACTIVITY_FILE = CACHE_DIR / "claude-notch-activity.json"   # written by the hooks
LOG_FILE = CACHE_DIR / "claude-notch.log"
KWIN_SCRIPT = CACHE_DIR / "claude-notch-kwin.js"
AUTOSTART_FILE = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "autostart" / "claude-notch.desktop"

APP_ID = "claude-notch"                     # Wayland app_id of the notch window
TERM_CLASS = "claude-notch-terminal"        # app_id of the chat terminal
WINDOW_CLASS = "claude-notch-window"        # app_id of a free-standing agent window
DBUS_SERVICE = "org.claudenotch.Notch"
DBUS_PATH = "/Notch"
TMUX_SOCKET = "claude-notch"                # own tmux server, apart from the user's sessions
TMUX_SESSION = "chat"
TMUX_CONF = CONFIG_DIR / "tmux.conf"

DEFAULTS = {
    "terminal": {
        "launch": "alacritty --config-file {theme} --class {class} -T {title} -e {shell} -c {command}",
        "theme": str(CONFIG_DIR / "alacritty.toml"),
    },
    "agent": {"command": "claude", "workdir": "~/Projects", "keep_shell": True,
              "tmux": True},        # run the agent inside tmux so the + bar can type into it
    "screen": {"name": "auto", "height_ratio": 0.94},
    "layout": {
        "width": 720, "strip": 46, "pad": 10, "inset": 26,
        "sliver_w": 5, "sliver_h": 132, "sliver_hot": 14,
        "bubble_w": 78, "bubble_h": 118, "panel_w": 300, "bar": 40,
    },
    "timing": {
        "fade_ms": 130, "collapse_delay_ms": 100,
        "stale_after_s": 1200, "data_refresh_ms": 15000,
    },
    "colors": {"background": "#0d0d0f", "ok": "#32d74b", "warn": "#ffd426",
               "crit": "#ff453a", "none": "#6b6b6b"},
    "ui": {"language": "auto"},
}
LANGUAGES = ("en", "cs")

VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv


def log(msg: str) -> None:
    if VERBOSE:
        print(f"[claude-notch] {msg}", file=sys.stderr, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


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
    raw = str(cfg["ui"]["language"])
    lang = raw
    if lang == "auto":
        lang = (QLocale.system().name() or os.environ.get("LANG") or "en")[:2].lower()
    cfg["ui"]["language_setting"] = raw
    cfg["ui"]["language"] = lang if lang in LANGUAGES else "en"
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_config(section: str, key: str, value) -> None:
    """Update one key in config.toml in place, keeping comments and layout.
    Creates the file from the example, or the section/key, if missing."""
    if not CONFIG_FILE.exists():
        example = APP_DIR.parent / "config" / "config.example.toml"
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(example.read_text() if example.exists() else "")
    lines = CONFIG_FILE.read_text().splitlines()
    out, in_sec, done, sec_end = [], False, False, None
    for i, ln in enumerate(lines):
        m = re.match(r"\s*\[([^\]]+)\]", ln)
        if m:
            if in_sec and not done:
                sec_end = len(out)
            in_sec = m.group(1).strip() == section
        elif in_sec and not done and re.match(rf"\s*{re.escape(key)}\s*=", ln):
            comment = ln.split("#", 1)[1] if "#" in ln.split("=", 1)[1] else None
            ln = f"{key} = {_toml_value(value)}" + (f"   #{comment}" if comment else "")
            done = True
        out.append(ln)
    if not done:
        if sec_end is not None:
            out.insert(sec_end, f"{key} = {_toml_value(value)}")
        elif in_sec:
            out.append(f"{key} = {_toml_value(value)}")
        else:
            out += ["", f"[{section}]", f"{key} = {_toml_value(value)}"]
    CONFIG_FILE.write_text("\n".join(out) + "\n")


# ── screens ────────────────────────────────────────────────────────────────
def list_outputs() -> list[dict]:
    """Outputs as [{name, geo, primary}] from kscreen-doctor; 'primary' is KDE's
    priority-1 output (what System Settings calls primary — Qt often disagrees)."""
    outs: list[dict] = []
    try:
        raw = subprocess.run(["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=8).stdout
        raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        cur = None
        for line in raw.splitlines():
            if line.startswith("Output:"):
                parts = line.split()
                cur = {"name": parts[2] if len(parts) > 2 else "?", "geo": None, "primary": False, "enabled": True}
                outs.append(cur)
            elif cur is not None:
                m = re.search(r"priority (\d+)", line)
                if m:
                    cur["primary"] = int(m.group(1)) == 1
                m = re.search(r"Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)", line)
                if m:
                    cur["geo"] = tuple(int(g) for g in m.groups())
                if re.search(r"\bdisabled\b", line):
                    cur["enabled"] = False
    except Exception as e:                               # noqa: BLE001
        log(f"kscreen-doctor unavailable ({e})")
    return [o for o in outs if o["geo"]]


def primary_geometry(name: str = "auto") -> tuple[int, int, int, int]:
    outs = list_outputs()
    for o in outs:
        if (name == "auto" and o["primary"]) or o["name"] == name:
            return o["geo"]
    if name != "auto" and outs:
        warn(f"output '{name}' not found; falling back to primary")
        for o in outs:
            if o["primary"]:
                return o["geo"]
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
_RAISE = "if (workspace.raiseWindow) workspace.raiseWindow(win); else workspace.activeWindow = win;"


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
    {_RAISE if raise_it else ""}
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


def raise_window(cls: str) -> None:
    run_kwin(f"""
workspace.windowList().forEach(function(win) {{
    if (win.resourceClass !== "{cls}" || win.minimized) return;
    {_RAISE}
}});
""")


# ── the bridge between Python and QML (also exported on D-Bus) ─────────────
@ClassInfo({"D-Bus Interface": DBUS_SERVICE})
class Bridge(QObject):
    usageChanged = Signal()
    chatOpenChanged = Signal()
    settingsChanged = Signal()
    geomChanged = Signal()
    activityChanged = Signal()
    menuRequested = Signal()
    plusRequested = Signal()
    detailsRequested = Signal()

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.L = cfg["layout"]
        self.T = cfg["timing"]
        self._usage = self._empty()
        self._window = None
        self._engine = None
        self._pending_rects = None
        self._chat = False
        self._proc: subprocess.Popen | None = None
        self._workdir = self._resolve_workdir(cfg["agent"]["workdir"])
        self._continue = False
        self._geo = primary_geometry(cfg["screen"]["name"])
        self._win_h = int(self._geo[3] * cfg["screen"]["height_ratio"])

        self._data_timer = QTimer(self)
        self._data_timer.timeout.connect(self.reload)
        self._data_timer.start(int(self.T["data_refresh_ms"]))
        self.reload()

        # Session activity (busy / waiting / idle) from the Claude Code hooks —
        # polled every second: it is a tiny file and drives a live animation.
        self._activity = "idle"
        self._act_timer = QTimer(self)
        self._act_timer.timeout.connect(self._poll_activity)
        self._act_timer.start(1000)
        self._poll_activity()

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

    # ── session activity (from hooks) ───────────────────────────────────
    @Property(str, notify=activityChanged)
    def activity(self):
        return self._activity

    def _poll_activity(self) -> None:
        """Aggregate all sessions: any fresh 'waiting' wins, then any fresh
        'busy'; a 'busy' older than 90 s is treated as idle in case the Stop
        hook never arrived."""
        state = "idle"
        try:
            now = time.time()
            for rec in (json.loads(ACTIVITY_FILE.read_text()) or {}).values():
                st, at = rec.get("state"), rec.get("at", 0)
                if st == "waiting" and now - at < 3600:
                    state = "waiting"; break
                if st == "busy" and now - at < 90:
                    state = "busy"
        except FileNotFoundError:
            pass
        except Exception as e:                           # noqa: BLE001
            log(f"activity file unreadable ({e})")
        if state != self._activity:
            self._activity = state
            log(f"activity -> {state}")
            self.activityChanged.emit()

    # ── chat state ──────────────────────────────────────────────────────
    @Property(bool, notify=chatOpenChanged)
    def chatOpen(self):
        return self._chat

    @Property(int, notify=geomChanged)
    def winH(self):
        return self._win_h

    @Property(str, notify=settingsChanged)
    def lang(self):
        return self.cfg["ui"]["language"]

    @Slot()
    def toggle(self) -> None:
        self.show() if not self._chat else self.hide()

    @Slot()
    def show(self) -> None:
        if self._chat:
            return
        self._chat = True
        self.chatOpenChanged.emit()
        self._show_terminal()

    @Slot()
    def hide(self) -> None:
        if not self._chat:
            return
        self._chat = False
        self.chatOpenChanged.emit()
        hide_window(TERM_CLASS)

    @Slot()
    def quit(self) -> None:
        self._kill_terminal()
        self._kill_session()
        QGuiApplication.quit()

    # ── the "+" bar under the chat ───────────────────────────────────────
    @Slot()
    def plus(self) -> None:
        """Toggle the + popup (files, folder, connectors, plugins); opens the chat first."""
        if not self._chat:
            self.show()
            QTimer.singleShot(600, self.plusRequested.emit)
        else:
            self.plusRequested.emit()

    def _pick(self, args: list[str], then) -> None:
        """Run a kdialog picker off the GUI thread, hand its lines to `then` on it."""
        if shutil.which("kdialog") is None:
            self._notify("Claude Notch", "kdialog is needed for the file picker.")
            return
        def worker():
            r = subprocess.run(["kdialog", "--title", "Claude Notch", *args], capture_output=True, text=True)
            lines = [ln for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []
            QTimer.singleShot(0, lambda: then(lines))
        threading.Thread(target=worker, daemon=True).start()

    def _mention(self, paths: list[str]) -> None:
        if paths and self._type("".join(f"@{p} " for p in paths)):
            raise_window(TERM_CLASS)

    @Slot()
    def addFiles(self) -> None:
        self._pick(["--multiple", "--separate-output", "--getopenfilename", str(self._workdir)], self._mention)

    @Slot()
    def addFolder(self) -> None:
        self._pick(["--getexistingdirectory", str(self._workdir)],
                   lambda d: self._mention([p.rstrip("/") + "/" for p in d]))

    @Slot(str)
    def sendCommand(self, cmd: str) -> None:
        if self._type(cmd, enter=True):
            raise_window(TERM_CLASS)

    @Slot("QVariant")
    def reportState(self, st) -> None:
        if hasattr(st, "toVariant"):          # a JS object arrives as QJSValue
            st = st.toVariant()
        self._ui_state = dict(st) if isinstance(st, dict) else {}

    @Slot(result=str)
    def state(self) -> str:
        """UI state as JSON — `claude-notch state`; handy when reporting bugs."""
        return json.dumps({"chat": self._chat, "workdir": str(self._workdir),
                           "terminal": self._terminal_running(),
                           "screen": self.cfg["screen"]["name"], "lang": self.cfg["ui"]["language"],
                           "geo": list(self._geo), "width": int(self.L["width"]),
                           "activity": self._activity,
                           "usage_fiveHour": self._usage.get("fiveHour"),
                           "usage_writtenAt": self._usage.get("writtenAt"),
                           "mask": getattr(self, "_mask_rects", None),
                           **getattr(self, "_ui_state", {})})

    @Slot()
    def menu(self) -> None:
        """Open/close the orb menu (also reachable as `claude-notch menu`)."""
        self.menuRequested.emit()

    @Slot()
    def details(self) -> None:
        """Pin/unpin the usage bubble with its details (`claude-notch details`)."""
        self.detailsRequested.emit()

    # ── terminal ────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_workdir(path: str) -> Path:
        p = Path(os.path.expanduser(path))
        return p if p.is_dir() else HOME

    def _term_bin(self) -> str:
        return os.path.basename(shlex.split(self.cfg["terminal"]["launch"])[0])

    def _terminal_running(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        # The notch may have been restarted while the terminal survived: look for
        # a process whose command line *starts* with the terminal binary and
        # carries our class. Anchored, so an editor with the theme file open
        # does not count.
        r = subprocess.run(["pgrep", "-f", f"^{re.escape(self._term_bin())}\\b.*{TERM_CLASS}"],
                           capture_output=True)
        return r.returncode == 0

    def _kill_terminal(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        subprocess.run(["pkill", "-f", f"^{re.escape(self._term_bin())}\\b.*{TERM_CLASS}"], capture_output=True)
        self._proc = None

    def _terminal_rect(self) -> tuple[int, int, int, int]:
        self._geo = primary_geometry(self.cfg["screen"]["name"])   # monitors may have changed
        gx, gy, gw, gh = self._geo
        L = self.L
        wx, wy = gx + gw - L["width"], gy + (gh - self._win_h) // 2
        return (wx + L["pad"], wy + L["inset"] + L["pad"],
                L["width"] - L["strip"] - L["pad"], self._win_h - 2 * (L["inset"] + L["pad"]) - L["bar"])

    def _shell(self) -> str:
        shell = os.environ.get("SHELL")
        if not shell:
            import pwd
            shell = pwd.getpwuid(os.getuid()).pw_shell or shutil.which("bash") or "/bin/sh"
        return shell

    def _agent_command(self) -> str:
        agent = self.cfg["agent"]["command"]
        if self._continue and shlex.split(agent)[0] == "claude":
            agent += " --continue"
        self._continue = False
        return agent

    # ── tmux: the panel's agent runs inside a private tmux server ────────
    def _tmux_on(self) -> bool:
        return bool(self.cfg["agent"].get("tmux", True)) and shutil.which("tmux") is not None

    @staticmethod
    def _tmux(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["tmux", "-L", TMUX_SOCKET, *args], capture_output=True, text=True)

    def _session_alive(self) -> bool:
        return self._tmux_on() and self._tmux("has-session", "-t", TMUX_SESSION).returncode == 0

    def _kill_session(self) -> None:
        if self._tmux_on():
            self._tmux("kill-session", "-t", TMUX_SESSION)

    def _type(self, text: str, enter: bool = False) -> bool:
        """Type text into the agent as if at the keyboard (needs tmux)."""
        if not self._session_alive():
            self._notify("Claude Notch", "No chat session to type into." if self._tmux_on()
                         else "Typing into the chat needs tmux (sudo pacman -S tmux).")
            return False
        self._tmux("send-keys", "-t", TMUX_SESSION, "-l", text)
        if enter:
            time.sleep(0.08)                  # let the slash-command menu settle first
            self._tmux("send-keys", "-t", TMUX_SESSION, "Enter")
        return True

    def _build_argv(self, cls: str, with_theme: bool) -> list[str] | None:
        a, t = self.cfg["agent"], self.cfg["terminal"]
        shell = self._shell()
        agent = self._agent_command()
        if shutil.which(shlex.split(agent)[0]) is None:
            warn(f"agent command '{agent}' not found on PATH")
        command = f"{agent}; exec {shell}" if a.get("keep_shell", True) else agent
        if cls == TERM_CLASS and self._tmux_on():
            # Attach to the surviving session if there is one, else start the agent in a new one.
            conf = [] if not TMUX_CONF.is_file() else ["-f", str(TMUX_CONF)]
            command = shlex.join(["tmux", "-L", TMUX_SOCKET, *conf, "new-session", "-A", "-s", TMUX_SESSION,
                                  "-c", str(self._workdir), shell, "-c", command])
        subst = {"{theme}": t["theme"], "{class}": cls, "{title}": "Claude Notch",
                 "{shell}": shell, "{command}": command}
        tokens = shlex.split(t["launch"])
        if not with_theme:   # a free-standing window keeps the user's own terminal look
            tokens = [tok for i, tok in enumerate(tokens)
                      if tok != "{theme}" and not (tok.startswith("--config") and i + 1 < len(tokens) and tokens[i + 1] == "{theme}")]
        argv = [subst.get(tok, tok) for tok in tokens]
        if shutil.which(argv[0]) is None:
            warn(f"terminal '{argv[0]}' not found — set [terminal].launch in {CONFIG_FILE}")
            self._notify("Claude Notch", f"Terminal '{argv[0]}' not found. Check your config.")
            return None
        return argv

    def _launch_terminal(self) -> bool:
        argv = self._build_argv(TERM_CLASS, with_theme=True)
        if argv is None:
            return False
        log(f"launching terminal: {argv} in {self._workdir}")
        # If the notch itself was (re)started from inside a Claude Code session,
        # do not let the panel's agent inherit that session's environment.
        env = {k: v for k, v in os.environ.items() if not (k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_"))}
        self._proc = subprocess.Popen(argv, cwd=str(self._workdir), start_new_session=True, env=env,
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

    @Slot()
    def raiseNotch(self) -> None:
        raise_window(APP_ID)

    @Slot()
    def raiseTerminal(self) -> None:
        if self._chat:
            raise_window(TERM_CLASS)

    # ── menu actions ────────────────────────────────────────────────────
    @Slot()
    def newSession(self) -> None:
        self._restart_session(cont=False)

    @Slot()
    def continueSession(self) -> None:
        self._restart_session(cont=True)

    def _restart_session(self, cont: bool) -> None:
        self._kill_terminal()
        self._kill_session()
        self._continue = cont
        if self._chat:
            QTimer.singleShot(250, self._show_terminal)
        else:
            QTimer.singleShot(250, self.show)

    @Slot()
    def openWindow(self) -> None:
        argv = self._build_argv(WINDOW_CLASS, with_theme=False)
        if argv:
            subprocess.Popen(argv, cwd=str(self._workdir), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @Property("QVariant", notify=settingsChanged)
    def projects(self):
        """The configured workdir first, then its sub-folders by recency."""
        base = self._resolve_workdir(self.cfg["agent"]["workdir"])
        items = [{"name": base.name or str(base), "path": str(base), "current": base == self._workdir}]
        try:
            subs = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
            subs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            items += [{"name": p.name, "path": str(p), "current": p == self._workdir} for p in subs[:8]]
        except Exception:                                # noqa: BLE001
            pass
        return items

    @Slot(str)
    def setProject(self, path: str) -> None:
        self._workdir = self._resolve_workdir(path)
        self.settingsChanged.emit()
        self._restart_session(cont=False)

    @Property("QVariant", notify=settingsChanged)
    def outputs(self):
        want = self.cfg["screen"]["name"]
        return [{"name": o["name"], "label": o["name"] + (" (primary)" if o["primary"] else ""),
                 "current": (want == "auto" and o["primary"]) or want == o["name"]}
                for o in list_outputs()] + [{"name": "auto", "label": "auto", "current": want == "auto"}]

    @Slot(str)
    def setScreen(self, name: str) -> None:
        set_config("screen", "name", name); self.reloadConfig()

    @Property(int, notify=geomChanged)
    def panelWidth(self):
        return int(self.L["width"])

    @Slot(int)
    def setWidth(self, px: int) -> None:
        set_config("layout", "width", int(px)); self.reloadConfig()

    @Property(bool, notify=settingsChanged)
    def autostart(self):
        return AUTOSTART_FILE.exists()

    @Slot(bool)
    def setAutostart(self, on: bool) -> None:
        if on:
            launcher = shutil.which("claude-notch") or str(HOME / ".local/bin/claude-notch")
            AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text("[Desktop Entry]\nType=Application\nName=Claude Notch\n"
                                      "Comment=Claude Code usage notch on the screen edge\n"
                                      f"Exec={launcher} start\nIcon=utilities-terminal\nTerminal=false\n"
                                      "X-KDE-autostart-phase=2\n")
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)
        self.settingsChanged.emit()

    @Property(str, notify=settingsChanged)
    def languageSetting(self):
        return self.cfg["ui"]["language_setting"]

    @Slot(str)
    def setLanguage(self, code: str) -> None:
        set_config("ui", "language", code); self.reloadConfig()

    @Slot()
    def openConfig(self) -> None:
        if not CONFIG_FILE.exists():
            set_config("ui", "language", self.cfg["ui"]["language_setting"])
        subprocess.Popen(["xdg-open", str(CONFIG_FILE)], start_new_session=True)

    @Slot()
    def openLog(self) -> None:
        LOG_FILE.touch(exist_ok=True)
        subprocess.Popen(["xdg-open", str(LOG_FILE)], start_new_session=True)

    @Slot()
    def openRepo(self) -> None:
        subprocess.Popen(["xdg-open", REPO_URL], start_new_session=True)

    @Property(str, constant=True)
    def version(self):
        return __version__

    @Slot()
    def reloadConfig(self) -> None:
        """Re-read config.toml and apply it live: geometry, colours, language,
        layout — no process restart (execv races the Wayland window and the
        D-Bus name). Also the target of `claude-notch reload` / the menu's
        Reload item, e.g. after editing the file by hand."""
        cfg = load_config()
        self.cfg = cfg
        self.L = cfg["layout"]
        self.T = cfg["timing"]
        self._geo = primary_geometry(cfg["screen"]["name"])
        self._win_h = int(self._geo[3] * cfg["screen"]["height_ratio"])
        self._workdir = self._resolve_workdir(cfg["agent"]["workdir"])
        self._data_timer.start(int(self.T["data_refresh_ms"]))
        # push the fresh cfg into QML (layout/colours/timing read from it directly)
        if self._engine is not None:
            self._engine.rootContext().setContextProperty("cfg", cfg)
        self.geomChanged.emit()
        self.settingsChanged.emit()
        self.pin()
        if self._chat:
            x, y, w, h = self._terminal_rect()
            place(TERM_CLASS, x, y, w, h, raise_it=True)

    @Slot()
    def restart(self) -> None:      # alias kept for the menu and older callers
        self.reloadConfig()

    @staticmethod
    def _notify(title: str, body: str) -> None:
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "Claude Notch", "-i", "dialog-warning", title, body])

    # ── own window ──────────────────────────────────────────────────────
    def attach_window(self, w, engine=None) -> None:
        self._window = w
        self._engine = engine
        if self._pending_rects is not None:
            self.applyMask(self._pending_rects)

    def pin(self) -> None:
        gx, gy, gw, gh = self._geo
        x, y = gx + gw - self.L["width"], gy + (gh - self._win_h) // 2
        log(f"pin -> {x},{y} {self.L['width']}x{self._win_h}  (geo={self._geo})")
        place(APP_ID, x, y, self.L["width"], self._win_h)

    @Slot("QVariantList")
    def applyMask(self, rects) -> None:
        """Input region of the notch window, as a list of [x, y, w, h] computed
        by QML from the current state (sliver / bubble / chat strip / orb / menu)."""
        if self._window is None:
            self._pending_rects = list(rects)
            return
        region = QRegion()
        self._mask_rects = []
        for r in rects:
            x, y, w, h = (int(v) for v in r)
            self._mask_rects.append([x, y, w, h])
            region = region.united(QRegion(x, y, max(1, w), max(1, h)))
        self._window.setMask(region)


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
    bridge.attach_window(engine.rootObjects()[0], engine)

    # The compositor decides where a Wayland window goes; pin it (twice, in
    # case the first placement races the window mapping).
    QTimer.singleShot(1200, bridge.pin)
    QTimer.singleShot(3000, bridge.pin)
    # A terminal left behind by a previous instance would sit over the folded notch.
    QTimer.singleShot(1500, lambda: hide_window(TERM_CLASS))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
