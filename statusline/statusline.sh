#!/usr/bin/env bash
# Example Claude Code status line:  model │ context │ session cost │ session time │ plan limits
# Installed by `install.sh --with-statusline`. Feel free to replace everything
# below the feed line with your own layout — only the feed matters to the notch.
export LC_ALL=C.UTF-8
input=$(cat)
source "${XDG_DATA_HOME:-$HOME/.local/share}/claude-notch/usage-feed.sh"

MODEL=$(jq -r '.model.display_name // "?"' <<<"$input")
PCT=$(jq -r '.context_window.used_percentage // empty' <<<"$input")
COST=$(jq -r '.cost.total_cost_usd // 0' <<<"$input")
MS=$(jq -r '.cost.total_duration_ms // 0' <<<"$input")
H5=$(jq -r '.rate_limits.five_hour.used_percentage // empty' <<<"$input")
D7=$(jq -r '.rate_limits.seven_day.used_percentage // empty' <<<"$input")

DIM=$'\033[38;5;242m'; R=$'\033[0m'
CYAN=$'\033[38;5;79m'; GREEN=$'\033[38;5;114m'
YELL=$'\033[38;5;179m'; RED=$'\033[38;5;168m'; VIOL=$'\033[38;5;140m'
SEP="${DIM}  │  ${R}"

level_color() { local v=${1%%.*}; [[ -z $v ]] && v=0
  if   (( v < 50 )); then printf '%s' "$GREEN"
  elif (( v < 80 )); then printf '%s' "$YELL"
  else printf '%s' "$RED"; fi; }

if [[ -n $PCT ]]; then CTX="$(level_color "$PCT")${PCT%%.*}%${R}${DIM} ctx${R}"
else CTX="${DIM}– ctx${R}"; fi

COSTS=$(printf '$%.2f' "$COST")

S=$(( MS / 1000 )); H=$(( S/3600 )); M=$(( (S%3600)/60 )); SS=$(( S%60 ))
if   (( H > 0 )); then CLK=$(printf '%dh %02dm' "$H" "$M")
elif (( M > 0 )); then CLK=$(printf '%dm %02ds' "$M" "$SS")
else CLK=$(printf '%ds' "$SS"); fi

LIM=""
if [[ -n $H5 || -n $D7 ]]; then
  parts=""
  [[ -n $H5 ]] && parts="${DIM}5h ${R}$(level_color "$H5")${H5%%.*}%${R}"
  [[ -n $D7 ]] && { [[ -n $parts ]] && parts+="${DIM} · ${R}"; parts+="${DIM}7d ${R}$(level_color "$D7")${D7%%.*}%${R}"; }
  LIM="${SEP}${parts}"
fi

printf '%b%s%b%s%s%s%b%s%b%s%b%s%b%s\n' \
  "$CYAN" "$MODEL" "$R" "$SEP" "$CTX" "$SEP" \
  "$GREEN" "$COSTS" "$R" "$SEP" "$VIOL" "$CLK" "$R" "$LIM"
