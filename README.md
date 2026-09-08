# Claude Notch

**A screen-edge notch for [Claude Code](https://claude.com/claude-code) on KDE Plasma.**
At rest it is a 5-pixel sliver coloured by how much of your plan you have used. Hover and it grows into a bubble with usage rings and reset times. Click and the bubble unfolds into a terminal running your agent — click the strip and it folds back.

<p align="center">
  <img src="docs/screenshots/hero.png" alt="Claude Notch on a CachyOS desktop: the usage bubble with its details card on the left, the chat panel unfolded on the right" width="960">
</p>
<p align="center">
  <img src="docs/screenshots/bubble.png" alt="Usage bubble with the details panel" width="440">
</p>
<p align="center">
  <img src="docs/screenshots/chat.png" alt="Chat panel unfolded from the notch" width="230">
  &nbsp;
  <img src="docs/screenshots/menu.png" alt="The orb menu" width="230">
  &nbsp;
  <img src="docs/screenshots/rest.png" alt="Resting sliver on the screen edge" width="42">
</p>

It is one shape the whole time — sliver → bubble → chat — morphing at 60 fps, with inverted corners where it meets the screen edge so it reads as carved into the display rather than parked on top of the wallpaper. Inspired by [Codenotch](https://github.com/vinzdg/codenotch) for macOS; built for Linux from scratch.

## What it shows

| | |
|---|---|
| **Sliver** | green / amber / red by the current 5-hour window; dims when the feed is stale |
| **Bubble** | starburst, usage ring, percentage; a details panel with both limit windows and their reset countdowns |
| **Chat** | your agent in a terminal inside the container, with the ring parked in a side strip |
| **Live activity** | a thin arc spins inside the ring while Claude is working; the ring — and the resting sliver — pulse amber while it waits on you (a permission prompt, a question) |

The numbers come from Claude Code itself. Claude Code hands its status line a JSON blob that already contains `rate_limits.five_hour` and `rate_limits.seven_day` (with `resets_at`); a one-line hook snapshots that to `~/.cache/claude-usage.json`. **No OAuth token is read, no undocumented endpoint is called** — which also means the data refreshes only while a Claude Code session is running. In practice the chat panel *is* a running session.

## Requirements

- KDE Plasma 6 on **Wayland** (tested on 6.7) — the notch is placed through KWin's scripting API
- Python 3.11+ with **PySide6** (QtQml, QtDBus)
- `qdbus6`, `kscreen-doctor`, `jq`
- a terminal — **alacritty** by default; kitty/foot/others via `[terminal].launch`
- **tmux** (optional) — for the + bar under the chat and sessions that survive the panel
- **wl-clipboard** (optional) — for *Paste image from clipboard*
- **whisper-cpp** + a model (optional) — for dictation, the mic in the bar; `ggml-vulkan` runs it on the GPU
- **piper** + a voice (optional) — for Claude reading its answers aloud, the speaker in the bar
- Claude Code with a plan that reports rate limits (Pro/Max); any other agent CLI works for the chat panel

On Arch/CachyOS: `sudo pacman -S pyside6 qt6-tools kscreen jq alacritty tmux wl-clipboard`

## Install

```sh
git clone https://github.com/Salic99/claude-notch
cd claude-notch
./install.sh --with-statusline --shortcut 'Meta+Ctrl+Shift+A' --start
```

`--with-statusline` installs a ready-made Claude Code status line (model · context · cost · time · plan limits) that includes the feed, backing up any existing one. If you already have a status line you like, skip the flag and add two lines to it instead:

```sh
input=$(cat)
source ~/.local/share/claude-notch/usage-feed.sh
```

Other flags: `--with-plasmoid` (a panel widget with the same rings), `--with-crash-agent` (see *Extras*), `--no-autostart`. The keyboard shortcut becomes active after the next login; the notch itself starts immediately with `--start`.

## Use

| | |
|---|---|
| hover the sliver | usage bubble + details |
| click the sliver / bubble | unfold the chat |
| click the side strip | fold it back |
| `claude-notch toggle` | the same, for shortcuts and scripts |
| hover the orb below the notch, click it | the menu (`claude-notch menu`) |
| the bar under the terminal | **+** (files, folder, clipboard image, `/mcp`, `/plugin` — `claude-notch plus`), the **mic** (`claude-notch voice`), the **project** and **model** pickers, the context gauge, **Stop** |
| `claude-notch add <file>…` | mention files in the chat |
| `claude-notch bar plus\|project\|model` | open one of the bar's popups |
| `claude-notch start` / `stop` / `restart` / `status` | lifecycle |
| `claude-notch reload` | re-read `config.toml` live (also after hand-editing it) |

The chat is a normal terminal window (`alacritty` with the shipped theme) running `claude` in `~/Projects`, dropping to your shell when the agent exits so the panel never closes under you. It keeps its session while folded — folding hides the window, it does not kill it.

Inside the terminal the agent runs in a private **tmux** session (no prefix key, no status line — you will not notice it). That is what lets the panel type into the agent, and it means the session outlives the window: restart the notch, or even close the terminal window, and the next open reattaches to the same conversation. Set `[agent].tmux = false` to run the agent bare.

### The bar

Under the terminal sits a bar — the chat's own controls, drawn by the notch rather than the terminal:

| | |
|---|---|
| **+** | the popup below (or `claude-notch plus`) |
| **mic** | a hands-free conversation, or dictation — see below |
| **speaker** | Claude reads its answers aloud — see *Speech* below (`claude-notch speech on\|off`) |
| **project ▾** | the folder the chat runs in; pick another and a new session starts there (the configured workdir and its most recent sub-folders — the same list as the orb menu's *Project*) |
| **model ▾** | the model the chat runs on; a pick types `/model <id>` into it. The list is `[agent].models` in `config.toml` |
| **◔ 28 % context** | how full the session's context window is, in the ring's colours |
| **Stop** | shows while the chat is working (sends Escape, which stops the running turn) and while the notch reads an answer aloud (quiets it) |

The model, context and Stop belong to *this* chat's session, not to whichever Claude Code session wrote the feed last: the notch starts the panel's agent with `CLAUDE_NOTCH_PANEL` set, and the feed keeps a separate snapshot for it (`~/.cache/claude-notch-panel.json`). Sessions started before that flag existed fall back to the shared feed.

The **+** popup:

| Item | What it types into the chat |
|---|---|
| Add files or photos | a KDE file picker; each pick becomes an `@path` mention (images included) |
| Add folder | a directory picker; `@folder/` |
| Paste image from clipboard | saves the clipboard image under `~/.cache/claude-notch/clips/` and mentions it (needs `wl-clipboard`) |
| Connectors | `/mcp` — Claude Code's MCP server dialog |
| Plugins | `/plugin` — the plugin manager |

Mentions are inserted without pressing Enter, so you can add your question after them. Two more ways in: **drag files** from your file manager onto the bar or the strip, or run `claude-notch add <file>…` from a shell. `./install.sh --plus-shortcut 'Meta+Shift+A'` binds a key to the popup.

### Conversation

Click the mic and just talk. When you pause (`[voice].silence_ms`, 0.9 s), what you said is transcribed and **sent**; Claude answers; the answer is read aloud (the speaker switches on for the conversation if it was off); then the mic listens again. While Claude works or talks the mic is deaf — it would otherwise hear the speakers — and the bar says so. A green pulse means it is listening, red means it hears you, **End** (or a click on the mic) ends the conversation. `claude-notch talk` toggles it from a shell. Two-second exchanges, entirely on your machine.

### Dictation

With `[voice].mode = "dictation"` the mic does one-shot dictation instead (always available as `claude-notch voice`, or the key from `--voice-shortcut`): click the mic and talk; click it again and what you said is typed into the chat — without Enter, so you can fix a word or add to it before sending. The halo around the mic follows your voice; **Cancel** (or a right click on the mic) throws the recording away. `claude-notch voice` does the same toggle from a shell, and `./install.sh --voice-shortcut 'Meta+Shift+V'` binds a key to it, for push-to-talk without reaching for the mouse.

Everything stays on your machine: PipeWire's `pw-record` captures 16 kHz mono, [whisper.cpp](https://github.com/ggml-org/whisper.cpp) transcribes it. Setup:

```sh
sudo pacman -S whisper-cpp ggml-vulkan       # ggml-vulkan: run the model on the GPU (any vendor); CPU works too, slower
mkdir -p ~/.local/share/claude-notch/models
curl -L -o ~/.local/share/claude-notch/models/ggml-large-v3-turbo-q5_0.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
```

The microphone is the system default unless you pick one in the orb menu (*Settings › Microphone*; `[voice].source`). `large-v3-turbo` (q5_0, 574 MB) handles Czech and English well and takes well under a second on a GPU; the first run after boot is slower while the Vulkan shaders compile. Smaller models (`ggml-small.bin`, `ggml-base.bin`) trade accuracy for speed on a CPU. `[voice]` in `config.toml` chooses the model (`auto` = the newest `.bin` in that folder), the language (`auto` detects; `cs` or `en` pins it) and the recorder command. Recordings that are too short or silent are dropped, so a stray click types nothing.

### Speech

Turn the speaker on and Claude reads every answer of the chat aloud as soon as it finishes — the plain sentences of it: code blocks, tables and link targets are left out, and a long answer stops at a sentence end after `[speech].max_chars`. While it talks the speaker's waves breathe; **Stop** in the bar, a click on the speaker or the mic, the voice shortcut, or `claude-notch speech stop` quiets it. When Claude waits on you (a permission prompt, a question), it says so. Nothing leaves the machine: [piper](https://github.com/OHF-Voice/piper1-gpl) synthesizes, PipeWire plays.

```sh
uv tool install piper-tts                     # or pipx; puts `piper` in ~/.local/bin
mkdir -p ~/.local/share/claude-notch/voices && cd ~/.local/share/claude-notch/voices
B=https://huggingface.co/rhasspy/piper-voices/resolve/main
curl -LO $B/cs/cs_CZ/jirka/medium/cs_CZ-jirka-medium.onnx -LO $B/cs/cs_CZ/jirka/medium/cs_CZ-jirka-medium.onnx.json
curl -LO $B/en/en_US/lessac/medium/en_US-lessac-medium.onnx -LO $B/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

Any voice from [piper-voices](https://huggingface.co/rhasspy/piper-voices) works; `auto` picks a Czech voice when the answer has Czech diacritics, else one for the UI language. Piper streams straight into the player, so the first words come before the sentence is finished; `[speech].rate` sets the tempo. Prefer another engine? `[speech].synth` takes any command that turns `{text}` into an audio `{file}` — for instance Microsoft's neural voices through [edge-tts](https://github.com/rany2/edge-tts) (`uv tool install edge-tts`; online, the text leaves your machine): `synth = "edge-tts --voice cs-CZ-AntoninNeural --text {text} --write-media {file}"`. The Stop hook of the panel's session hands the notch the transcript path; that is how it knows what was said (`claude-notch say "…"` speaks anything).

## Live activity

The ring answers *"is it still working?"* Claude Code fires [hooks](https://code.claude.com/docs/en/hooks) at the turns of a session; the installer registers a tiny hook target (`claude-notch-activity`) for five of them:

| Hook | State |
|---|---|
| `UserPromptSubmit`, `PreToolUse` | **busy** — a thin arc spins inside the ring |
| `Notification` | **waiting** — the ring and the resting sliver pulse amber: Claude needs you |
| `Stop`, `SessionEnd` | **idle** |

Each session writes its own record to `~/.cache/claude-notch-activity.json`; the notch shows *waiting* if any session waits, else *busy* if any is busy. A *busy* older than 90 s counts as idle, in case a `Stop` hook never arrived. The hooks are appended to whatever you already have in `~/.claude/settings.json` (backed up first) and take effect for sessions started afterwards.

## The orb menu

Below the notch sits a small hook — the **orb**. Hover it and the hook curls up into a gear; click it (or run `claude-notch menu`) for the menu:

| | |
|---|---|
| **Open / Close chat**, **New session**, **Continue last session** | session control without touching the terminal (`claude --continue` for the last one) |
| **Project ›** | pick the folder the agent starts in — the configured workdir and its most recent sub-folders |
| **Open in a window** | the same agent in a normal, decorated terminal window for longer work |
| **Show details** | keeps the usage bubble open until you click elsewhere |
| **Settings ›** | **Monitor** (lists your outputs), **Panel width**, **Start at login**, **Language**, **Microphone** (for dictation; picking a muted one unmutes it), plus *Edit config file* and *View log* |
| **Restart notch** / **Quit** | |

Settings that change the layout are written to `config.toml` (comments preserved) and applied **live** — monitor, panel width and language take effect without restarting; the chat's terminal keeps its session. After editing `config.toml` by hand, `claude-notch reload` (or the menu) re-reads it.

## Configure

`~/.config/claude-notch/config.toml` — every key is optional; [`config/config.example.toml`](config/config.example.toml) documents them all.

```toml
[agent]
command = "claude"          # or "codex", "opencode", …
workdir = "~/Projects"

[terminal]
launch = "alacritty --config-file {theme} --class {class} -T {title} -e {shell} -c {command}"

[screen]
name = "auto"               # KDE's primary output, or e.g. "DP-2"

[layout]
width = 720                 # chat panel width
```

The UI language follows your desktop locale (English, Czech). `claude-notch restart` after editing.

## How it works — and why it is built this way

**One fixed-size window, morphing content.** Wayland does not let a client position or animate its own window; every move goes through the compositor. So the notch is a single transparent Qt Quick surface the size of the open chat, placed once at the screen edge, and everything visible is drawn inside it: the shape is a canvas path whose width, height, corner radii and *inverted* edge corners are animated properties. The window's **input region** (`QWindow::setMask`) follows the visible shape, so a resting notch only reacts to the sliver and never steals clicks from the desktop.

**Placement via KWin scripting.** `LayerShellQt` would be the textbook way to anchor a surface to a screen edge, but it needs C++ initialisation the `qml` runtime does not perform, and hand-written KWin window rules were silently discarded on reconfigure. What works reliably is KWin's scripting D-Bus API: a tiny JavaScript snippet sets `frameGeometry`, `keepAbove` and `skipTaskbar` by `resourceClass`. KWin caches scripts by *name* and never re-reads the file, so each call registers a fresh name. Calls are serialised on a worker thread so the two D-Bus round trips never stall an animation.

**The terminal lives in a hole in the container.** It is a separate window, inset by a few pixels so the container's rounded corners frame it, and the notch stays *above* it: while the chat is up the container leaves the terminal's rectangle transparent and outside its input mask, so the terminal shows through and gets every click, while the bar's popups and the menu can open over it without any window being restacked. On click the terminal is placed immediately but fully transparent; QML watches the unfolding shape and calls back the instant the shape covers the terminal's rectangle, which fades it in — the terminal is never drawn over the wallpaper, and there is no fixed delay to tune. The KWin script that runs the fade reports back over D-Bus when it is done, and only then does the hole open, so a freshly launched terminal (a new session, a project switch) appears in place rather than as a glimpse of wallpaper. Closing mirrors this: the hole closes, the terminal fades out, then the shape collapses.

Every KWin script ends by calling back into the notch, so a job counts as done only once it has actually run: jobs execute strictly in order, a lost one is retried, and the script file is deleted only afterwards (KWin reads it on a worker thread after `run()` returns). Script objects are left loaded on purpose — KWin hands out ids as `scripts.size()`, so unloading one makes a later id collide with a live script's D-Bus path.

**No flight to the taskbar.** Hiding uses minimize, and KWin animates minimizing windows towards their taskbar icon. The notch fades the window to zero opacity *before* minimizing and un-minimizes it while still transparent, so KWin's animation runs on an invisible window.

**Nothing is drawn without a dark background.** The badge's opacity is a function of how far the shape has grown, and it is hard-gated to its rectangle being inside the shape; the details panel appears only past 60 % of the bubble's growth; the sliver's tint switches to the dark fill the moment the shape leaves sliver size instead of animating through green.

## Plasma widget (optional)

`--with-plasmoid` installs **Claude Usage**, a panel widget with the same 5 h / 7 d rings and reset times. Left click toggles the chat, middle click opens details. Add it to any panel via *Add Widgets*.

## Extras: crash → agent

`--with-crash-agent` installs a small user service that watches `systemd-coredump`. When something crashes you get a notification with **Investigate with the agent**; it collects `coredumpctl info`, the owning package, previous crashes and the surrounding journal into a report and opens your agent on it with a `diagnose-crash` skill that knows how to read a backtrace. Distro-neutral (pacman / dpkg / rpm). The skill is installed into `~/.agents/skills` if that shared hub exists, otherwise `~/.claude/skills`.

## Troubleshooting

- **Everything is grey / "no data".** The feed has not been written yet — start a Claude Code session with the status line hook installed. `cat ~/.cache/claude-usage.json` should show `rate_limits`.
- **The sliver appears on the wrong monitor.** Set `[screen].name` to the output (`kscreen-doctor -o` lists them). "auto" follows KDE's *primary* (priority 1), which is what System Settings shows — Qt's own notion of primary often differs.
- **Clicking does nothing / the terminal never appears.** Check `~/.cache/claude-notch.log` and run `claude-notch restart`. `[terminal].launch` must point at an installed terminal; the agent must be on `PATH`.
- **The shortcut does nothing.** KWin reads shortcut configuration at login — log out and back in.
- **The notch is missing after resuming from sleep or replugging monitors.** `claude-notch reload` re-reads screen geometry.

## Uninstall

```sh
./uninstall.sh           # keeps ~/.config/claude-notch
./uninstall.sh --purge   # removes the config too
```

## Contributing

Issues and pull requests are welcome. The whole app is two files — [`claude-notch/notch.py`](claude-notch/notch.py) (placement, config, D-Bus, terminal) and [`claude-notch/notch.qml`](claude-notch/notch.qml) (the shape and its animations) — so it is a small codebase to read end to end. Things that would be genuinely useful: support for more agents' usage feeds, a left-edge variant, a GNOME port on top of a shell extension.

## License and trademarks

MIT — see [LICENSE](LICENSE).

This is an independent community project. *Claude* and *Claude Code* are trademarks of Anthropic, PBC; this project is not affiliated with or endorsed by Anthropic. The repository ships no Anthropic artwork: the installer extracts the starburst from a Claude app icon *already installed on your machine* (if there is one and ImageMagick is available); otherwise the notch draws a procedural starburst.
