# Claude Notch usage feed — source this from your Claude Code status line script.
#
#   input=$(cat)
#   source ~/.local/share/claude-notch/usage-feed.sh   # writes the feed
#   ... your own status line output ...
#
# It snapshots rate_limits / model / cost / context_window from the JSON Claude
# Code hands the status line, plus a timestamp, into ~/.cache/claude-usage.json. The write is
# atomic (temp file + rename) so readers never see a half-written file.
claude_notch_feed() {
  local out="${XDG_CACHE_HOME:-$HOME/.cache}/claude-usage.json" tmp
  tmp=$(mktemp "${out}.XXXXXX") || return 0
  if jq -c --argjson t "$(date +%s)" '{rate_limits, model, cost, context_window, session_id, cwd: (.workspace.current_dir // .cwd), _at: $t}' <<<"$1" > "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$out"
    # The chat panel's own session (the notch sets CLAUDE_NOTCH_PANEL in its
    # environment) also keeps a private snapshot, so the bar under the terminal
    # shows *its* model and context rather than whichever session wrote last.
    if [[ -n ${CLAUDE_NOTCH_PANEL:-} ]]; then
      local pnl="${out%/*}/claude-notch-panel.json"
      cp -f "$out" "$pnl.tmp" && mv -f "$pnl.tmp" "$pnl"
    fi
  else
    rm -f "$tmp"
  fi
}
claude_notch_feed "$input"
