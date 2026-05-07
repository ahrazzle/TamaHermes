---
name: tamacodex-status
description: Show Tamacodex growth state and installed custom pet package status. Use when the user invokes /tamacodex-status or asks for Tamacodex status.
---

# Tamacodex Status

This skill is the Desktop slash-menu alias for `/tamacodex-status`.

From the Tamacodex release folder, use the bundled Codex Python runtime when
available:

```bash
PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
[ -x "$PY" ] || PY=python3
"$PY" -m tamacodex status
"$PY" -m tamacodex doctor
ls -la "${CODEX_HOME:-$HOME/.codex}/pets/tamacodex"
```

Summarize:

- current line, machine, life stage, form, XP, and last Codex state
- visual bins for energy, satiety, health, bond, mess, and alert
- whether the installed pet package exists
- whether atlas validation and screen mask clipping pass
- note that readable growth information is shown here because Codex native
  Wake Pet custom avatars cannot render custom hover panels or play bundled SFX
