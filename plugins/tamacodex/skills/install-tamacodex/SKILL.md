---
name: install-tamacodex
description: Install or switch the Tamacodex Codex custom pet. Use when the user invokes /install-tamacodex, asks to install Tamacodex, or chooses Toast/Mais and Aurora/Pulse.
---

# Install Tamacodex

This skill is the Desktop slash-menu alias for `/install-tamacodex`.

## Parse Choices

Accept the user's requested choices from the prompt text after the command.

- pawn line: `toast`, `mais` (default: `toast`)
- tamago shell: `aurora`, `pulse` (default: `aurora`)

Examples:

- `/install-tamacodex mais pulse` means `--line mais --machine pulse`
- `/install-tamacodex toast` means `--line toast --machine aurora`

## Run

From the Tamacodex repository root, use the bundled Codex Python runtime when
available:

```bash
/Users/chua/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m tamacodex setup --line <line> --machine <machine> --force --json
```

If that runtime is unavailable, use the Python interpreter that has the
Tamacodex package and Pillow installed.

## Verify

Confirm that these files exist:

```bash
ls -la "${CODEX_HOME:-$HOME/.codex}/pets/tamacodex/pet.json" "${CODEX_HOME:-$HOME/.codex}/pets/tamacodex/spritesheet.webp"
```

The setup JSON should include `overlaySupervisor` even when Codex has not
created `.codex-global-state.json` yet. That supervisor idles until Codex App's
selected pet is `custom:tamacodex`, then starts a Swift/AppKit nonactivating
panel with a transparent WKWebView frontend plus gated sidecar audio/state. The
status panel is hidden until the mouse stays over the native Tamacodex mascot
for 1 second, then shows a rectangular frosted LCD box populated from
`${CODEX_HOME:-~/.codex}/tamacodex/state.json`. The older Tk visual status strip
is explicit-only and should not be used for product validation.

Sidecar audio controls are:

```bash
tamacodex overlay mute
tamacodex overlay unmute
tamacodex overlay quiet
tamacodex overlay normal
```

Then tell the user to enable the visible pet in Codex App:

```text
Settings -> Appearance -> scroll to bottom -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

If `Tamacodex` is not listed, tell the user to run `Cmd+K` ->
`Force reload skills` or fully quit and reopen Codex App.
