# W1 — the combined ledger and the cross-agent drain (design, v1)

Status: design landed 2026-09-11 by Lugia. Nothing here is built yet except the fork point.

## The requirement this is built to satisfy

User, 2026-09-11, verbatim intent: *the pet collects and grows and gains XP from all active sessions
across all profiles and group chats — no segregated XP. I want one pet that grows from everything
I do.*

Everything below is subordinate to that sentence. It is an acceptance criterion, not a preference.

## Why the current shape cannot satisfy it

Today the pet is **profile-scoped**: every Hermes profile owns `<HERMES_HOME>/tamahermes/state.json`,
its own stage and its own sheet, and the desktop app mirrors whichever profile last changed (last
writer wins). That is nine pets, not one. There is no machine-level state at all.

Evidence that group chats are already real work but already segregated: five profiles each carry
sessions for the *same* group chat id, and each counts it separately —

    profile           sessions titled "Group: rmtx9wc7f-oxkw5"
    azaraki           26
    halakukhan        27
    kodekoot          25
    lugia             28
    shayba            27
    sheikh-al-jabr    27

Those turns earn XP in each profile's own ledger, so the same conversation grows six pets. Under the
combined ledger it grows one pet, by the sum of the six agents' work — which is the point.

## Target shape

    ~/.evopet/state.json        ONE combined ledger: xp, stage, stats, traits, attribution, cursor
    <HERMES_HOME>/tamahermes/state.json   per-profile ledgers, kept as the components they are
    ~/.petdex/runtime/evo-queue/          the spool: foreign agents only (see the split below)
    ~/.petdex/pets/<slug>/                the rendered sheet every surface displays

The compiler renders **from the combined state**, and installs that one sheet into all nine profiles
and into the desktop pet directory. Every surface therefore shows the same creature at the same
stage, and the last-writer-wins identity flip disappears as a side effect.

## The two routes, and why they can never double-count

    route A  Hermes turns      <- per-profile ledgers (the plugin: tokens, write detection, failures)
    route B  foreign agents    <- the spool (claude-code, codex, opencode, gemini)

Each source is counted on exactly one route, and the drain enforces it:

- a spool event with `agent_source == "hermes"` is **ignored** (route A already has it, richer);
- a spool event with **no `session_id`** is ignored. These are the `config.yaml` shell-hook posts
  (`petdex-hook bubble … hermes`), 484 of the 914 events on disk today: display-only, no identity, and
  un-attributable, so they can neither be counted nor deduped safely;
- everything else is a foreign turn and comes from the spool only.

## The drain

`tamahermes/evopet_drain.py`, dry-run by default, `--apply` to write.

1. **Absorb per-profile XP.** For each profile ledger, absorb `max(0, current_xp - cursor_xp)` and
   sum the monotonic counters. This is also the *force-combine* the handoff requires: on first run the
   cursor is zero, so every profile's whole history lands in the combined ledger exactly once.
2. **Merge clamped stats by maximum, not by sum.** energy/health/bond/mess/satiety are clamped to
   0-100, so a delta on them is meaningless; take the max. XP and the run counters are monotonic and
   are summed.
3. **Consume foreign events.** Map spool events to pet events at **turn boundaries only** — never per
   tool call (a Codex turn must be worth a Hermes turn; per-tool accounting inflates 20-40x against
   thresholds of 120/320/900/1800):

       user-prompt    -> prompt_sent   (route A's 4 XP)
       session-start  -> session_start (2)
       approval-request -> review_opened (5)
       stop / session-end -> task_end, resolved by the session's last visual state:
                            jumping -> task_success (14) · failed -> task_failure (5)
                            anything else -> unresolved, counted and reported, awarded nothing

   Rows that earn nothing (`pre`, `post`, `assistant`, `bubble` text) are state/motion only.
4. **Prune what has been absorbed.** After absorption the event files are deleted (move through
   `~/.evopet/consumed/` for a bounded window first). The spool is otherwise unbounded, and it is not
   theoretical: 510 files at handoff, 914 files ~40 minutes later, 892 of them Hermes display noise.
5. **Be idempotent.** A second run with no new activity must report zero deltas and change no bytes.

## What the drain must never carry

The spool's `title` field is the opening line of the user's own prompt and `source_cwd` is a path.
The combined ledger stores attributable numbers only — never prompt text, never a path, never a
session transcript. That restriction is checked by test, not by convention.

## Known gap that blocks value, not correctness

Every non-Hermes event on disk today is a **fixture**: `session_id` values `real-1`, `real-2` and
`phase-test`, `source_cwd` = this project's own repo — 22 events, from `e2e-tap-test.sh`. The tap is
proven; it has simply never seen a real foreign-agent turn on this machine. So the drain can be built,
tested and run now, but it will earn from Hermes (route A) and nothing else until Claude Code and Codex
actually post to the loopback server — which is the transport work (P1: high-frequency events as HTTP
hooks, not a `command` hook with a 2 s timeout per tool call).

## Acceptance checks

1. Sum of per-profile XP == the Hermes share of the combined ledger's attribution.
2. A group-chat session's XP appears in the combined ledger once per participating agent, and zero
   times from the spool.
3. Re-running the drain with no new activity changes no bytes (idempotent).
4. After `--apply`, the spool holds no absorbed `hermes`-route or foreign event, and does not grow
   unbounded while an agent works.
5. The combined ledger contains no prompt text and no filesystem path.

## Deliberately out of scope here

The session-attention model (one card per session, priority, badge) — decided, sequenced **after**
this. HUD option C and its escalation row, mess decay (D1/D2), mood as a today-signal, and the
pet-declares-itself manifest are separate edits that consume this ledger; they are not part of the
drain.
