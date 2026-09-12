# Provenance

EvoPet is built from two lineages. Both are recorded here because GitHub can only show **one**
fork parent per repository, and this project has two.

    TamaCodex   (Alichua/TamaCodex, upstream, effectively dormant)
        └── TamaHermes   (ahrazzle/TamaHermes — ours: a TamaCodex fork made Hermes-compatible)
                └── EvoPet / pet   (this fork — the pet half of EvoPet)

    Petdex      (crafter-station/petdex, upstream)
        └── EvoPet / shell  (ahrazzle/petdex fork — the desktop app that floats the pet)

## What each half is

| half | lineage | language | role |
|---|---|---|---|
| the pet | `TamaCodex` -> `TamaHermes` -> this tree | Python | the mechanics: XP, stats, stages, evolution; bakes the 1536x1872 sprite sheet |
| the shell | `Petdex` -> the EvoPet app repo | Zig | the desktop app that floats the pet; taps every agent's hook payload |

## What is deliberately NOT changed

`ahrazzle/TamaHermes` continues as its own, lighter project: a TamaCodex fork carrying the tweaks
that make the pet work with Hermes, living on inside Petdex. It is actively maintained separately.
This tree forked **from** it at a pinned commit; it does not replace it, and work in one must not be
pushed into the other without a deliberate merge.

## Why the pet half is a separate tree at all

The pet's growth logic is EvoPet's to change (combined ledger, cross-agent drain, decay, mood) and
those changes must not land on the tree another project is still maintaining. Two trees, two futures,
one shared ancestor — recorded in the tables above.
