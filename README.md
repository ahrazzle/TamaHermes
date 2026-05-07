# Tamacodex

<p align="center">
  <img src="docs/media/hero-toast.png" alt="Tamacodex Toast hero banner" width="100%">
</p>

<p align="center">
  <strong>A tiny Codex desktop pet that grows while you work.</strong><br>
  Toast celebrates your runs, survives your failures, chirps in 8-bit, and slowly becomes yours.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#switch-things">Switch Things</a> ·
  <a href="#make-a-pawn">Make a Pawn</a> ·
  <a href="#growth">Growth</a> ·
  <a href="#languages">中文 / 日本語 / 한국어</a>
</p>

<p align="center">
  <img src="docs/media/tamacodex-toast-demo.gif" alt="Tamacodex animated Toast demo" width="78%">
</p>

Not a productivity hack. A little desk ritual.

Pick a `pawn`, pick a `tamago`, wake it in Codex, and let your real work feed the pet. Prompts, successful runs, failures, reviews, recovery, rest, token usage, hover, and drag all become tiny local growth signals.

Have fun. Fork it. Hatch weird little companions.

## Quick Start

Ask Codex to install it:

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

Or install by hand:

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

Then wake it:

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

Open this repo in Codex App and enable the local plugin from `.agents/plugins/marketplace.json` if you want hook-powered growth and slash skills.

## What's Inside

- Codex custom pet package: `pet.json` + `spritesheet.webp`
- Two pawn lines: `toast`, `mais`
- Two tamago shells: `aurora`, `pulse`
- Local growth ledger: XP, stages, stats, traits, counters, recent events
- macOS sidecar: frosted LCD on hover, 8-bit SFX, no focus stealing
- Custom pawn generator from small profile JSON files

Tamacodex stores numeric usage signals, not raw prompts or tool output text.

## Switch Things

Change the shell:

```bash
./install.sh --line toast --machine pulse
```

Change the pawn:

```bash
./install.sh --line mais --machine aurora
```

Start fresh:

```bash
./install.sh --line toast --machine aurora --reset
```

Install an exact form:

```bash
tamacodex setup --form toast_adult_worker --machine pulse --force --json
```

See what ships:

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

## Make A Pawn

Create `custom/ducky.json`:

```json
{
  "id": "ducky",
  "displayName": "Ducky",
  "inspiration": "a duck",
  "description": "A duck-inspired Tamacodex companion.",
  "family": "duck",
  "palette": {
    "main": "#fff4a8",
    "shade": "#d59a3a"
  }
}
```

Render and install:

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

Want Codex to draft the profile?

```bash
tamacodex generate-profile \
  --prompt "Create a Tamacodex pawn named Ducky inspired by a duck" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output custom/ducky.json
```

QA is there when you want it:

```bash
open build/ducky/qa/pet_contact_sheet.png
open build/ducky/qa/catalog_matrix.png
tamacodex --catalog-dir build/ducky/assets doctor --line ducky --machine pulse
```

## Growth

<p align="center">
  <img src="docs/media/growth-map.png" alt="Tamacodex growth rules" width="92%">
</p>

Stages:

| Stage | Trigger |
| --- | --- |
| Egg | start |
| Hatchling | 12 XP |
| Child | 32 XP |
| Teen | 90 XP |
| Adult | 180 XP |
| Hibernation | low energy, low health, or long idle |

Signals:

| Event | Effect |
| --- | --- |
| `prompt_sent` | small XP, focus up, energy down |
| `task_success` | big XP, mood up, bond up |
| `task_failure` | resilience up, health down, mess up |
| `review_opened` | focus up, mess down |
| `care` | energy, mood, health, bond up |
| `rest` | energy and health recover |

Useful little controls:

```bash
tamacodex status
tamacodex doctor
tamacodex overlay status
tamacodex overlay mute
tamacodex overlay quiet
tamacodex preview --port 8765
```

## Languages

**中文**
Tamacodex 是一个会跟着 Codex 工作成长的桌面小宠物。选 `pawn`，选 `tamago`，安装后在 Codex 里唤醒。提示词、成功、失败、Review、休息和 token 用量都会变成成长信号。Have fun，欢迎 fork 出自己的小东西。

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

**日本語**
Tamacodex は Codex の作業と一緒に育つ小さなデスクトップペットです。`pawn` と `tamago` を選び、Codex App で起こすだけ。成功も失敗も、少しずつ成長になります。Have fun.

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

**한국어**
Tamacodex는 Codex 작업과 함께 자라는 작은 데스크톱 펫입니다. `pawn`과 `tamago`를 고르고 Codex App에서 깨우면 됩니다. 성공, 실패, 리뷰, 휴식이 모두 성장 신호가 됩니다. Have fun.

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

## Notes

Tamacodex does not patch Codex App internals. It uses the custom pet package contract, local plugin hooks, local session-log adaptation, and a supervised macOS sidecar.

MIT. PRs and strange pawn ideas welcome.
