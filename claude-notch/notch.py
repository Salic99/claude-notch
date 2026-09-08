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
import signal
import subprocess
import sys
import threading
import time
from array import array
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:          # Python < 3.11
    tomllib = None

from PySide6.QtCore import ClassInfo, Property, QLocale, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtDBus import QDBusConnection
from PySide6.QtGui import QGuiApplication, QImage, QRegion
from PySide6.QtQml import QQmlApplicationEngine

__version__ = "0.1.0"
REPO_URL = "https://github.com/Salic99/claude-notch"

APP_DIR = Path(__file__).resolve().parent
HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "claude-notch"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache"))
CONFIG_FILE = CONFIG_DIR / "config.toml"
USAGE_FILE = CACHE_DIR / "claude-usage.json"
PANEL_FILE = CACHE_DIR / "claude-notch-panel.json"   # the same feed, from the panel's own session
ACTIVITY_FILE = CACHE_DIR / "claude-notch-activity.json"   # written by the hooks
LOG_FILE = CACHE_DIR / "claude-notch.log"
KWIN_SCRIPT = CACHE_DIR / "claude-notch-kwin.js"
MODELS_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "claude-notch" / "models"
VOICES_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "claude-notch" / "voices"
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
              "tmux": True,         # run the agent inside tmux so the bar can type into it
              # the bar's model picker: [id, label]; picking one types `/model <id>`
              "models": [["claude-fable-5-1", "Fable 5.1"], ["claude-opus-5", "Opus 5"],
                         ["claude-sonnet-5", "Sonnet 5"], ["claude-haiku-4-5-20251001", "Haiku 4.5"],
                         ["default", "Default"]]},
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
    "voice": {                                  # dictation: the mic in the bar
        "model": "auto",        # a ggml whisper model, or "auto" = newest *.bin in MODELS_DIR
        "language": "auto",     # whisper language code; "auto" detects
        "record": "pw-record --rate 16000 --channels 1 --format s16 {file}",
        "source": "auto",       # PipeWire source (microphone) name; "auto" = the system default
        # What the mic in the bar does: "conversation" — hands-free, each thing you say is
        # transcribed and sent, the answer read aloud; "dictation" — click, talk, click, edit, send.
        "mode": "conversation",
        "silence_ms": 900,      # conversation: this much quiet ends what you were saying
        "start_level": 0.05,    # conversation: input peak (0–1) that counts as speech …
        "end_level": 0.03,      # … and below which it counts as quiet
        "max_utterance_s": 30,
    },
    "speech": {                                 # Claude reads its answers aloud (the speaker in the bar)
        "enabled": False,
        "voice": "auto",        # a piper voice name in VOICES_DIR (e.g. "cs_CZ-jirka-medium"); "auto" picks by language
        "max_chars": 700,       # longer answers are cut at a sentence end
        "rate": 1.0,            # speaking speed; 1.2 = a fifth faster
        "piper": "piper",       # the piper CLI (uv tool install piper-tts)
        "play_raw": "pw-play --raw --rate {rate} --channels 1 --format s16 -",   # piper streams into this
        # Another engine instead of piper: a command that writes {file} from {text}
        # (the text is also on stdin), e.g. edge-tts --voice cs-CZ-AntoninNeural --text {text} --write-media {file}
        "synth": "",
        "play": "pw-play {file}",
    },
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


_kwin_done: list[tuple[float, Path]] = []    # (when, file) of jobs already handed to KWin
_kwin_ack = threading.Event()                  # set by Bridge.kwinDone when the job's script has run
_kwin_ack_seq = 0


def _kwin_cleanup() -> None:
    """Delete script files of jobs that ran a while ago. KWin reads the file on
    a worker thread after `run` returns, so deleting right away loses the job
    whenever KWin is busy. The script objects are left loaded on purpose: KWin
    hands out ids as scripts.size(), so unloading one makes a later id collide
    with a live script's D-Bus path."""
    now = time.time()
    keep = []
    for when, path in _kwin_done:
        if now - when > 2.0:
            path.unlink(missing_ok=True)
        else:
            keep.append((when, path))
    _kwin_done[:] = keep


def _qdbus(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["qdbus6", "org.kde.KWin", *args], capture_output=True, text=True, timeout=8)


def _kwin_worker() -> None:
    """Every script ends by calling back into the notch (callDBus → kwinDone),
    so a job counts as done only once KWin has actually run it. Jobs therefore
    execute strictly in order, and one that got lost — KWin ran a different
    script under that id, or never read the file — is retried through
    Scripting.start(), which runs every loaded script that is not running yet."""
    global _seq, _kwin_ack_seq
    while True:
        with _kwin_lock:
            if not _kwin_jobs:
                return
            js = _kwin_jobs[0]
        _seq += 1
        path = KWIN_SCRIPT.with_name(f"claude-notch-kwin-{os.getpid()}-{_seq}.js")
        path.parent.mkdir(parents=True, exist_ok=True)
        # A script that KWin only gets round to running much later (a retry
        # through Scripting.start() picks up every script it never ran) must not
        # act on a state long gone: it just reports in and stops.
        stale = int((time.time() + 1.5) * 1000)
        path.write_text(f'if (Date.now() > {stale}) {{ callDBus("{DBUS_SERVICE}", "{DBUS_PATH}", "{DBUS_SERVICE}", "kwinDone", {_seq}); }} else {{\n'
                        + js + f'\ncallDBus("{DBUS_SERVICE}", "{DBUS_PATH}", "{DBUS_SERVICE}", "kwinDone", {_seq}); }}\n')
        name = f"claudenotch{os.getpid()}x{_seq}"
        t0 = time.time()
        try:
            _kwin_cleanup()
            ld = _qdbus("/Scripting", "org.kde.kwin.Scripting.loadScript", str(path), name)
            sid = ld.stdout.strip()
            if not sid or sid == "-1":
                warn(f"KWin refused the script (is this a KDE Plasma Wayland session?) {ld.stderr.strip()[:120]}")
                path.unlink(missing_ok=True)
                continue
            _kwin_ack_seq = _seq
            _kwin_ack.clear()
            _qdbus(f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
            if not _kwin_ack.wait(0.8):
                log(f"kwin job {_seq}: Script{sid}.run did not reach it — retrying via Scripting.start")
                _qdbus("/Scripting", "org.kde.kwin.Scripting.start")
                if not _kwin_ack.wait(1.5):
                    warn(f"kwin job {_seq} lost: {' '.join(js.split())[:80]}")
            elif VERBOSE:
                log(f"kwin job {_seq}: done in {int((time.time() - t0) * 1000)} ms — {' '.join(js.split())[:70]}")
            _kwin_done.append((time.time(), path))
        except Exception as e:                           # noqa: BLE001
            warn(f"cannot talk to KWin: {e}")
            path.unlink(missing_ok=True)
        finally:
            with _kwin_lock:
                _kwin_jobs.pop(0)


_PIN = ("win.keepAbove = true; win.skipTaskbar = true; "
        "win.skipPager = true; win.skipSwitcher = true;")
_RAISE = "if (workspace.raiseWindow) workspace.raiseWindow(win); else workspace.activeWindow = win;"
# The notch sits above the terminal for good and leaves the terminal's rectangle
# open (paint and input) while the chat is up — so no window has to be restacked
# for a popup. Whenever the terminal is raised, the notch goes back on top.
_ABOVE = (f'workspace.windowList().forEach(function(n) {{ if (n.resourceClass === "{APP_ID}") '
          '{ if (workspace.raiseWindow) workspace.raiseWindow(n); else workspace.activeWindow = n; } });')
_SHOWN = f'callDBus("{DBUS_SERVICE}", "{DBUS_PATH}", "{DBUS_SERVICE}", "terminalMapped");'


def _fade_js(ms: int, then: str = "") -> str:
    steps = max(3, round(ms / 16))
    return f"""
    var t = new QTimer(); t.interval = 16; var step = 0;
    t.timeout.connect(function() {{
        step++; var p = Math.min(1, step / {steps});
        win.opacity = p * p * (3 - 2 * p);
        if (p >= 1) {{ t.stop(); {then} }}
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
    {_fade_js(fade_ms, _SHOWN if cls == TERM_CLASS else "") if fade_ms else ""}
}});
{_ABOVE if raise_it and cls != APP_ID else ""}
""")


def place_on_map(cls: str, x: int, y: int, w: int, h: int, *, fade_ms: int,
                 timeout_ms: int = 20000) -> None:
    """Catch a window the instant KWin maps it and put it in place before its
    first frame is shown, so a freshly launched terminal never flashes at
    KWin's default (centred) position. Also handles a window that mapped
    before the script loaded. The hook disarms itself after timeout_ms."""
    run_kwin(f"""
var done = false;
function grab(win) {{
    if (done || !win || win.resourceClass !== "{cls}") return;
    done = true;
    {_PIN}
    win.opacity = 0;
    if (win.minimized) win.minimized = false;
    win.frameGeometry = {{ x: {x}, y: {y}, width: {w}, height: {h} }};
    {_RAISE}
    {_ABOVE if cls != APP_ID else ""}
    {_fade_js(fade_ms, _SHOWN if cls == TERM_CLASS else "")}
}}
workspace.windowAdded.connect(grab);
workspace.windowList().forEach(grab);
var stop = new QTimer(); stop.interval = {timeout_ms}; stop.singleShot = true;
stop.timeout.connect(function() {{ workspace.windowAdded.disconnect(grab); }});
stop.start();
""")


def reveal(cls: str, fade_ms: int) -> None:
    """Fade a parked window in. Called by QML the moment the container fully
    covers the terminal's rectangle, so the terminal never shows over the desktop."""
    run_kwin(f"""
workspace.windowList().forEach(function(win) {{
    if (win.resourceClass !== "{cls}") return;
    if (win.minimized) win.minimized = false;
    {_fade_js(fade_ms, _SHOWN if cls == TERM_CLASS else "")}
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
{_ABOVE if cls != APP_ID else ""}
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
    barRequested = Signal(str)             # "plus" | "project" | "model"
    detailsRequested = Signal()
    voiceChanged = Signal()
    terminalShownChanged = Signal()
    speechChanged = Signal()
    _voiceResult = Signal(str)             # from the transcription thread to the GUI thread
    _convResult = Signal(str)              # the same, for the conversation
    _speechDone = Signal(bool)             # from the speech thread

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
        self._term_shown = False               # KWin has faded the terminal in (see _SHOWN)
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
        self._panel_activity = ""
        self._act_timer = QTimer(self)
        self._act_timer.timeout.connect(self._poll_activity)
        self._act_timer.start(1000)
        self._poll_activity()

        # Dictation: pw-record into a wav, whisper-cli on it, typed into the chat.
        self._voice_state = ""                 # "", "recording", "transcribing"
        self._voice_level = 0.0
        self._voice_peak = 0.0
        self._rec: subprocess.Popen | None = None
        self._rec_file: Path | None = None
        self._rec_offset = 0
        self._rec_started = 0.0
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(50)
        self._level_timer.timeout.connect(self._poll_level)
        self._voiceResult.connect(self._voice_done)
        self._convResult.connect(self._conv_result)
        # Conversation: hands-free turns. Segments are cut out of the running
        # recording by input level; the mic is deaf while Claude works or talks.
        self._conv = False
        self._conv_speech_on = False           # we switched speech on for the conversation
        self._seg_start: int | None = None
        self._speech_since = 0.0
        self._silence_since = 0.0
        self._loud_ticks = 0
        self._speechDone.connect(self._speech_finished)

        # Speech: the last answer of the panel session, read by piper.
        self._speaking = False
        self._speech_procs: list[subprocess.Popen] = []
        self._speech_seq = 0

    # ── usage feed ──────────────────────────────────────────────────────
    @staticmethod
    def _empty() -> dict:
        return {"fiveHour": -1, "sevenDay": -1, "fiveReset": 0, "sevenReset": 0,
                "writtenAt": 0, "model": "", "ctx": -1, "panelSession": "", "cwd": "", "now": time.time()}

    @Property("QVariant", notify=usageChanged)
    def usage(self):
        return self._usage

    @Slot()
    def reload(self) -> None:
        d = self._empty()
        raw = {}
        try:
            raw = json.loads(USAGE_FILE.read_text())
            rl = raw.get("rate_limits") or {}
            fh, sd = rl.get("five_hour") or {}, rl.get("seven_day") or {}
            if fh.get("used_percentage") is not None:       # keys may be present but null
                d["fiveHour"] = round(fh["used_percentage"]); d["fiveReset"] = fh.get("resets_at") or 0
            if sd.get("used_percentage") is not None:
                d["sevenDay"] = round(sd["used_percentage"]); d["sevenReset"] = sd.get("resets_at") or 0
            d["writtenAt"] = raw.get("_at", 0)
        except FileNotFoundError:
            log(f"no usage feed yet at {USAGE_FILE} — is the status line hook installed?")
        except Exception as e:                           # noqa: BLE001
            log(f"usage feed unreadable ({e}); keeping empty")
        # Model and context are per session: prefer the panel's own snapshot
        # (written while CLAUDE_NOTCH_PANEL is set) over whichever session wrote last.
        src = raw
        try:
            panel = json.loads(PANEL_FILE.read_text())
            if panel.get("session_id"):
                src = panel; d["panelSession"] = panel["session_id"]; d["cwd"] = panel.get("cwd") or ""
        except FileNotFoundError:
            pass
        except Exception as e:                           # noqa: BLE001
            log(f"panel feed unreadable ({e})")
        d["model"] = (src.get("model") or {}).get("display_name", "")
        cw = src.get("context_window") or {}
        if cw.get("used_percentage") is not None:
            d["ctx"] = round(cw["used_percentage"])
        self._usage = d
        self.usageChanged.emit()

    @Property("QVariant", notify=usageChanged)
    def models(self):
        """The bar's model picker, from [agent].models; the current one by the feed's display name."""
        cur = self._usage.get("model", "")
        out = []
        for m in self.cfg["agent"].get("models") or []:
            mid, name = (str(m[0]), str(m[1])) if isinstance(m, (list, tuple)) and len(m) > 1 else (str(m), str(m))
            out.append({"id": mid, "name": name, "current": bool(cur) and cur in (name, mid)})
        return out

    @Slot(str)
    def setModel(self, model_id: str) -> None:
        self.sendCommand(f"/model {model_id}")

    # ── session activity (from hooks) ───────────────────────────────────
    @Property(str, notify=activityChanged)
    def activity(self):
        return self._activity

    @Property(str, notify=activityChanged)
    def panelActivity(self):
        """The panel session's own state, "" while unknown (drives the bar's Stop)."""
        return self._panel_activity

    @staticmethod
    def _fresh(rec: dict, now: float) -> str:
        st, at = rec.get("state"), rec.get("at", 0)
        if st == "waiting" and now - at < 3600:
            return "waiting"
        if st == "busy" and now - at < 90:
            return "busy"
        return "idle"

    def _poll_activity(self) -> None:
        """Aggregate all sessions: any fresh 'waiting' wins, then any fresh
        'busy'; a 'busy' older than 90 s is treated as idle in case the Stop
        hook never arrived."""
        state, here = "idle", ""
        try:
            now = time.time()
            recs = json.loads(ACTIVITY_FILE.read_text()) or {}
            for rec in recs.values():
                st = self._fresh(rec, now)
                if st == "waiting":
                    state = "waiting"; break
                if st == "busy":
                    state = "busy"
            sid = self._usage.get("panelSession")
            if sid and sid in recs:
                here = self._fresh(recs[sid], now)
        except FileNotFoundError:
            pass
        except Exception as e:                           # noqa: BLE001
            log(f"activity file unreadable ({e})")
        if state != self._activity or here != self._panel_activity:
            if state != self._activity:
                log(f"activity -> {state}")
            self._activity, self._panel_activity = state, here
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
        self._set_term_shown(False)
        self.chatOpenChanged.emit()
        hide_window(TERM_CLASS)

    @Slot()
    def quit(self) -> None:          # the chat (terminal + tmux session) outlives the notch
        self._stop_recorder()        # … but not the microphone
        self.speakStop()
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

    @Slot(str)
    def bar(self, which: str) -> None:
        """Toggle one of the bar's popups by name (`claude-notch bar model`)."""
        if which not in ("plus", "project", "model"):
            return
        if not self._chat:
            self.show()
            QTimer.singleShot(600, lambda: self.barRequested.emit(which))
        else:
            self.barRequested.emit(which)

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

    @Slot()
    def interrupt(self) -> None:
        """Escape into the agent — Claude Code stops the running turn."""
        if self._session_alive():
            self._tmux("send-keys", "-t", TMUX_SESSION, "Escape")

    @Slot(str)
    def add(self, items: str) -> None:
        """Mention files in the chat: newline-separated paths or file:// URLs
        (drag-and-drop from QML, `claude-notch add <file>...` from the shell)."""
        paths = []
        for it in items.splitlines():
            it = it.strip()
            if not it:
                continue
            u = QUrl(it)
            p = u.toLocalFile() if u.isLocalFile() else it
            p = os.path.abspath(os.path.expanduser(p))
            paths.append(p.rstrip("/") + "/" if os.path.isdir(p) else p)
        self._mention(paths)

    @Slot()
    def pasteImage(self) -> None:
        """Save the image on the clipboard to a file and mention it."""
        out = CACHE_DIR / "claude-notch" / "clips"
        out.mkdir(parents=True, exist_ok=True)
        path = out / time.strftime("clip-%Y%m%d-%H%M%S.png")
        ok = False
        if shutil.which("wl-paste"):            # reads the clipboard without owning focus
            r = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True)
            types = [t for t in r.stdout.split() if t.startswith("image/")]
            if types:
                mime = "image/png" if "image/png" in types else types[0]
                with open(path, "wb") as f:
                    ok = subprocess.run(["wl-paste", "-t", mime], stdout=f).returncode == 0
                if ok and mime != "image/png":
                    img = QImage(str(path)); ok = not img.isNull() and img.save(str(path), "PNG")
        if not ok:                              # Qt: works when the notch has been focused recently
            img = QGuiApplication.clipboard().image()
            ok = not img.isNull() and img.save(str(path), "PNG")
        if not ok:
            path.unlink(missing_ok=True)
            self._notify("Claude Notch", "No image on the clipboard." + ("" if shutil.which("wl-paste") else
                         " Install wl-clipboard for reliable clipboard access."))
            return
        self._mention([str(path)])

    # ── dictation ───────────────────────────────────────────────────────
    @Property(str, notify=voiceChanged)
    def voiceState(self):
        return self._voice_state

    @Property(float, notify=voiceChanged)
    def micLevel(self):
        return self._voice_level

    @Property(bool, notify=settingsChanged)
    def voiceReady(self):
        return self._voice_missing() is None

    @Property(bool, notify=voiceChanged)
    def conversation(self):
        return self._conv

    @Property(str, notify=settingsChanged)
    def micMode(self):
        return str(self.cfg["voice"].get("mode", "conversation") or "conversation")

    @Slot()
    def micTapped(self) -> None:
        """The mic in the bar: the conversation on/off, or one-shot dictation, by
        [voice].mode. While the notch talks, a tap just quiets it."""
        if self._speaking:
            self.speakStop()
            return
        if self._conv:
            self.talk()
        elif self.micMode == "conversation" and not self._voice_state:
            self.talk()
        else:
            self.voice()

    @Slot()
    def talk(self) -> None:
        """Toggle the hands-free conversation (`claude-notch talk`)."""
        if self._conv:
            self._conv_end()
            return
        if self._voice_state:                            # a one-shot dictation is under way
            return
        if not self._start_recorder():
            return
        self._conv = True
        self._seg_start = None
        self._loud_ticks = 0
        if not self.speechEnabled:                       # a conversation talks back
            self._conv_speech_on = True
            self.setSpeech(True)
        self._set_voice("listening", 0.0)
        log("conversation: on")

    def _conv_end(self) -> None:
        self._stop_recorder()
        self._conv = False
        self._seg_start = None
        if self._conv_speech_on:
            self._conv_speech_on = False
            self.setSpeech(False)
        self._set_voice("", 0.0)
        log("conversation: off")

    def _panel_busy(self) -> bool:
        # Only the panel's own session counts: another session's work must not
        # silence the conversation. Unknown (no panel feed yet) means not busy.
        return self._panel_activity == "busy"

    def _conv_tick(self, peak: float, now: float) -> None:
        V = self.cfg["voice"]
        paused = self._speaking or self._panel_busy()
        if self._voice_state == "transcribing":
            return
        if paused:
            self._seg_start = None
            self._loud_ticks = 0
            if self._voice_state != "paused":
                self._set_voice("paused")
            return
        if self._seg_start is None:
            if self._voice_state != "listening":
                self._set_voice("listening")
            self._loud_ticks = self._loud_ticks + 1 if peak > float(V.get("start_level", 0.05)) else 0
            if self._loud_ticks >= 2:                    # two ticks (100 ms) of sound: speech
                pre = 16000 * 2 * 4 // 10                # 0.4 s before it
                self._seg_start = max(44, self._rec_offset - pre)
                self._speech_since, self._silence_since, self._loud_ticks = now, 0.0, 0
                self._set_voice("hearing")
            return
        if peak > float(V.get("end_level", 0.03)):
            self._silence_since = 0.0
        elif not self._silence_since:
            self._silence_since = now
        quiet = self._silence_since and now - self._silence_since > int(V.get("silence_ms", 900)) / 1000
        if quiet or now - self._speech_since > float(V.get("max_utterance_s", 30)):
            start, end = self._seg_start, self._rec_offset
            self._seg_start = None
            if (end - start) / 32000 >= 0.7:
                self._conv_transcribe(start, end)
            else:
                self._set_voice("listening")

    def _conv_transcribe(self, start: int, end: int) -> None:
        try:
            with open(self._rec_file, "rb") as f:
                f.seek(start)
                data = f.read(end - start)
        except OSError as e:
            log(f"conversation: cannot read the recording ({e})")
            self._set_voice("listening")
            return
        seg = self._rec_file.with_name("utterance.wav")
        n = len(data)
        header = (b"RIFF" + (36 + n).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
                  + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
                  + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
                  + b"data" + n.to_bytes(4, "little"))
        seg.write_bytes(header + data)
        self._set_voice("transcribing")
        self._transcribe(seg, self._convResult)

    _HALLUCINATION = re.compile(r"^(you|thank you|thanks|děkuji|titulky.*|subtitles.*|amara\.org.*|[.…\s]*)[.!?\s]*$", re.I)

    def _conv_result(self, text: str) -> None:
        if not self._conv:
            return
        if text and not self._HALLUCINATION.match(text):
            log(f"conversation: {len(text)} chars")
            self._type(text, enter=True)
        self._set_voice("listening")

    @Property("QVariant", notify=settingsChanged)
    def microphones(self):
        """PipeWire sources for the menu: the system default first, then each
        input (monitors left out), marked current / muted."""
        want = str(self.cfg["voice"].get("source", "auto") or "auto")
        items = [{"name": "auto", "label": "", "current": want == "auto", "muted": False}]
        try:
            r = subprocess.run(["pactl", "-f", "json", "list", "sources"], capture_output=True, text=True, timeout=5)
            for src in json.loads(r.stdout or "[]"):
                name = src.get("name", "")
                if not name or name.endswith(".monitor"):
                    continue
                props = src.get("properties") or {}
                label = (props.get("node.description") or props.get("device.description")
                         or props.get("node.nick") or src.get("description") or "")
                if not label or label == "(null)":
                    label = name.replace("alsa_input.", "").replace("bluez_input.", "")
                items.append({"name": name, "label": label, "current": want == name, "muted": bool(src.get("mute"))})
        except Exception as e:                           # noqa: BLE001
            log(f"cannot list microphones ({e})")
        return items

    @Slot(str)
    def setMicrophone(self, name: str) -> None:
        """Pick the dictation source; a muted one is unmuted, or nothing would ever be heard."""
        if name != "auto":
            subprocess.run(["pactl", "set-source-mute", name, "0"], capture_output=True, timeout=5)
        set_config("voice", "source", name)
        self.reloadConfig()

    def _record_argv(self, wav: Path) -> list[str]:
        argv = [a.replace("{file}", str(wav)) for a in shlex.split(self.cfg["voice"]["record"])]
        src = str(self.cfg["voice"].get("source", "auto") or "auto")
        if src != "auto":
            if "{target}" in argv:
                argv = [src if a == "{target}" else a for a in argv]
            elif argv and os.path.basename(argv[0]) == "pw-record":
                argv[1:1] = ["--target", src]
        else:
            argv = [a for a in argv if a != "{target}"]
        return argv

    def _voice_model(self) -> Path | None:
        m = str(self.cfg["voice"].get("model", "auto") or "auto")
        if m != "auto":
            p = Path(os.path.expanduser(m))
            return p if p.is_file() else None
        try:
            bins = sorted(MODELS_DIR.glob("*.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        return bins[0] if bins else None

    def _voice_missing(self) -> str | None:
        rec = shlex.split(self.cfg["voice"]["record"])
        if not rec or shutil.which(rec[0]) is None:
            return f"the recorder '{rec[0] if rec else ''}' (pipewire's pw-record) is not installed"
        if shutil.which("whisper-cli") is None:
            return "whisper-cli is not installed (sudo pacman -S whisper-cpp ggml-vulkan)"
        if self._voice_model() is None:
            return f"no whisper model in {MODELS_DIR} (see README: Dictation)"
        return None

    def _set_voice(self, state: str, level: float | None = None) -> None:
        self._voice_state = state
        if level is not None:
            self._voice_level = level
        self.voiceChanged.emit()

    def _start_recorder(self) -> bool:
        missing = self._voice_missing()
        if missing:
            self._notify("Claude Notch", f"Dictation: {missing}.")
            return False
        if not self._chat:
            self.show()
        out = CACHE_DIR / "claude-notch" / "voice"
        out.mkdir(parents=True, exist_ok=True)
        self._rec_file = out / "dictation.wav"
        self._rec_file.unlink(missing_ok=True)
        argv = self._record_argv(self._rec_file)
        try:
            self._rec = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            warn(f"cannot start the recorder {argv}: {e}")
            self._notify("Claude Notch", f"Dictation: cannot start {argv[0]}.")
            return False
        self._rec_offset, self._voice_peak, self._rec_started = 44, 0.0, time.time()   # past the wav header
        self._level_timer.start()
        return True

    @Slot()
    def voice(self) -> None:
        """Toggle one-shot dictation: start recording; the next call stops it and
        types the transcript into the chat (no Enter, so it can be edited first).
        During a conversation it ends the conversation instead."""
        if self._speaking:                               # the shortcut while it talks: quiet, first
            self.speakStop()
            if self._conv:
                return
        if self._conv:
            self._conv_end()
            return
        if self._voice_state == "recording":
            self._voice_stop()
            return
        if self._voice_state:
            return                                       # still transcribing
        if self._start_recorder():
            self._set_voice("recording", 0.0)
            log("dictation: recording")

    def _poll_level(self) -> None:
        """Peak of the samples written since the last tick — drives the mic halo."""
        if self._rec is None or self._rec_file is None:
            return
        try:
            with open(self._rec_file, "rb") as f:
                f.seek(self._rec_offset)
                data = f.read()
        except OSError:
            data = b""
        level, peak = 0.0, 0.0
        n = len(data) // 2
        if n:
            self._rec_offset += n * 2
            samples = array("h")
            samples.frombytes(data[: n * 2])
            peak = max(abs(v) for v in samples) / 32768
            self._voice_peak = max(self._voice_peak, peak)
            level = min(1.0, peak * 2.5)
        self._voice_level = level if level > self._voice_level else self._voice_level * 0.75   # fast attack, slow release
        self.voiceChanged.emit()
        if self._rec.poll() is not None:                 # the recorder died under us
            if self._conv:
                self._conv_end()
            else:
                self._voice_stop()
            return
        if self._conv:
            self._conv_tick(peak, time.time())

    def _stop_recorder(self) -> None:
        self._level_timer.stop()
        proc, self._rec = self._rec, None
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGINT)              # lets pw-record finish the wav header
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _voice_stop(self) -> None:
        self._stop_recorder()
        wav, dur = self._rec_file, time.time() - self._rec_started
        if wav is None or not wav.exists() or dur < 0.6 or self._voice_peak < 0.015:
            self._set_voice("", 0.0)                     # a stray click, or silence
            log(f"dictation: dropped ({dur:.1f} s, peak {self._voice_peak:.3f})")
            if dur >= 0.6:
                self._notify("Claude Notch", "Dictation: nothing heard. Pick the microphone in the orb menu: Settings › Microphone.")
            return
        self._set_voice("transcribing", 0.0)
        self._transcribe(wav, self._voiceResult)

    def _transcribe(self, wav: Path, done) -> None:
        """whisper-cli on a wav, off the GUI thread; the text goes out through `done`."""
        model, lang = self._voice_model(), str(self.cfg["voice"].get("language", "auto") or "auto")

        def worker():
            text = ""
            try:
                r = subprocess.run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", lang, "-nt", "-np"],
                                   capture_output=True, text=True, timeout=180)
                if r.returncode == 0:
                    text = " ".join(ln.strip() for ln in r.stdout.splitlines() if ln.strip())
                else:
                    log(f"whisper-cli failed ({r.returncode}): {r.stderr.strip()[-300:]}")
            except Exception as e:                       # noqa: BLE001
                log(f"whisper-cli: {e}")
            text = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text)  # [BLANK_AUDIO], (music) …
            done.emit(re.sub(r"\s+", " ", text).strip())
        threading.Thread(target=worker, daemon=True).start()

    def _voice_done(self, text: str) -> None:
        self._set_voice("", 0.0)
        log(f"dictation: {len(text)} chars")
        if not text:
            self._notify("Claude Notch", "Dictation: nothing recognised.")
            return
        if self._type(text + " "):
            raise_window(TERM_CLASS)

    @Slot()
    def voiceCancel(self) -> None:
        if self._conv:
            self._conv_end()
            return
        if self._voice_state != "recording":
            return
        self._stop_recorder()
        self._set_voice("", 0.0)
        log("dictation: cancelled")

    @Slot(int)
    def kwinDone(self, seq: int) -> None:
        """Called from inside each KWin script once it has run (see _kwin_worker)."""
        if seq == _kwin_ack_seq:
            _kwin_ack.set()

    # ── speech ──────────────────────────────────────────────────────────
    @Property(bool, notify=speechChanged)
    def speechEnabled(self):
        return bool(self.cfg["speech"].get("enabled"))

    @Property(bool, notify=speechChanged)
    def speaking(self):
        return self._speaking

    @Slot(bool)
    def setSpeech(self, on: bool) -> None:
        self.cfg["speech"]["enabled"] = bool(on)
        set_config("speech", "enabled", bool(on))
        if not on:
            self.speakStop()
        self.speechChanged.emit()

    def _piper(self) -> str | None:
        p = str(self.cfg["speech"].get("piper") or "piper")
        return shutil.which(p) or (str(HOME / ".local" / "bin" / p) if (HOME / ".local" / "bin" / p).is_file() else None)

    def _voice_for(self, text: str) -> Path | None:
        want = str(self.cfg["speech"].get("voice", "auto") or "auto")
        try:
            voices = sorted(VOICES_DIR.glob("*.onnx"))
        except OSError:
            voices = []
        if not voices:
            return None
        if want != "auto":
            for v in voices:
                if v.stem == want or v.name == want:
                    return v
            p = Path(os.path.expanduser(want))
            return p if p.is_file() else voices[0]
        # by language: Czech diacritics in the text, else the UI language, else whatever there is
        lang = "cs" if re.search(r"[ěščřžýáíéůúďťňĚŠČŘŽÝÁÍÉŮÚĎŤŇ]", text) else self.cfg["ui"]["language"]
        for v in voices:
            if v.name.startswith(lang + "_"):
                return v
        return voices[0]

    @staticmethod
    def _speakable(text: str, limit: int) -> str:
        """Plain sentences out of a markdown answer: no code, no tables, no link targets."""
        t = re.sub(r"```.*?```", " ", text, flags=re.S)             # fenced code
        t = re.sub(r"^\s*\|.*\|\s*$", " ", t, flags=re.M)             # table rows
        t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)             # [text](url)
        t = re.sub(r"`([^`]*)`", r"\1", t)                             # inline code
        t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)            # headings
        t = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", t, flags=re.M)    # list bullets
        t = re.sub(r"^\s*>\s?", "", t, flags=re.M)                     # quotes
        t = re.sub(r"[*_~]{1,3}(\S.*?\S|\S)[*_~]{1,3}", r"\1", t)     # emphasis
        t = re.sub(r"https?://\S+", "", t)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\s*\n\s*", "\n", t).strip()
        if len(t) > limit:
            cut = t[:limit]
            end = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
            t = (cut[: end + 1] if end > limit // 3 else cut).rstrip() + " …"
        return t

    @staticmethod
    def _last_answer(transcript: Path) -> str:
        """The text of the assistant's final message of the last turn in a Claude
        Code transcript (.jsonl). `user` records that only carry tool results do
        not start a new turn."""
        text = ""
        try:
            with open(transcript, encoding="utf-8") as f:
                for ln in f:
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    content = (rec.get("message") or {}).get("content")
                    if rec.get("type") == "user":
                        blocks = content if isinstance(content, list) else []
                        if isinstance(content, str) or any(isinstance(b, dict) and b.get("type") == "text" for b in blocks):
                            text = ""
                        continue
                    if rec.get("type") != "assistant" or not isinstance(content, list):
                        continue
                    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    t = "\n".join(x for x in parts if x.strip())
                    if t:
                        text = t
        except OSError as e:
            log(f"transcript unreadable ({e})")
        return text

    @Slot(str, str)
    def turnEnded(self, transcript_path: str, last_message: str = "") -> None:
        """From the Stop hook of the panel session: read the answer aloud if speech
        is on. The hook hands over the answer itself (last_assistant_message);
        the transcript is the fallback for a Claude Code without that field."""
        log(f"turn ended: {os.path.basename(transcript_path)}, {len(last_message)} chars")
        if not self.speechEnabled:
            return
        if last_message.strip():
            self.say(last_message)
        elif transcript_path:
            self._turn_read(Path(transcript_path), 0)

    def _turn_read(self, transcript: Path, attempt: int) -> None:
        # The hook may fire before the answer has reached the transcript; look again shortly.
        text = self._last_answer(transcript)
        if text:
            self.say(text)
        elif attempt < 6:
            QTimer.singleShot(250, lambda: self._turn_read(transcript, attempt + 1))
        else:
            log("turn ended: no answer text found in the transcript")

    @Slot(str)
    def notified(self, message: str) -> None:
        """From the Notification hook: Claude waits on you — say so, briefly."""
        if self.speechEnabled and message and not self._speaking:
            self.say(message)

    @Slot(str)
    def say(self, text: str) -> None:
        """Speak a text with piper (`claude-notch say "…"`). Interrupts what is being said."""
        piper = self._piper()
        voice = self._voice_for(text)
        if piper is None or voice is None:
            self._notify("Claude Notch", "Speech needs piper (uv tool install piper-tts) and a voice in "
                         f"{VOICES_DIR} — see README: Speech.")
            return
        S = self.cfg["speech"]
        speech = self._speakable(text, int(S.get("max_chars", 700)))
        if not speech:
            return
        self.speakStop()
        self._speech_seq += 1
        seq = self._speech_seq
        out = CACHE_DIR / "claude-notch" / "speech"
        out.mkdir(parents=True, exist_ok=True)
        wav = out / f"say-{seq}.wav"
        custom = str(S.get("synth") or "").strip()
        rate = max(0.5, min(2.0, float(S.get("rate", 1.0) or 1.0)))
        sr = 22050
        try:
            sr = int((json.loads(voice.with_suffix(".onnx.json").read_text()).get("audio") or {}).get("sample_rate") or sr)
        except Exception:                                # noqa: BLE001
            pass
        self._speaking = True
        self.speechChanged.emit()
        log(f"speech: {len(speech)} chars, {'custom' if custom else voice.stem}, rate {rate}")

        def worker():
            ok = False
            try:
                if custom:                               # another engine: writes a file, then it is played
                    argv = [a.replace("{text}", speech).replace("{file}", str(wav)) for a in shlex.split(custom)]
                    if shutil.which(argv[0]) is None and (HOME / ".local" / "bin" / argv[0]).is_file():
                        argv[0] = str(HOME / ".local" / "bin" / argv[0])      # uv/pipx tools live there
                    synth = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, text=True)
                    self._speech_procs.append(synth)
                    _, err = synth.communicate(speech, timeout=120)
                    if synth.returncode == 0 and wav.exists():
                        play = [a.replace("{file}", str(wav)) for a in shlex.split(S["play"])]
                        player = subprocess.Popen(play, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        self._speech_procs.append(player)
                        player.wait()
                        ok = True
                    elif synth.returncode not in (0, -signal.SIGTERM):
                        log(f"synth failed ({synth.returncode}): {err.strip()[-200:]}")
                else:                                    # piper streams raw samples straight into the player
                    play = [a.replace("{rate}", str(sr)) for a in shlex.split(S["play_raw"])]
                    synth = subprocess.Popen([piper, "-m", str(voice), "--output-raw", "--length-scale", f"{1 / rate:.3f}"],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
                    player = subprocess.Popen(play, stdin=synth.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._speech_procs += [synth, player]
                    synth.stdout.close()                 # the player owns the pipe now
                    synth.stdin.write(speech.encode()); synth.stdin.close()
                    err = synth.stderr.read().decode(errors="replace"); synth.wait(timeout=120)
                    player.wait()
                    ok = synth.returncode == 0
                    if synth.returncode not in (0, -signal.SIGTERM):
                        log(f"piper failed ({synth.returncode}): {err.strip()[-200:]}")
            except Exception as e:                       # noqa: BLE001
                log(f"speech: {e}")
            finally:
                wav.unlink(missing_ok=True)
                if seq == self._speech_seq:              # not superseded by a newer say()
                    self._speechDone.emit(ok)
        threading.Thread(target=worker, daemon=True).start()

    def _speech_finished(self, ok: bool) -> None:
        self._speaking = False
        self._speech_procs = []
        self.speechChanged.emit()

    @Slot()
    def speakStop(self) -> None:
        procs, self._speech_procs = self._speech_procs, []
        for pr in procs:
            if pr.poll() is None:
                pr.terminate()
        if self._speaking:
            self._speaking = False
            self.speechChanged.emit()

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
                           "terminalShown": self._term_shown,
                           "screen": self.cfg["screen"]["name"], "lang": self.cfg["ui"]["language"],
                           "geo": list(self._geo), "width": int(self.L["width"]),
                           "activity": self._activity,
                           "panelActivity": self._panel_activity, "panelSession": self._usage.get("panelSession"),
                           "voiceState": self._voice_state, "conversation": self._conv, "speaking": self._speaking,
                           "speech": self.speechEnabled,
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
        self._set_term_shown(False)
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
        env["CLAUDE_NOTCH_PANEL"] = "1"       # the status line feed keys its panel snapshot on this
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
        # Arm KWin first: the fresh terminal is placed the moment it maps,
        # before its first frame, instead of flashing at KWin's default
        # position until a timer catches up (visible after a cold boot).
        place_on_map(TERM_CLASS, x, y, w, h, fade_ms=fade)
        if not self._launch_terminal():
            self.hide()
            return
        # Belt and braces for a KWin that refused the hook: re-place a few
        # times without touching opacity, so an already faded-in window
        # does not blink.
        for ms in (1500, 3000, 6000):
            QTimer.singleShot(ms, lambda: self._chat and place(
                TERM_CLASS, x, y, w, h, raise_it=True))

    @Slot()
    def revealTerminal(self) -> None:
        if self._chat:
            reveal(TERM_CLASS, int(self.T["fade_ms"]))

    @Property(bool, notify=terminalShownChanged)
    def terminalShown(self):
        """True from the moment the terminal is on screen until the chat closes —
        the container keeps its rectangle open (transparent, no input) meanwhile."""
        return self._term_shown

    def _set_term_shown(self, on: bool) -> None:
        if on != self._term_shown:
            self._term_shown = on
            self.terminalShownChanged.emit()

    @Slot()
    def terminalMapped(self) -> None:
        """Called from the KWin fade-in script once the terminal is fully visible."""
        if self._chat:
            self._set_term_shown(True)

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
        PANEL_FILE.unlink(missing_ok=True)
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

    @Property(str, notify=usageChanged)
    def projectName(self):
        """The bar's project chip: where the panel session actually runs (from
        its feed), else the folder the next session will start in."""
        cwd = self._usage.get("cwd") or ""
        p = Path(cwd) if cwd else self._workdir
        return p.name or str(p)

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

    for stale in KWIN_SCRIPT.parent.glob("claude-notch-kwin-*.js"):   # left by an instance that died
        stale.unlink(missing_ok=True)
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
