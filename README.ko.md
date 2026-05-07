# Tamacodex

<p align="center">
  <img src="docs/media/hero-toast.png" alt="Tamacodex Toast hero banner" width="100%">
</p>

<p align="center">
  <strong>Codex로 일할수록 함께 자라는 작은 데스크톱 펫.</strong><br>
  Toast는 성공을 축하하고, 실패를 견디고, 8-bit 사운드로 반응하면서 조금씩 당신의 작은 동료가 됩니다.
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <strong>한국어</strong>
</p>

<p align="center">
  <a href="#빠른-시작">빠른 시작</a> ·
  <a href="#바꾸기">바꾸기</a> ·
  <a href="#나만의-tamacodex-부화시키기">나만의 Tamacodex</a> ·
  <a href="#성장">성장</a>
</p>

<p align="center">
  <img src="docs/media/tamacodex-toast-demo.gif" alt="Tamacodex animated Toast demo" width="78%">
</p>

생산성 해킹 도구가 아닙니다. 책상 위의 작은 의식에 가깝습니다.

동료 라인을 고르고, tamago shell을 고르고, Codex에서 깨워 주세요. 프롬프트, 성공한 실행, 실패, 리뷰, 회복, 휴식, token 사용량, hover, drag가 모두 작은 로컬 성장 신호가 됩니다.

Have fun. Fork 하세요. 이상하게도 당신다운 무언가를 부화시켜 보세요.

## 빠른 시작

**🤖 에이전트에게 맡기는 한 줄 설치: 이 문장을 Codex에 붙여 넣으세요.**

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

**🛠 수동 설치: clone 하고, 폴더로 들어가서, 설치합니다.**

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

**✨ Codex App에서 펫을 깨웁니다.**

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

hook 기반 성장과 slash skills를 쓰고 싶다면 Codex App에서 이 repo를 열고 `.agents/plugins/marketplace.json`의 로컬 플러그인을 활성화하세요.

## 들어 있는 것

- Codex custom pet package: `pet.json` + `spritesheet.webp`
- 두 가지 동료 라인: `toast`, `mais`
- 두 가지 tamago shell: `aurora`, `pulse`
- 로컬 성장 ledger: XP, stage, stats, traits, counters, recent events
- macOS sidecar: hover 시 frosted LCD, 8-bit SFX, focus를 훔치지 않음
- 작은 profile JSON으로 나만의 Tamacodex 부화

Tamacodex는 숫자화된 사용 신호만 저장합니다. 원본 프롬프트나 도구 출력 텍스트는 저장하지 않습니다.

## 바꾸기

terminal에 익숙하지 않아도 괜찮습니다. Codex App에서 이 repo를 열고, Composer에 아래 프롬프트를 붙여 넣으면 Codex가 대신 명령을 실행합니다. 완료 후 펫이 잠들어 있으면 `Cmd+K -> Wake Pet`으로 깨워 주세요.

**🎛 tamago shell을 바꿉니다.**

**Composer에 붙여 넣을 프롬프트:**

```text
이 repo에서 Tamacodex를 Toast 라인은 유지한 채 Pulse tamago shell로 바꿔 주세요. ./install.sh --line toast --machine pulse 를 실행하고, 완료되면 언제 Wake Pet을 하면 되는지 알려 주세요.
```

**Terminal 대체 명령:**

```bash
./install.sh --line toast --machine pulse
```

**🍞 동료 라인을 바꿉니다.**

**Composer에 붙여 넣을 프롬프트:**

```text
이 repo에서 Tamacodex를 Mais 동료 라인 + Aurora shell로 바꿔 주세요. ./install.sh --line mais --machine aurora 를 실행하고, 완료되면 언제 Wake Pet을 하면 되는지 알려 주세요.
```

**Terminal 대체 명령:**

```bash
./install.sh --line mais --machine aurora
```

**🥚 새 알에서 다시 시작합니다.**

**Composer에 붙여 넣을 프롬프트:**

```text
이 repo에서 Tamacodex를 리셋하고, 새로운 Toast 알을 Aurora shell로 설치해 주세요. ./install.sh --line toast --machine aurora --reset 를 실행하고, 완료되면 언제 Wake Pet을 하면 되는지 알려 주세요.
```

**Terminal 대체 명령:**

```bash
./install.sh --line toast --machine aurora --reset
```

**🎯 특정 성장 form을 설치합니다.**

**Composer에 붙여 넣을 프롬프트:**

```text
이 repo에서 Tamacodex의 지정 form toast_adult_worker를 Pulse shell로 설치해 주세요. ./install.sh --line toast --machine pulse --form toast_adult_worker 를 실행하고, 완료되면 언제 Wake Pet을 하면 되는지 알려 주세요.
```

**Terminal 대체 명령:**

```bash
./install.sh --line toast --machine pulse --form toast_adult_worker
```

**🔎 기본 catalog에 무엇이 있는지 확인합니다.**

**Composer에 붙여 넣을 프롬프트:**

```text
이 repo에서 toast와 mais의 기본 Tamacodex form을 모두 보여 주세요. tamacodex list-forms --line toast 와 tamacodex list-forms --line mais 를 실행한 뒤, 선택지를 쉬운 한국어로 요약해 주세요.
```

**Terminal 대체 명령:**

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

## 나만의 Tamacodex 부화시키기

**🐣 작은 profile을 만듭니다: `custom/ducky.json`.**

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

**🎨 렌더링한 뒤 hatchling을 설치합니다.**

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

**🧪 Codex가 먼저 profile brief를 작성하게 할 수도 있습니다.**

```bash
tamacodex generate-profile \
  --prompt "Hatch a Tamacodex named Ducky inspired by a duck" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output custom/ducky.json
```

**🔬 선택 QA: 배포 전에 sprite sheets를 확인합니다.**

```bash
open build/ducky/qa/pet_contact_sheet.png
open build/ducky/qa/catalog_matrix.png
tamacodex --catalog-dir build/ducky/assets doctor --line ducky --machine pulse
```

## 성장

<p align="center">
  <img src="docs/media/growth-map.png" alt="Tamacodex growth rules" width="92%">
</p>

단계:

| Stage | Trigger |
| --- | --- |
| Egg | 시작 |
| Hatchling | 12 XP |
| Child | 32 XP |
| Teen | 90 XP |
| Adult | 180 XP |
| Hibernation | energy 낮음, health 낮음, 또는 긴 idle |

신호:

| Event | Effect |
| --- | --- |
| `prompt_sent` | 작은 XP, focus 상승, energy 감소 |
| `task_success` | 큰 XP, mood 상승, bond 상승 |
| `task_failure` | resilience 상승, health 감소, mess 상승 |
| `review_opened` | focus 상승, mess 감소 |
| `care` | energy, mood, health, bond 상승 |
| `rest` | energy와 health 회복 |

**🎚 유용한 작은 컨트롤.**

```bash
tamacodex status
tamacodex doctor
tamacodex overlay status
tamacodex overlay mute
tamacodex overlay quiet
tamacodex preview --port 8765
```

## 메모

Tamacodex는 Codex App 내부를 patch하지 않습니다. custom pet package contract, 로컬 plugin hooks, 로컬 session-log adaptation, 그리고 supervised macOS sidecar를 사용합니다.

MIT. PR과 이상하지만 귀여운 Tamacodex 부화 아이디어를 환영합니다.
