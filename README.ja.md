# Tamacodex

<p align="center">
  <img src="docs/media/hero-toast.png" alt="Tamacodex Toast hero banner" width="100%">
</p>

<p align="center">
  <strong>Codex で作業するほど育つ、小さなデスクトップペット。</strong><br>
  Toast は成功を祝って、失敗を一緒に乗り越えて、8-bit の音で反応しながら、少しずつあなたの相棒になります。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <strong>日本語</strong> ·
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="#クイックスタート">クイックスタート</a> ·
  <a href="#切り替え">切り替え</a> ·
  <a href="#自分だけの-tamacodex-を孵化する">自分だけの Tamacodex</a> ·
  <a href="#成長">成長</a>
</p>

<p align="center">
  <img src="docs/media/tamacodex-toast-demo.gif" alt="Tamacodex animated Toast demo" width="78%">
</p>

これは生産性ハックではありません。机の上の小さな儀式です。

相棒ラインを選び、tamago シェルを選び、Codex で起こしてください。プロンプト、成功した実行、失敗、レビュー、回復、休憩、token 使用量、ホバー、ドラッグが、軽いローカル成長シグナルになります。

Have fun. Fork して、自分らしい何かを孵化させてください。

## クイックスタート

**🤖 エージェントに任せるインストール: これを Codex に貼り付けます。**

```text
Install https://github.com/Alichua/tamacodex with Toast and Aurora.
```

**🛠 手動インストール: clone して、入って、インストールします。**

```bash
git clone https://github.com/Alichua/tamacodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

**✨ Codex App でペットを起こします。**

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

hook による成長と slash skills を使いたい場合は、Codex App でこの repo を開き、`.agents/plugins/marketplace.json` のローカルプラグインを有効にしてください。

## 入っているもの

- Codex custom pet package: `pet.json` + `spritesheet.webp`
- 2 つの相棒ライン: `toast`, `mais`
- 2 つの tamago シェル: `aurora`, `pulse`
- ローカル成長台帳: XP、ステージ、ステータス、特性、カウンター、最近のイベント
- macOS sidecar: ホバー時の frosted LCD、8-bit SFX、フォーカスを奪わない
- 小さな profile JSON から自分の Tamacodex を孵化

Tamacodex は数値化された利用シグナルだけを保存します。元のプロンプトやツール出力テキストは保存しません。

## 切り替え

terminal に慣れていなくても大丈夫です。Codex App でこの repo を開き、Composer に下のプロンプトを貼り付ければ、Codex が代わりにコマンドを実行します。完了後、ペットが眠っている場合は `Cmd+K -> Wake Pet` で起こしてください。

**🎛 tamago シェルを切り替えます。**

**Composer に貼り付けるプロンプト:**

```text
この repo で、Tamacodex を Toast のまま Pulse tamago シェルに切り替えてください。./install.sh --line toast --machine pulse を実行し、完了したら Wake Pet するタイミングを教えてください。
```

**Terminal 用の予備コマンド:**

```bash
./install.sh --line toast --machine pulse
```

**🍞 相棒ラインを切り替えます。**

**Composer に貼り付けるプロンプト:**

```text
この repo で、Tamacodex を Mais 相棒ライン + Aurora シェルに切り替えてください。./install.sh --line mais --machine aurora を実行し、完了したら Wake Pet するタイミングを教えてください。
```

**Terminal 用の予備コマンド:**

```bash
./install.sh --line mais --machine aurora
```

**🥚 新しい卵から始めます。**

**Composer に貼り付けるプロンプト:**

```text
この repo で、Tamacodex をリセットし、新しい Toast の卵を Aurora シェルでインストールしてください。./install.sh --line toast --machine aurora --reset を実行し、完了したら Wake Pet するタイミングを教えてください。
```

**Terminal 用の予備コマンド:**

```bash
./install.sh --line toast --machine aurora --reset
```

**🎯 特定の成長フォームをインストールします。**

**Composer に貼り付けるプロンプト:**

```text
この repo で、Tamacodex の指定フォーム toast_adult_worker を Pulse シェルでインストールしてください。./install.sh --line toast --machine pulse --form toast_adult_worker を実行し、完了したら Wake Pet するタイミングを教えてください。
```

**Terminal 用の予備コマンド:**

```bash
./install.sh --line toast --machine pulse --form toast_adult_worker
```

**🔎 付属カタログのフォームを確認します。**

**Composer に貼り付けるプロンプト:**

```text
この repo で、toast と mais の内蔵 Tamacodex フォームを一覧してください。tamacodex list-forms --line toast と tamacodex list-forms --line mais を実行し、選択肢をわかりやすい日本語でまとめてください。
```

**Terminal 用の予備コマンド:**

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

## 自分だけの Tamacodex を孵化する

**🐣 小さな profile を作ります: `custom/ducky.json`。**

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

**🎨 レンダリングして、hatchling をインストールします。**

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

**🧪 先に Codex に profile brief を作ってもらうこともできます。**

```bash
tamacodex generate-profile \
  --prompt "Hatch a Tamacodex named Ducky inspired by a duck" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output custom/ducky.json
```

**🔬 任意の QA: 公開前に sprite sheets を確認します。**

```bash
open build/ducky/qa/pet_contact_sheet.png
open build/ducky/qa/catalog_matrix.png
tamacodex --catalog-dir build/ducky/assets doctor --line ducky --machine pulse
```

## 成長

<p align="center">
  <img src="docs/media/growth-map.png" alt="Tamacodex growth rules" width="92%">
</p>

ステージ:

| Stage | Trigger |
| --- | --- |
| Egg | 開始 |
| Hatchling | 12 XP |
| Child | 32 XP |
| Teen | 90 XP |
| Adult | 180 XP |
| Hibernation | energy 低下、health 低下、または長時間 idle |

シグナル:

| Event | Effect |
| --- | --- |
| `prompt_sent` | 小さな XP、focus 上昇、energy 低下 |
| `task_success` | 大きな XP、mood 上昇、bond 上昇 |
| `task_failure` | resilience 上昇、health 低下、mess 上昇 |
| `review_opened` | focus 上昇、mess 低下 |
| `care` | energy、mood、health、bond 上昇 |
| `rest` | energy と health が回復 |

**🎚 便利な小さな操作。**

```bash
tamacodex status
tamacodex doctor
tamacodex overlay status
tamacodex overlay mute
tamacodex overlay quiet
tamacodex preview --port 8765
```

## メモ

Tamacodex は Codex App の内部を patch しません。custom pet package contract、ローカル plugin hooks、ローカル session-log adaptation、監視付き macOS sidecar を使います。

MIT。PR と、少し変わった Tamacodex の孵化アイデアを歓迎します。
