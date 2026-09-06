#!/usr/bin/env bash
# Claude Notch installer — copies the app into ~/.local, wires autostart and
# (optionally) the status line feed, the Plasma widget and the crash-to-agent extra.
#
#   ./install.sh [--with-statusline] [--with-plasmoid] [--with-crash-agent]
#                [--shortcut "Meta+Ctrl+Shift+A"] [--plus-shortcut "Meta+Shift+A"] [--no-autostart] [--start]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN="$HOME/.local/bin"
APPDIR="$DATA/claude-notch"

WITH_STATUSLINE=0 WITH_PLASMOID=0 WITH_CRASH=0 AUTOSTART=1 START=0 SHORTCUT="" PLUS_SHORTCUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-statusline) WITH_STATUSLINE=1 ;;
    --with-plasmoid)   WITH_PLASMOID=1 ;;
    --with-crash-agent) WITH_CRASH=1 ;;
    --shortcut)        SHORTCUT="${2:?}"; shift ;;
    --plus-shortcut)   PLUS_SHORTCUT="${2:?}"; shift ;;
    --no-autostart)    AUTOSTART=0 ;;
    --start)           START=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac; shift
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

echo "Checking requirements"
[[ "${XDG_SESSION_TYPE:-}" == "wayland" && "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]] \
  && ok "KDE Plasma on Wayland" || warn "not a KDE Plasma Wayland session — the notch relies on KWin scripting"
command -v python3 >/dev/null || die "python3 not found"
python3 - <<'PY' || die "Python 3.11+ with PySide6 (incl. QtDBus) is required"
import sys; assert sys.version_info >= (3, 11)
import PySide6.QtDBus, PySide6.QtQml
PY
ok "python3 $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))') + PySide6 $(python3 -c 'import PySide6;print(PySide6.__version__)')"
for c in qdbus6 kscreen-doctor jq; do command -v "$c" >/dev/null && ok "$c" || die "$c not found (packages: qt6-tools, kscreen, jq)"; done
command -v alacritty >/dev/null && ok "alacritty" || warn "alacritty not found — set [terminal].launch in $CONF/claude-notch/config.toml"
command -v claude >/dev/null && ok "claude" || warn "claude not on PATH — set [agent].command if your agent is called differently"
command -v tmux >/dev/null && ok "tmux" || warn "tmux not found — the chat's + bar (files, folders, /mcp, /plugin) needs it: sudo pacman -S tmux"
command -v wl-paste >/dev/null && ok "wl-clipboard" || warn "wl-clipboard not found — 'Paste image from clipboard' needs it: sudo pacman -S wl-clipboard"

echo "Installing files"
mkdir -p "$APPDIR" "$BIN" "$CONF/claude-notch" "$CONF/autostart" "$DATA/applications"
install -m 644 claude-notch/notch.py claude-notch/notch.qml "$APPDIR/"
install -m 644 statusline/usage-feed.sh "$APPDIR/"
install -m 755 bin/claude-notch "$BIN/claude-notch"
install -m 755 bin/claude-notch-activity "$BIN/claude-notch-activity"
install -m 644 config/alacritty.toml "$CONF/claude-notch/alacritty.toml"
install -m 644 config/tmux.conf "$CONF/claude-notch/tmux.conf"
[[ -f "$CONF/claude-notch/config.toml" ]] || install -m 644 config/config.example.toml "$CONF/claude-notch/config.toml"
rm -f "$APPDIR/place.js" "$APPDIR"/claude-notch-kwin-*.js 2>/dev/null || true
ok "app → $APPDIR, launcher → $BIN/claude-notch, config → $CONF/claude-notch/"

# The genuine Claude mark, taken from an icon you already have installed — the
# repository ships no Anthropic artwork. Falls back to a procedural starburst.
ICON=""; for c in /usr/share/icons/hicolor/256x256/apps/claude-desktop.png /usr/share/icons/hicolor/128x128/apps/claude-desktop.png \
              "$DATA/icons/hicolor/256x256/apps/claude-desktop.png"; do [[ -f $c ]] && { ICON=$c; break; }; done
if [[ -n $ICON ]] && command -v magick >/dev/null; then
  magick "$ICON" -alpha remove -alpha off -colorspace gray -threshold 72% \
    -gravity center -crop 66%x66%+0+0 +repage -transparent black "PNG32:$APPDIR/glyph.png" 2>/dev/null \
    && ok "starburst taken from your installed Claude icon" || warn "could not extract the icon; using the procedural starburst"
else
  rm -f "$APPDIR/glyph.png"; ok "procedural starburst (install claude-desktop + imagemagick to use the real mark)"
fi

cat > "$DATA/applications/claude-notch.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Claude Notch
GenericName=AI agent
Comment=Open or close the Claude Notch chat panel
Exec=$BIN/claude-notch toggle
Icon=utilities-terminal
Terminal=false
Categories=Development;Utility;
Keywords=ai;agent;claude;notch;
Actions=Plus;

[Desktop Action Plus]
Name=Add to the chat
Exec=$BIN/claude-notch plus
DESK
update-desktop-database "$DATA/applications" 2>/dev/null || true
ok "application entry (Claude Notch → toggle)"

if (( AUTOSTART )); then
  cat > "$CONF/autostart/claude-notch.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Claude Notch
Comment=Claude Code usage notch on the screen edge
Exec=$BIN/claude-notch start
Icon=utilities-terminal
Terminal=false
X-KDE-autostart-phase=2
DESK
  ok "autostart at login"
fi

if [[ -n $SHORTCUT ]]; then
  kwriteconfig6 --file kglobalshortcutsrc --group services --group claude-notch.desktop \
    --key _launch "$SHORTCUT,none,Claude Notch"
  ok "shortcut $SHORTCUT → toggle (active after the next login)"
fi
if [[ -n ${PLUS_SHORTCUT:-} ]]; then
  kwriteconfig6 --file kglobalshortcutsrc --group services --group claude-notch.desktop \
    --key Plus "$PLUS_SHORTCUT,none,Add to the chat"
  ok "shortcut $PLUS_SHORTCUT → + popup (active after the next login)"
fi

if (( WITH_STATUSLINE )); then
  echo "Status line"
  mkdir -p "$HOME/.claude"
  if [[ -f "$HOME/.claude/statusline.sh" ]]; then
    cp "$HOME/.claude/statusline.sh" "$HOME/.claude/statusline.sh.bak-$(date +%Y%m%d%H%M%S)"
    warn "existing ~/.claude/statusline.sh backed up"
  fi
  install -m 755 statusline/statusline.sh "$HOME/.claude/statusline.sh"
  S="$HOME/.claude/settings.json"
  [[ -f $S ]] || echo '{}' > "$S"
  cp "$S" "$S.bak-claude-notch"
  jq '. + {statusLine: {type: "command", command: "~/.claude/statusline.sh", padding: 0}}' "$S.bak-claude-notch" > "$S"
  ok "~/.claude/statusline.sh installed and registered (previous settings.json → settings.json.bak-claude-notch)"
else
  echo "Status line feed"
  if grep -qs 'usage-feed.sh' "$HOME/.claude/statusline.sh" 2>/dev/null; then ok "your status line already sources the feed"
  else warn "the notch needs the usage feed: rerun with --with-statusline, or add to your own status line script:"
       echo '        input=$(cat); source ~/.local/share/claude-notch/usage-feed.sh'; fi
fi

# Session activity hooks: the notch's ring spins while Claude works and pulses
# amber while it waits on you. Appended to any hooks you already have.
echo "Activity hooks"
S="$HOME/.claude/settings.json"; mkdir -p "$HOME/.claude"; [[ -f $S ]] || echo '{}' > "$S"
A="$BIN/claude-notch-activity"
if grep -q 'claude-notch-activity' "$S"; then ok "hooks already present in settings.json"
else
  cp "$S" "$S.bak-claude-notch-hooks"
  jq --arg a "$A" '
    def add($ev; $st): .hooks[$ev] = ((.hooks[$ev] // []) + [{hooks: [{type: "command", command: ($a + " " + $st)}]}]);
    .hooks = (.hooks // {})
    | add("UserPromptSubmit"; "busy") | add("PreToolUse"; "busy")
    | add("Stop"; "idle") | add("SessionEnd"; "idle") | add("Notification"; "waiting")
  ' "$S.bak-claude-notch-hooks" > "$S" && ok "hooks added (UserPromptSubmit/PreToolUse → busy, Stop/SessionEnd → idle, Notification → waiting)"
fi

if (( WITH_PLASMOID )); then
  echo "Plasma widget"
  if kpackagetool6 --type Plasma/Applet --show org.claudenotch.usage >/dev/null 2>&1; then
    kpackagetool6 --type Plasma/Applet --upgrade plasmoid/org.claudenotch.usage >/dev/null && ok "widget upgraded"
  else
    kpackagetool6 --type Plasma/Applet --install plasmoid/org.claudenotch.usage >/dev/null && ok "widget installed — add “Claude Usage” to a panel"
  fi
fi

if (( WITH_CRASH )); then
  echo "Crash-to-agent"
  install -m 755 extras/crash-to-agent/crash-watch extras/crash-to-agent/crash-brief "$APPDIR/"
  mkdir -p "$CONF/systemd/user"
  install -m 644 extras/crash-to-agent/crash-watch.service "$CONF/systemd/user/"
  for d in "$HOME/.agents/skills" "$HOME/.claude/skills"; do
    [[ -d $d && ! -L $d/diagnose-crash ]] || true
  done
  SK="$HOME/.claude/skills"; [[ -d "$HOME/.agents/skills" ]] && SK="$HOME/.agents/skills"
  mkdir -p "$SK/diagnose-crash"; install -m 644 extras/crash-to-agent/skill/diagnose-crash/SKILL.md "$SK/diagnose-crash/"
  systemctl --user daemon-reload && systemctl --user enable --now crash-watch.service >/dev/null 2>&1 \
    && ok "crash-watch service running; skill → $SK/diagnose-crash" || warn "could not enable crash-watch.service"
fi

if (( START )); then
  "$BIN/claude-notch" restart >/dev/null && ok "claude-notch running"
fi

echo
echo "Done. Next:"
(( START )) || echo "  • start it:            claude-notch start        (or just log in again)"
echo "  • open/close the chat: claude-notch toggle       (bind it: ./install.sh --shortcut 'Meta+Ctrl+Shift+A')"
echo "  • tweak:               $CONF/claude-notch/config.toml"
