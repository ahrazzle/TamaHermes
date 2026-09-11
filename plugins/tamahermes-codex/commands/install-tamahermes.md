---
description: Install or switch the TamaHermes Codex custom pet and enable hook-driven growth.
argument-hint: [toast|mais] [aurora|pulse]
---

# /install-tamahermes

Install TamaHermes into the current Codex home as a custom pet, then verify the
hook-driven growth ledger and M10 sidecar supervisor.

## Arguments

The user invoked this command with: $ARGUMENTS

- Pawn line: `toast` or `mais` (default: `toast`)
- Tamago shell: `aurora` or `pulse` (default: `aurora`)

## Workflow

1. From the repository root, choose the requested line and machine.
2. Use the bundled Codex Python runtime when available:
   `/Users/chua/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`
3. Run:

```bash
python -m tamahermes setup --line <line> --machine <machine> --force --json
```

4. Confirm that `${CODEX_HOME:-~/.codex}/pets/tamahermes/pet.json` and
   `spritesheet.webp` exist.
5. Check `overlaySupervisor.ok`; setup should install the supervisor even if
   Codex has not written `.codex-global-state.json` yet.
6. Tell the user to select custom pet `TamaHermes` in Codex App settings.

The TamaHermes plugin hook updates growth during Codex tool use. The hook is
best-effort and can self-heal the sidecar supervisor. The supervisor owns the
Swift/AppKit nonactivating WebKit status overlay. The overlay stays hidden until
the mouse remains over the TamaHermes mascot for 1 second, then shows real
growth values from the local state ledger. The user does not need a separate
terminal process.
