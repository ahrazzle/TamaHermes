# TamaHermes — Hermes Agent support

**TamaHermes** is [Tamacodex](https://github.com/Alichua/TamaCodex) ported to
[Hermes Agent](https://github.com/NousResearch/hermes-agent). TamaHermes is a
Tamagotchi-style pet that grows from your agent activity; upstream it was built
for Codex. This fork runs the same pet, with the same growth model, fed from
Hermes' own hooks instead of Codex's.

> **Names.** Everything is **TamaHermes**: the project, the repository, the
> Python package, the `tamahermes` CLI, and the `tamahermes` Hermes plugin. It
> began as a fork of Tamacodex (credited below), which is why a few upstream
> art and catalog names still read "Tamacodex" — the pet art is unchanged.

The port is small on purpose. TamaHermes already compiles exactly the artifact
Hermes renders: an 8-column × 9-row atlas of 192×208 cells (1536×1872) whose row
order — `idle, running-right, running-left, waving, jumping, failed, waiting,
running, review` — is byte-identical to Hermes' `CODEX_STATE_ROWS`. So no sprite
surgery was needed; what changed is **where the pet installs** and **where the
growth events come from**.

## Install

```bash
git clone <this fork>
cd TamaHermes
./hermes/install-hermes.sh --line toast --machine aurora
```

Then enable the plugin and select the pet:

```bash
hermes plugins enable tamahermes
hermes pets select tamahermes
hermes pets doctor        # should report ✓ ready
```

That's it. On the next turn the pet appears in the CLI, the TUI, and the desktop
app, and starts reacting to what the agent is doing.

## What grows the pet

Hermes fires observer hooks; this port maps them onto TamaHermes's existing event
taxonomy (`tamahermes/state.py`), so the deltas are identical to the Codex side:

| Hermes hook | TamaHermes event | Effect |
|---|---|---|
| `on_session_start` | `session_start` | small XP, focus up, energy down |
| `pre_llm_call` | `prompt_sent` | XP, focus, work counter (once per turn) |
| `post_tool_call` — write/patch/edit succeeded | `task_success` | big XP, mood up, bond up (once per turn) |
| `post_tool_call` — tool failed | `task_failure` | resilience up, health down, mess up |
| `post_tool_call` — next success after a failure | `recovery` | XP, health/mood back, mess down |
| `post_tool_call` — image/browser/vision tool | `review_opened` | focus up, mess down |
| `post_api_request` | `token_usage` | feeds satiety (token counters) |
| `on_session_end` | `task_success` / `task_failure` | turn-level fallback for turns with no write tool |

Two invariants the adapter enforces so the ledger stays honest:

- **One success per turn.** Several writes in one turn still count as a single
  completed run; `on_session_end` can't double-count a turn already recorded.
- **Recovery is real.** A failure marks the turn pending; the next success in
  that turn becomes `recovery`, not another `task_success`.

Care and rest work exactly as documented upstream:

```bash
python -m tamahermes --target hermes event care --amount 1 --install
python -m tamahermes --target hermes event rest --amount 4
python -m tamahermes --target hermes status
```

## The XP bar

Every compiled pet carries a growth bar on its face, in the empty space below the
LCD screen and above the button — visible on the terminal pet, in the desktop
mirror, and in the preview, because it is baked into the sprite rather than drawn
by any one host (Hermes renders nothing but the atlas).

```
egg 0%                 egg 50%               child ~30%            adult 100%
▁▁▁▁▁▁▁▁▁▁▁▁▁▁          ████████▁▁▁▁▁▁          ██████▁▁▁▁▁▁▁▁          ██████████████
```

It tracks **progress toward the next life stage**, not overall XP, so it always
means something at a glance: empty the moment a stage begins, full the moment the
next one lands. Stages and their cumulative-XP boundaries live in one place
(`STAGE_THRESHOLDS` / `STAGE_ORDER` in `tamahermes/state.py`):

| Stage | Begins at | Bar reads |
|---|---|---|
| `egg` | 0 | 0–99% toward hatchling (120) |
| `hatchling` | 120 | 0–99% toward child (320) |
| `child` | 320 | 0–99% toward teen (900) |
| `teen` | 900 | 0–99% toward adult (1800) |
| `adult` | 1800 | full — terminal stage |

The fill is tinted per stage (amber, green, blue, red, gold), with quarter ticks on
the unfilled remainder so a short bar still reads as a gauge. Hibernation — a
dormant condition, not a growth step — paints the bar dimmed and empty.

Two things worth knowing:

- **Growth is quantised to 5% buckets** before it reaches the atlas
  (`visual_state.percent_bucket`). The sprite is recomposited when the drawn bar
  *moves*, not on every XP tick, so a busy turn doesn't trigger dozens of
  72-cell recompiles.
- The bar is painted **outside the LCD screen mask**, which is the one region the
  atlas contract previously kept pristine. The screen-mask validator still enforces
  that nothing else outside the LCD changes, and additionally proves the bar rect
  sits inside the shell silhouette — so a drifting bar is a build failure, not a
  graphic floating in the void.

## Two ways to wire it

Both feed the **same** ledger (`<HERMES_HOME>/tamahermes/state.json`). Pick one —
wiring both double-feeds growth.

### 1. Native plugin (recommended)

`plugins/tamahermes/` is a Hermes plugin (`plugin.yaml` + `register(ctx)`).
The installer copies it to `<HERMES_HOME>/plugins/tamahermes/`; enable it
with `hermes plugins enable tamahermes`.

It runs in-process, so there is nothing to allowlist, and it is careful about the
hot path: hook callbacks only enqueue a payload on a background worker thread, and
the expensive part — recompiling the annotated atlas and reinstalling the pet —
is coalesced, so a burst of tool calls triggers one rebuild, not forty. A failed
rebuild is logged and dropped; a mascot must never break an agent turn.

### 2. Shell hooks

If you'd rather not run a plugin, add the block in
`hermes/hooks.snippet.yaml` to `<HERMES_HOME>/config.yaml` (replace `__REPO__`
with your checkout path), then allowlist each entry with `hermes hooks list`.
The hook shells out to `plugins/tamahermes/scripts/hermes_hook.sh`,
which reads the same Hermes payload and applies it identically.

## Where things live

| Path | What |
|---|---|
| `<HERMES_HOME>/pets/tamahermes/pet.json` | the pet manifest Hermes reads |
| `<HERMES_HOME>/pets/tamahermes/spritesheet-<hash>.webp` | the compiled 8×9 atlas |
| `<HERMES_HOME>/tamahermes/state.json` | the growth ledger |
| `<HERMES_HOME>/tamahermes/hermes-hook-state.json` | per-turn bookkeeping (turn ids only) |
| `<HERMES_HOME>/plugins/tamahermes/` | the native plugin |

`HERMES_HOME` is honoured everywhere, including hosted profiles
(`~/.hermes/profiles/<name>`), so each profile keeps its own pet and its own
ledger. Pointing `--target hermes` at a non-live home (tests, staging) installs
the pet but deliberately does **not** run `hermes pets select`, so it can't
mutate the running profile's config.

## CLI additions

Every command takes `--target {codex,hermes}` (default `codex`) and
`--hermes-home PATH` (default `HERMES_HOME` or `~/.hermes`):

```bash
python -m tamahermes --target hermes --hermes-home ~/.hermes setup --line mais --machine pulse --force --json
python -m tamahermes --target hermes status --json
python -m tamahermes --target hermes install --force
echo '{"hook_event_name":"post_tool_call","tool_name":"write_file","extra":{"status":"ok"},"turn_id":"t1"}' \
  | python -m tamahermes --target hermes hermes-hook --json
```

New subcommand: `hermes-hook` applies one Hermes hook payload from stdin (this is
what the shell-hook script calls). `--reset` clears the turn bookkeeping.

## Environment

| Variable | Purpose |
|---|---|
| `HERMES_HOME` | target Hermes home (profile-aware) |
| `TAMAHERMES_HOME` | overrides `HERMES_HOME` for the hook script/plugin |
| `TAMAHERMES_PETDEX_HOME` | Petdex desktop home to mirror into; unset = no desktop mirror |
| `TAMAHERMES_REPO_ROOT` | lets the plugin/script import `tamahermes` without an install |
| `TAMAHERMES_PY` | interpreter for the shell-hook wrapper |
| `TAMAHERMES_SYNC=1` | run the plugin inline instead of on the worker thread (tests) |
| `TAMAHERMES_LINE` / `TAMAHERMES_MACHINE` | pin the companion line / tamago shell |
| `TAMAHERMES_CATALOG_DIR` | custom rendered catalog directory |

## Floating it on the desktop (Petdex)

Hermes draws pets inside the terminal/TUI. If you also run **Petdex.app** — the
macOS desktop pet host (`/Applications/Petdex.app`, pets in `~/.petdex/pets/`) —
TamaHermes can float there too, off the same ledger:

```bash
./hermes/install-hermes.sh --petdex-activate     # close Petdex.app first
./hermes/install-all-profiles.sh --petdex-activate   # every profile
```

This is a copy, not a second pet. The Petdex desktop app consumes the same
8&times;9 / 192&times;208 atlas Hermes does, so the mirror writes
`~/.petdex/pets/tamahermes/` (`pet.json` + `spritesheet.webp`) and optionally
points `active_pet` at it.

Once the opt-in marker is recorded at `<HERMES_HOME>/tamahermes/petdex-home`,
every growth refresh re-mirrors the sheet, so the floating pet evolves with the
terminal one instead of freezing at whatever it looked like on install day.

```bash
tamahermes petdex --petdex-home ~/.petdex --force [--activate] [--kind creature]
```

Honest caveats:

- The desktop pet is **machine-global** while growth ledgers are per-profile, so
  the sprite you see reflects whichever profile last grew. Installing from every
  profile (as `install-all-profiles.sh --petdex` does) is what keeps the mirror
  current from all of them.
- `--activate` rewrites only `active_pet`; every other key of
  `desktop-native-settings.json` — and its key order — is preserved. Petdex must
  be **closed**, or it rewrites the file from memory on quit and clobbers it.
- `--petdex` alone just adds the pet to the rotation (`rotate_pets`);
  `--petdex-activate` pins it as the one on screen.

## Profiles

Hermes homes are **profile-scoped**: the pets dir is `<HERMES_HOME>/pets`, and
`HERMES_HOME` is set to `<home>/.hermes/profiles/<name>` inside a named profile
(it is unset, i.e. `~/.hermes`, for the default profile). Install once per
profile you actually use:

```bash
# every profile on this machine at once (default + ~/.hermes/profiles/*)
./hermes/install-all-profiles.sh

# ...or one at a time
HERMES_HOME=~/.hermes/profiles/you ./hermes/install-hermes.sh
HERMES_HOME=~/.hermes                 ./hermes/install-hermes.sh
```

`install-all-profiles.sh` also takes `--only name1,name2` and `--keep-selection`
(install the pet without changing which pet is active).

If `hermes plugins enable` dies with `TypeError: list indices must be integers`,
that profile still has the legacy bare-list form of `plugins:` in its
`config.yaml`. Rewrite it to the mapping form and retry:

```yaml
plugins:
  enabled:
    - petdex-desktop
```

The installer records the checkout in `<HERMES_HOME>/tamahermes/repo-root`, which
is how the plugin imports `tamahermes` during an ordinary `hermes` run — no
environment variable and no site-packages install required (Pillow, the only
dependency, already ships with Hermes). If you move the checkout, re-run the
installer or set `TAMAHERMES_REPO_ROOT`.

## Tests

```bash
python -m unittest discover -s tests -p "test_hermes_*.py"
```

`tests/test_hermes_events.py` covers the pure mapping (once-per-turn prompt,
failure→recovery, review tools, token usage, turn-end fallback) and
`tests/test_hermes_install.py` installs into a temp `HERMES_HOME`, asserts the
atlas is a Hermes-renderable 1536×1872 grid, drives the real hook script end to
end, and checks the plugin registers all five hooks.

## Codex compatibility

The Codex integration is untouched: `--target codex` (the default) keeps the
original `.codex-plugin` manifest, `hooks.json`, `PostToolUse` hook, and the
macOS sidecar overlay supervisor. The overlay is Codex-only and is skipped on
`--target hermes`, where Hermes renders the pet itself.

## Credits

Upstream project, pet art, and growth model:
[Alichua/TamaCodex](https://github.com/Alichua/TamaCodex) (MIT).
Hermes Agent port, desktop mirroring, and the 1.0 release: this fork
([ahrazzle/TamaHermes](https://github.com/ahrazzle/TamaHermes)).
