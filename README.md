# Tamacodex

<p align="center">
  <img src="docs/media/hero-toast.png" alt="Tamacodex Toast hero banner" width="100%">
</p>

<p align="center">
  <strong>A tiny Codex desktop pet that grows while you work.</strong><br>
  Toast watches your runs, celebrates wins, sulks through failures, chirps in 8-bit, and evolves into the kind of companion your coding habits deserve.
</p>

<p align="center">
  <a href="#english">English</a> ·
  <a href="#中文">中文</a> ·
  <a href="#日本語">日本語</a> ·
  <a href="#한국어">한국어</a>
</p>

<p align="center">
  <img src="docs/media/tamacodex-toast-demo.gif" alt="Tamacodex animated Toast demo" width="78%">
</p>

## English

Tamacodex turns Codex into a little desktop ritual. Pick a `pawn` line, pick a `tamago` shell, wake it in Codex App, then let your actual work feed the ledger. Prompts, successful runs, failures, reviews, recovery, rest, token usage, hover, and dragging all become local growth signals.

Have fun. Make the workbench feel alive.

### What You Get

- Native Codex custom pet package: `pet.json` plus `spritesheet.webp`.
- Default Toast pawn, alternate Mais pawn, and two shells: Aurora and Pulse.
- Growth ledger with XP, stages, stats, traits, counters, and recent events.
- Frosted LCD sidecar on macOS, hidden until you hover over the mascot for 1 second.
- Short 8-bit SFX, now tuned softer at 50% amplitude.
- Privacy-friendly usage binding: Tamacodex stores numeric lengths and token counters, not raw prompts or tool output text.
- Custom pawn generator: author a small profile JSON, render a full lifecycle catalog, install it.

### Requirements

- Codex App on macOS.
- Python 3.10+ with `pip`. The installer prefers Codex's bundled Python runtime when it exists.
- Xcode Command Line Tools are recommended for the Swift/AppKit sidecar. If the helper cannot compile, the native pet still installs and the sidecar falls back quietly.

### Install In Codex

Paste this into Codex:

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

The agent should clone the repo, run the installer, and verify `~/.codex/pets/tamacodex/pet.json` plus `spritesheet.webp`.

Manual install:

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

Then enable it in Codex App:

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

For hook skills, open this repository in Codex App and enable the local plugin from `.agents/plugins/marketplace.json`. The installer also starts the sidecar supervisor, which idles until `custom:tamacodex` is selected.

### Switch Tamago Shell

`tamago` means the shell/device. Built-ins:

- `aurora`: cool cyan-violet glass.
- `pulse`: warm pink-mint glass.

Keep your current growth ledger and switch only the shell:

```bash
./install.sh --line toast --machine pulse
```

Reset growth while switching:

```bash
./install.sh --line toast --machine pulse --reset
```

### Switch Pawn

`pawn` means the creature line/form. Built-ins:

- `toast`: the default bread-bodied companion.
- `mais`: the alternate corn-colored companion.

Switch to Mais:

```bash
./install.sh --line mais --machine aurora
```

List exact forms:

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

Install an exact form, useful for screenshots or testing:

```bash
tamacodex setup --form toast_adult_worker --machine pulse --force --json
```

### Make Your Own Pawn

The custom pawn flow is deterministic after you write the profile. The renderer creates egg, hatchling, child, teen branches, adult branches, hibernation, pose sheets, shell composites, manifests, QA reports, and a catalog you can install.

1. Create a profile:

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

Save it as `custom/ducky.json`, then validate:

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
```

If you want Codex to author the JSON, ask it to use this brief:

```bash
tamacodex generate-profile \
  --prompt "Create a Tamacodex pawn named Ducky inspired by a duck" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output custom/ducky.json
```

2. Render the catalog:

```bash
tamacodex render-catalog \
  --profile custom/ducky.json \
  --output-dir build/ducky \
  --milestone M2.1 \
  --asset-version m2.1
```

3. Install it:

```bash
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

4. Inspect QA:

```bash
open build/ducky/qa/pet_contact_sheet.png
open build/ducky/qa/catalog_matrix.png
tamacodex --catalog-dir build/ducky/assets doctor --line ducky --machine pulse
```

### Growth And Care

<p align="center">
  <img src="docs/media/growth-map.png" alt="Tamacodex growth rules" width="92%">
</p>

Stages:

| Stage | Trigger |
| --- | --- |
| Egg | Start |
| Hatchling | 12 XP |
| Child | 32 XP |
| Teen | 90 XP, branch from focus/resilience/restlessness |
| Adult | 180 XP, branch from focus, resilience, quiet time, success, and energy |
| Hibernation | Energy <= 4, health <= 12, or idle minutes >= 240 |

Core event deltas:

| Event | Growth effect |
| --- | --- |
| `prompt_sent` | XP +4, energy -2, mood +1, focus +2, workRuns +1 |
| `task_success` | XP +14, energy -3, mood +7, bond +2, focus +3, completedRuns +1 |
| `task_failure` | XP +5, energy -4, mood -7, health -4, resilience +4, mess +6 |
| `review_opened` | XP +5, energy -1, mood +2, focus +2, reviews +1, mess -1 |
| `care` | XP +3, energy +5, mood +5, health +4, bond +3, mess -2 |
| `rest` | energy +10, mood +1, health +3, quietMinutes +10 |

Useful controls:

```bash
tamacodex status
tamacodex doctor
tamacodex overlay status
tamacodex overlay mute
tamacodex overlay quiet
tamacodex preview --port 8765
```

Tamacodex does not patch Codex App internals. It uses the public custom pet package shape, local plugin hooks, local session-log adaptation, and a supervised macOS sidecar.

## 中文

Tamacodex 是一个会跟着你的 Codex 工作一起成长的桌面小宠物。选择一个 `pawn` 角色线，选择一个 `tamago` 外壳，在 Codex App 里唤醒它，然后让真实工作喂养它的本地成长账本。它会因为提示词、成功、失败、Review、恢复、休息、token 用量、悬停和拖动而改变。

Have fun. 让工作台活起来。

### 安装

在 Codex 里直接说：

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

手动安装：

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

然后在 Codex App 里启用：

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

如果要使用插件技能和 hook，请在 Codex App 中打开这个仓库，并启用 `.agents/plugins/marketplace.json` 提供的本地插件。

### 切换 Tamago 外壳

`tamago` 是设备外壳：

- `aurora`：冷色青紫玻璃。
- `pulse`：暖色粉绿玻璃。

只换外壳，不清空成长：

```bash
./install.sh --line toast --machine pulse
```

切换并重新开始：

```bash
./install.sh --line toast --machine pulse --reset
```

### 切换 Pawn 角色

`pawn` 是里面的角色线：

- `toast`：默认吐司兽。
- `mais`：玉米色的 Mais。

切换到 Mais：

```bash
./install.sh --line mais --machine aurora
```

查看可用形态：

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

指定某个形态：

```bash
tamacodex setup --form toast_adult_worker --machine pulse --force --json
```

### 生成自己的 Pawn

写一个 profile，比如 `custom/ducky.json`：

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

验证、渲染、安装：

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

如果你想让 Codex 帮你写 profile：

```bash
tamacodex generate-profile \
  --prompt "生成一个叫 Ducky 的 Tamacodex，灵感来自鸭子" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output custom/ducky.json
```

然后让 Codex 根据 brief 写入 JSON，再执行上面的验证和渲染命令。

### 养育规则

成长阶段：0 XP 是蛋，12 XP 孵化，32 XP 童年，90 XP 进入 teen 分支，180 XP 进入 adult 分支。能量过低、健康过低或闲置太久会进入 hibernation。

核心事件：`prompt_sent` 加 XP 和 focus；`task_success` 大幅提升 mood、bond、XP；`task_failure` 增加 resilience 但降低 mood、health 并增加 mess；`review_opened` 提升 focus 并清理一点 mess；`care` 恢复能量、健康和羁绊；`rest` 恢复能量并累计安静时间。

## 日本語

Tamacodex は、Codex で作業するほど育つ小さなデスクトップペットです。`pawn` のキャラクターラインと `tamago` のシェルを選び、Codex App で起こすだけ。プロンプト、成功、失敗、レビュー、回復、休憩、token 使用量、ホバー、ドラッグがローカルの成長データになります。

Have fun. 作業机に小さな生命感を。

### インストール

Codex に貼り付けます：

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

手動の場合：

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

Codex App で有効化：

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

プラグインのスキルと hook を使う場合は、このリポジトリを Codex App で開き、`.agents/plugins/marketplace.json` のローカルプラグインを有効化してください。

### Tamago を切り替える

`tamago` は外側のデバイスシェルです。

- `aurora`：シアンとバイオレットのガラス調。
- `pulse`：ピンクとミントの暖かいガラス調。

```bash
./install.sh --line toast --machine pulse
```

成長データもリセットする場合：

```bash
./install.sh --line toast --machine pulse --reset
```

### Pawn を切り替える

`pawn` は中のキャラクターです。

- `toast`：デフォルトの Toast。
- `mais`：Mais の別ライン。

```bash
./install.sh --line mais --machine aurora
```

フォーム一覧：

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

特定フォームを入れる：

```bash
tamacodex setup --form toast_adult_worker --machine pulse --force --json
```

### 自分の Pawn を作る

`custom/ducky.json` を作ります：

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

検証、レンダー、インストール：

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

### 育成ルール

0 XP は egg、12 XP で hatchling、32 XP で child、90 XP で teen、180 XP で adult。energy や health が危険域になるか、長時間 idle になると hibernation に入ります。成功は mood と bond を伸ばし、失敗は resilience を伸ばしますが mess と health に影響します。`care` と `rest` で回復できます。

## 한국어

Tamacodex는 Codex 작업과 함께 자라는 작은 데스크톱 펫입니다. `pawn` 캐릭터 라인과 `tamago` 셸을 고르고 Codex App에서 깨우면 됩니다. 프롬프트, 성공, 실패, 리뷰, 회복, 휴식, token 사용량, hover, drag가 모두 로컬 성장 신호가 됩니다.

Have fun. 작업 공간을 조금 더 살아 있게 만드세요.

### 설치

Codex에 이렇게 붙여 넣으세요:

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

수동 설치:

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

Codex App에서 활성화:

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

플러그인 스킬과 hook을 쓰려면 이 저장소를 Codex App에서 열고 `.agents/plugins/marketplace.json`의 로컬 플러그인을 활성화하세요.

### Tamago 바꾸기

`tamago`는 외부 셸입니다.

- `aurora`: 시안/바이올렛 유리 느낌.
- `pulse`: 핑크/민트 유리 느낌.

```bash
./install.sh --line toast --machine pulse
```

성장 기록까지 초기화하려면:

```bash
./install.sh --line toast --machine pulse --reset
```

### Pawn 바꾸기

`pawn`은 안쪽 캐릭터 라인입니다.

- `toast`: 기본 Toast.
- `mais`: 대체 Mais 라인.

```bash
./install.sh --line mais --machine aurora
```

사용 가능한 form 보기:

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

특정 form 설치:

```bash
tamacodex setup --form toast_adult_worker --machine pulse --force --json
```

### 나만의 Pawn 만들기

`custom/ducky.json`을 만듭니다:

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

검증, 렌더, 설치:

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

### 육성 규칙

0 XP는 egg, 12 XP는 hatchling, 32 XP는 child, 90 XP는 teen, 180 XP는 adult입니다. energy나 health가 너무 낮거나 idle 시간이 너무 길면 hibernation에 들어갑니다. 성공은 mood와 bond를 키우고, 실패는 resilience를 키우지만 mess와 health에 부담을 줍니다. `care`와 `rest`로 회복할 수 있습니다.

## License

MIT
