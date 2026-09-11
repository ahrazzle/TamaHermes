# TamaHermes SFX

Short, soft chiptune-style WAV assets for the local preview. Codex native
custom pet packages currently load only `pet.json` and `spritesheet.webp`, so
these sounds are an experimental preview contract rather than native overlay
audio.

Design notes:

- Motifs are written from C major / A minor scale tones, then rendered down a
  perfect fourth for a warmer G major / E minor range.
- Each pulse has a softened onset and release to avoid clicky, startling
  attacks while retaining a small quantized 8-bit edge.
- The WAV peaks are intentionally conservative because the overlay also applies
  per-event playback volume.
- Regenerate these assets with `python -m tamahermes_gen.scripts.render_sfx`.

Event map:

- `hatch` -> `hatch.wav`
- `evolve` -> `evolve.wav`
- `session_start` -> `work.wav`
- `prompt_sent` -> `work.wav`
- `progress` -> `work.wav`
- `task_success` -> `task_success.wav`
- `task_failure` -> `task_failure.wav`
- `recovery` -> `recovery.wav`
- `review_opened` -> `review_opened.wav`
- `care` -> `care.wav`
- `rest` -> `rest.wav`
- `hover` -> `care.wav`
- `drag` -> `work.wav`
