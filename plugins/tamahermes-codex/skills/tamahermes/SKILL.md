---
name: tamahermes
description: Install, switch, and inspect the TamaHermes Codex custom pet. Use when the user asks to install TamaHermes, pick Toast/Mais, pick Aurora/Pulse, or check TamaHermes status.
---

# TamaHermes Plugin Skill

TamaHermes is a Codex custom pet plus a hook-driven growth ledger and M10
sidecar overlay/audio supervisor.

## Install Or Switch

Use the bundled Codex Python runtime if available:

```bash
PY=/Users/chua/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
```

Available choices:

- pawn line: `toast`, `mais`
- tamago shell: `aurora`, `pulse`

Install:

```bash
$PY -m tamahermes setup --line <line> --machine <machine> --force --json
```

Then tell the user to select custom pet `TamaHermes` in Codex App settings.
When Codex global state is available, setup also installs the LaunchAgent
supervisor that shows the readable sidecar status and plays deduped bit SFX
only while `custom:tamahermes` is selected.

## Hook Boundary

The plugin hook is best-effort and runs after Codex tool use. It can update the
ledger, refresh the installed pet package, and self-heal the sidecar supervisor.
It still cannot inject code into Codex App's native pet renderer because the
current custom pet contract only loads `pet.json` and `spritesheet.webp`.
