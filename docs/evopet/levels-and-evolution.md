# Levels and evolution: the contract for a pet creator

You are forking the pet catalogue and the pet generator, and you will hand us a `pet.json` plus a
spritesheet. This page is everything EvoPet fixes and everything you choose, so you can pick your
evolution levels and predict when your pet changes form. The XP that fills the ladder comes from the
combined ledger (`docs/evopet/W1-combined-ledger.md`); the fork lineage is in `PROVENANCE.md`. Your
gates are live: the compiler writes them into every `pet.json` it emits, install copies them into the
ledger, and the running pet evolves on the ledger's copy, not on the manifest (§2).

## 1. The ladder (fixed by EvoPet)

Every pet in every package climbs the same ladder, so a level means the same work in every pet.
Everything here is implemented in `tamahermes/levels.py` unless a section says otherwise.

    level 1              0 XP
    level 99       100,000 XP
    xp_for_level(L) = round(100_000 * ((L - 1) / 98) ** 2.0)

XP is cumulative and never decreases, so a level once reached is never lost; the exponent of 2.0 makes
the early levels cheap and the last ones long.

    level   cumulative XP   this level costs
        1               0                  -
        2              10                 10
       20           3,759                385
       50          25,000              1,010
       70          49,573              1,426
       99         100,000              2,030

The first level costs 10 XP, which one prompt and one success covers (4 + 14 = 18 XP). Level 50 sits at
exactly a quarter of the cap, half the cap (50,000 XP) lands inside level 70, and the climb from level 50
to level 99 costs 75,000 XP.

**The bar fills against the level, not the form.** `level_progress(xp)` reports the level and your
position between its floor (`xp_for_level(L)`) and ceiling (`xp_for_level(L + 1)`), so the bar fills
about a hundred times in a pet's life and resets at every level-up; it changes only when the pet
crosses a gate, since a gate is the only thing that changes the sprite. At level 99 the ceiling is
`None`, the bar reports 100%, and `visual_state.percent_bucket` snaps it to 20 buckets of 5%.

## 2. Evolution gates (your choice)

How many evolutions your pet has, and at which levels, is yours. Declare it in `pet.json`:

    "evopet": { "evolutionGates": [11, 23, 32, 45] }

A gate is a level the pet *reaches*: when cumulative XP first equals or passes `xp_for_level(gate)`, the
pet moves to the next form. There is always one more form than gate, the first form being the one it
starts in; four gates (five forms) is the maximum, one gate (two forms) the minimum.
`levels.validate_gates` enforces exactly that, and names the problem when it refuses: `evolution gate
120 is outside 1..99`, `evolution gates must strictly increase; 10 follows 30`, `a pet needs at least
one evolution gate`, `at most 4 gates supported (5 forms); got 5`.

Form names are positional, not yours to name: `egg`, `hatchling`, `child`, `teen`, `adult` — three gates
give you egg, hatchling, child and teen, and your pet never reaches `adult`. `teen` and `adult` also carry
branch variants the runtime picks from stats and traits, not from a gate (§5; one line's twelve forms are
listed in `tamahermes/catalog_assets/manifest.json`).

A gate at level 1 is legal and fires at 0 XP, so the pet starts already hatched and its first form is
never drawn; the lowest useful gate is level 2, at 10 XP. `levels.gates_from_manifest` is forgiving
when it only *reads* a manifest: no `evopet` block at all returns the default pet's gates,
`(11, 23, 32, 45)`, and an empty `"evopet": {}` does the same.

**Applying your gates.** The sequence, and when each step takes effect:

1. Declare `"evopet": {"evolutionGates": [...]}` in the `pet.json` the install will replace, or edit
   the installed one afterwards (`<codex home>/pets/<petId>/pet.json`).
2. Install. `install_codex_pet` absorbs the manifest it is about to replace into the ledger
   (`pet_compiler.sync_ledger_gates`), then builds, so the new `pet.json` carries the ledger's gates and
   `levels.gates_from_manifest(json.load(pet.json))` returns exactly what the ledger holds. The real
   callers (`cli.py`, `watcher.py`) then record and save the ledger.
3. The change takes effect on the **next install**, not on the next event: nothing re-reads a manifest
   mid-run, and `state.evolution_gates(state)` — the list the ledger carries — is what `maybe_evolve`
   and `stage_progress` read. Editing a manifest does not retroactively move a growing pet.

A manifest that declares nothing leaves the ledger alone: silence is not a declaration, so an
`"evopet": {}` block cannot reset gates that are already installed. A block that is present but invalid
is refused at install, and `PetCompileError` names the file — `pet.json: evolution gates must strictly
increase; 20 follows 40`. A ledger with no `evolutionGates` key at all (every older ledger) behaves
exactly as it always has, on the default pet's gates; an unusable list takes the same fallback at
runtime, deliberately, because a growth event that raises would strand a user mid-run.

## 3. The default pet's gates

`DEFAULT_EVOLUTION_GATES = (11, 23, 32, 45)`, which on the fixed ladder is:

| gate level | cumulative XP | from | to |
|---|---|---|---|
| 11 | 1,041 | egg | hatchling |
| 23 | 5,040 | hatchling | child |
| 32 | 10,006 | child | teen |
| 45 | 20,158 | teen | adult |

The gate levels were picked against the curve, not round XP numbers: at 18 XP per prompt-and-success turn
they cost about 58, 280, 556 and 1,120 turns.

Two properties make a reached gate safe to rely on. The gate level becomes a floor in the ledger table
(`ledger_thresholds`) and XP only rises, so a pet cannot regress out of a form. The terminal form is
absent from that table (for the default pet, `{egg: 1041, hatchling: 5040, child: 10006, teen: 20158}`),
so the last form has nothing to grow into. Dormancy is a condition, not a stage, so a hibernating pet
still reports the progress it has genuinely made.

## 4. Choosing your own gates

Three evolutions, at levels 20, 40 and 60:

    "evopet": { "evolutionGates": [20, 40, 60] }

Four forms, named `egg`, `hatchling`, `child` and `teen`:

| gate level | cumulative XP | from | to | XP this step costs |
|---|---|---|---|---|
| 20 | 3,759 | egg | hatchling | 3,759 |
| 40 | 15,837 | hatchling | child | 12,078 |
| 60 | 36,245 | child | teen | 20,408 |

That is about 209, 880 and 2,014 turns at 18 XP each. The gates sit 20 levels apart and nowhere near
evenly spaced in XP: the third step costs 1.69 times what the second costs, because the same 20 levels
are worth less work near the bottom of the curve.

- Front-load: the whole climb to level 20 costs 3,759 XP, under 4% of the cap. Gates below level 30
  feel fast, gates above level 50 do not.
- Price a gate in turns, not in levels: a gate at 40 is 880 turns, a gate at 60 is 2,014.
- Keep the first gate above level 1, or your starting form is never displayed.
- Expect the last form to be most of the pet's life: the shipping pet reaches adult at 20,158 XP, a
  fifth of the cap, and stays there.

`state.EVENT_DELTAS` values a turn: `prompt_sent` 4 XP, `task_success` 14, `task_failure` 5,
`recovery` 6, `review_opened` 5, `care` 3, `session_start` 2, so a bad day is the slow path.

## 5. Not yet implemented

- **You cannot name your own forms.** `forms_for_gates` names them positionally from `STAGE_ORDER`, and
  the `"forms"` key in the `levels.py` docstring is read by nothing: a manifest's `"forms"` list is ignored.
- **Gates are levels only.** No XP amount, no branch, no condition such as three failed runs; a gate's XP
  is whatever the curve says its level is worth, and the `teen`/`adult` branches still come from stats.
- **Nothing cross-checks your gates against your spritesheet when you declare them** — that half of the
  old item holds, its reason (that nothing reads the gates) does not. `validate_gates` checks the list's
  shape only, so a line lacking a form for a stage your gates imply fails later, in the catalogue lookup,
  when the pet grows into it (`CatalogError: no form for line ...`); the runtime's no-raise fallback does
  not cover that, because it protects an unusable gate *list*, not one the catalogue cannot satisfy.

## 6. Where the numbers live

In `tamahermes/levels.py`:

    MAX_LEVEL = 99        CAP_XP = 100_000        EXPONENT = 2.0        MANIFEST_KEY = "evopet"
    STAGE_ORDER = ("egg", "hatchling", "child", "teen", "adult")
    DEFAULT_EVOLUTION_GATES = (11, 23, 32, 45)

Functions, in the order you will want them: `xp_for_level(L)`, `level_for_xp(xp)`, `level_progress(xp)`
(the level band the bar fills against), `validate_gates(gates)` (raises `ValueError`),
`gates_from_manifest(manifest)` (your gates, or the defaults), `forms_for_gates(gates)` (positional
form names), `thresholds_for_gates(gates)` (`{form: the XP it begins at}`), `ledger_thresholds(gates)`
(`{stage: the XP that ends it}`, terminal form absent), `stage_for_xp(xp, gates)`, `gate_report(gates)`
(the creator-facing gate table), `curve_report(levels)`.

Related: `state.py` holds `EVOLUTION_GATES` and `STAGE_THRESHOLDS` (the default pet's) and reads the
ledger's own list in `evolution_gates` / `evolution_thresholds` — what `maybe_evolve` and `stage_progress`
use; `state.EVENT_DELTAS` holds the XP each event is worth; `visual_state.py` quantises the bar. The
installer's side is `pet_compiler.py` (`CURVE`, `evolution_block`, `declared_gates`, `sync_ledger_gates`,
`read_manifest`); `evopet_drain.curve_block()` stamps the combined ledger.

Every number here was printed from the real formula, not remembered, and the whole contract is covered by
the suite (`uv run --quiet python -m unittest discover -s tests -t tests`, 183 tests, OK):

    uv run --quiet python -c "from tamahermes import levels; print(levels.curve_report()); print(levels.gate_report()); print(levels.gate_report((20,40,60)))"
