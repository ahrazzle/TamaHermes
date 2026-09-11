# Tamacodex on Hermes Agent

TamaCodex is a Tamagotchi-style desktop pet that grows from your agent activity.
It was built for Codex; this fork adds first-class **Hermes Agent** support — the
same pet, the same growth model, fed from Hermes' own hooks instead of Codex's.

The port is small on purpose. TamaCodex already compiles exactly the artifact
Hermes renders: an 8-column × 9-row atlas of 192×208 cells (1536×1872) whose row
order — `idle, running-right, running-left, waving, jumping, failed, waiting,
running, review` — is byte-identical to Hermes' `CODEX_STATE_ROWS`. So no sprite
surgery was needed; what changed is **where the pet installs** and **where the
growth events come from**.

## Install

```bash
git clone <this fork>
cd TamaCodex
./hermes/install-hermes.sh --line toast --machine aurora
```

Then enable the plugin and select the pet:

```bash
hermes plugins enable tamacodex-hermes
hermes pets select tamacodex
hermes pets doctor        # should report ✓ ready
```

That's it. On the next turn the pet appears in the CLI, the TUI, and the desktop
app, and starts reacting to what the agent is doing.

## What grows the pet

Hermes fires observer hooks; this port maps them onto TamaCodex's existing event
taxonomy (`tamacodex/state.py`), so the deltas are identical to the Codex side:

| Hermes hook | TamaCodex event | Effect |
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
python -m tamacodex --target hermes event care --amount 1 --install
python -m tamacodex --target hermes event rest --amount 4
python -m tamacodex --target hermes status
```

## Two ways to wire it

Both feed the **same** ledger (`<HERMES_HOME>/tamacodex/state.json`). Pick one —
wiring both double-feeds growth.

### 1. Native plugin (recommended)

`plugins/tamacodex-hermes/` is a Hermes plugin (`plugin.yaml` + `register(ctx)`).
The installer copies it to `<HERMES_HOME>/plugins/tamacodex-hermes/`; enable it
with `hermes plugins enable tamacodex-hermes`.

It runs in-process, so there is nothing to allowlist, and it is careful about the
hot path: hook callbacks only enqueue a payload on a background worker thread, and
the expensive part — recompiling the annotated atlas and reinstalling the pet —
is coalesced, so a burst of tool calls triggers one rebuild, not forty. A failed
rebuild is logged and dropped; a mascot must never break an agent turn.

### 2. Shell hooks

If you'd rather not run a plugin, add the block in
`hermes/hooks.snippet.yaml` to `<HERMES_HOME>/config.yaml` (replace `__REPO__`
with your checkout path), then allowlist each entry with `hermes hooks list`.
The hook shells out to `plugins/tamacodex/scripts/tamacodex_hermes_hook.sh`,
which reads the same Hermes payload and applies it identically.

## Where things live

| Path | What |
|---|---|
| `<HERMES_HOME>/pets/tamacodex/pet.json` | the pet manifest Hermes reads |
| `<HERMES_HOME>/pets/tamacodex/spritesheet-<hash>.webp` | the compiled 8×9 atlas |
| `<HERMES_HOME>/tamacodex/state.json` | the growth ledger |
| `<HERMES_HOME>/tamacodex/hermes-hook-state.json` | per-turn bookkeeping (turn ids only) |
| `<HERMES_HOME>/plugins/tamacodex-hermes/` | the native plugin |

`HERMES_HOME` is honoured everywhere, including hosted profiles
(`~/.hermes/profiles/<name>`), so each profile keeps its own pet and its own
ledger. Pointing `--target hermes` at a non-live home (tests, staging) installs
the pet but deliberately does **not** run `hermes pets select`, so it can't
mutate the running profile's config.

## CLI additions

Every command takes `--target {codex,hermes}` (default `codex`) and
`--hermes-home PATH` (default `HERMES_HOME` or `~/.hermes`):

```bash
python -m tamacodex --target hermes --hermes-home ~/.hermes setup --line mais --machine pulse --force --json
python -m tamacodex --target hermes status --json
python -m tamacodex --target hermes install --force
echo '{"hook_event_name":"post_tool_call","tool_name":"write_file","extra":{"status":"ok"},"turn_id":"t1"}' \
  | python -m tamacodex --target hermes hermes-hook --json
```

New subcommand: `hermes-hook` applies one Hermes hook payload from stdin (this is
what the shell-hook script calls). `--reset` clears the turn bookkeeping.

## Environment

| Variable | Purpose |
|---|---|
| `HERMES_HOME` | target Hermes home (profile-aware) |
| `TAMACODEX_HERMES_HOME` | overrides `HERMES_HOME` for the hook script/plugin |
| `TAMACODEX_REPO_ROOT` | lets the plugin/script import `tamacodex` without an install |
| `TAMACODEX_PY` | interpreter for the shell-hook wrapper |
| `TAMACODEX_HERMES_SYNC=1` | run the plugin inline instead of on the worker thread (tests) |
| `TAMACODEX_LINE` / `TAMACODEX_MACHINE` | pin the companion line / tamago shell |
| `TAMACODEX_CATALOG_DIR` | custom rendered catalog directory |

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

The installer records the checkout in `<HERMES_HOME>/tamacodex/repo-root`, which
is how the plugin imports `tamacodex` during an ordinary `hermes` run — no
environment variable and no site-packages install required (Pillow, the only
dependency, already ships with Hermes). If you move the checkout, re-run the
installer or set `TAMACODEX_REPO_ROOT`.

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

Upstream: [Alichua/TamaCodex](https://github.com/Alichua/TamaCodex) (MIT).
Hermes port: this fork.
