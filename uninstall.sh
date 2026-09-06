#!/usr/bin/env bash
# Removes Claude Notch. Keeps ~/.config/claude-notch (your config) unless --purge.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"; CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
"$HOME/.local/bin/claude-notch" stop 2>/dev/null || true
pkill -f "claude-notch/notch.py" 2>/dev/null || true
systemctl --user disable --now crash-watch.service 2>/dev/null || true
rm -f "$CONF/systemd/user/crash-watch.service"; systemctl --user daemon-reload 2>/dev/null || true
kpackagetool6 --type Plasma/Applet --remove org.claudenotch.usage >/dev/null 2>&1 || true
rm -rf "$DATA/claude-notch"
rm -f "$HOME/.local/bin/claude-notch" "$CONF/autostart/claude-notch.desktop" "$DATA/applications/claude-notch.desktop"
rm -f "${XDG_CACHE_HOME:-$HOME/.cache}"/claude-notch-kwin-*.js "${XDG_CACHE_HOME:-$HOME/.cache}/claude-notch.log"
kwriteconfig6 --file kglobalshortcutsrc --group services --group claude-notch.desktop --key _launch --delete 2>/dev/null || true
if [[ "${1:-}" == "--purge" ]]; then rm -rf "$CONF/claude-notch"; echo "config removed"; else echo "config kept in $CONF/claude-notch"; fi
echo "If you installed the status line with --with-statusline, your previous ~/.claude/settings.json is in settings.json.bak-claude-notch."
echo "Claude Notch removed."
