---
name: tamahermes-status
description: Show TamaHermes growth state and installed custom pet package status. Use when the user invokes /tamahermes-status or asks for TamaHermes status.
---

# TamaHermes Status

This skill is the Desktop slash-menu alias for `/tamahermes-status`.

From the TamaHermes release folder, use the bundled Codex Python runtime when
available:

```bash
PY="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
[ -x "$PY" ] || PY=python3
"$PY" -m tamahermes status
"$PY" -m tamahermes doctor
ls -la "${CODEX_HOME:-$HOME/.codex}/pets/tamahermes"
```

Summarize:

- current line, machine, life stage, form, XP, and last Codex state
- visual bins for energy, satiety, health, bond, mess, and alert
- whether the installed pet package exists
- whether atlas validation and screen mask clipping pass
- note that readable growth information is shown here because Codex native
  Wake Pet custom avatars cannot render custom hover panels or play bundled SFX
