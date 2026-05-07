# Tamacodex

<p align="center">
  <img src="docs/media/hero-toast.png" alt="Tamacodex Toast hero banner" width="100%">
</p>

<p align="center">
  <strong>一个会跟着 Codex 工作一起成长的桌面小宠物。</strong><br>
  Toast 会庆祝你的成功、陪你熬过失败、发出 8-bit 小音效，然后慢慢变成你的伙伴。
</p>

<p align="center">
  <strong>目前仅支持 macOS。</strong><br>
  Tamacodex 使用 macOS sidecar 来提供悬停状态、SFX 和受监督的后台成长。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <strong>简体中文</strong> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#切换配置">切换配置</a> ·
  <a href="#孵化你自己的-tamacodex">孵化你自己的 Tamacodex</a> ·
  <a href="#成长">成长</a>
</p>

<p align="center">
  <img src="docs/media/tamacodex-toast-demo.gif" alt="Tamacodex animated Toast demo" width="78%">
</p>

它不是效率工具。它是一个小小的桌面仪式。

选一条伙伴线，选一个 tamago 外壳，在 Codex 里唤醒它，然后让你的真实工作喂养它。提示词、成功运行、失败、Review、恢复、休息、token 用量、悬停和拖拽，都会变成轻量的本地成长信号。

Have fun。Fork 它。孵化一点很像你的东西。

## 快速开始

**🍎 当前支持平台：macOS。**

**🤖 一句话交给 Codex 安装：把这句贴进 Codex。**

```text
Install https://github.com/Alichua/TamaCodex with Toast and Aurora.
```

**🛠 手动安装：clone，进入目录，然后安装。**

```bash
git clone https://github.com/Alichua/TamaCodex.git
cd tamacodex
./install.sh --line toast --machine aurora
```

**✨ 在 Codex App 里唤醒它。**

```text
Settings -> Appearance -> Pet -> Custom Pet -> Tamacodex
Cmd+K -> Wake Pet
```

如果你想启用 hook 驱动的成长和 slash skills，请在 Codex App 里打开这个仓库，并启用 `.agents/plugins/marketplace.json` 里的本地插件。

## 里面有什么

- Codex custom pet package：`pet.json` + `spritesheet.webp`
- 两条伙伴线：`toast`、`mais`
- 两个 tamago 外壳：`aurora`、`pulse`
- 本地成长账本：XP、阶段、数值、特质、计数器、最近事件
- macOS sidecar：悬停时显示毛玻璃 LCD、8-bit SFX、不抢焦点
- 用小型 profile JSON 孵化自定义 Tamacodex

Tamacodex 只保存数值化使用信号，不保存原始提示词或工具输出文本。

## 切换配置

你不需要会用 terminal。打开 Codex App 里的这个仓库，新建一条 Composer 消息，把下面的提示词贴进去，让 Codex 替你运行命令。完成后，如果宠物还在睡觉，用 `Cmd+K -> Wake Pet` 唤醒它。

**🎛 切换 tamago 外壳。**

**Composer 里直接粘贴：**

```text
在这个 repo 里，把 Tamacodex 切换成 Toast + Pulse 外壳。请运行 ./install.sh --line toast --machine pulse，完成后告诉我什么时候需要 Wake Pet。
```

**Terminal 备用命令：**

```bash
./install.sh --line toast --machine pulse
```

**🍞 切换伙伴线。**

**Composer 里直接粘贴：**

```text
在这个 repo 里，把 Tamacodex 切换成 Mais 伙伴线 + Aurora 外壳。请运行 ./install.sh --line mais --machine aurora，完成后告诉我什么时候需要 Wake Pet。
```

**Terminal 备用命令：**

```bash
./install.sh --line mais --machine aurora
```

**🥚 从一颗新蛋重新开始。**

**Composer 里直接粘贴：**

```text
在这个 repo 里，重置 Tamacodex，并安装一颗新的 Toast + Aurora 蛋。请运行 ./install.sh --line toast --machine aurora --reset，完成后告诉我什么时候需要 Wake Pet。
```

**Terminal 备用命令：**

```bash
./install.sh --line toast --machine aurora --reset
```

**🎯 安装指定的成长形态。**

**Composer 里直接粘贴：**

```text
在这个 repo 里，安装指定形态 toast_adult_worker，并使用 Pulse 外壳。请运行 ./install.sh --line toast --machine pulse --form toast_adult_worker，完成后告诉我什么时候需要 Wake Pet。
```

**Terminal 备用命令：**

```bash
./install.sh --line toast --machine pulse --form toast_adult_worker
```

**🔎 查看内置目录里有哪些形态。**

**Composer 里直接粘贴：**

```text
在这个 repo 里，列出 toast 和 mais 的所有内置 Tamacodex 形态。请运行 tamacodex list-forms --line toast 和 tamacodex list-forms --line mais，然后用普通中文总结可选项。
```

**Terminal 备用命令：**

```bash
tamacodex list-forms --line toast
tamacodex list-forms --line mais
```

## 孵化你自己的 Tamacodex

**🐣 创建一个小 profile：`custom/ducky.json`。**

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

**🎨 渲染它，然后安装这个 hatchling。**

```bash
tamacodex generate-profile --input custom/ducky.json --output custom/ducky.json
tamacodex render-catalog --profile custom/ducky.json --output-dir build/ducky --milestone M2.1 --asset-version m2.1
./install.sh --catalog-dir build/ducky/assets --line ducky --machine pulse --reset
```

**🧪 想先让 Codex 起草 profile brief？**

```bash
tamacodex generate-profile \
  --prompt "Hatch a Tamacodex named Ducky inspired by a duck" \
  --brief-output /tmp/ducky-profile-brief.md \
  --output custom/ducky.json
```

**🔬 可选 QA：发布前看一眼 sprite sheets。**

```bash
open build/ducky/qa/pet_contact_sheet.png
open build/ducky/qa/catalog_matrix.png
tamacodex --catalog-dir build/ducky/assets doctor --line ducky --machine pulse
```

## 成长

<p align="center">
  <img src="docs/media/growth-map.png" alt="Tamacodex growth rules" width="92%">
</p>

阶段：

| 阶段 | 触发条件 |
| --- | --- |
| Egg | 起始 |
| Hatchling | 12 XP |
| Child | 32 XP |
| Teen | 90 XP |
| Adult | 180 XP |
| Hibernation | 能量低、健康低，或长时间 idle |

信号：

| 事件 | 效果 |
| --- | --- |
| `prompt_sent` | 少量 XP，focus 上升，energy 下降 |
| `task_success` | 大量 XP，mood 上升，bond 上升 |
| `task_failure` | resilience 上升，health 下降，mess 上升 |
| `review_opened` | focus 上升，mess 下降 |
| `care` | energy、mood、health、bond 上升 |
| `rest` | energy 和 health 恢复 |

**🎚 常用小控制。**

```bash
tamacodex status
tamacodex doctor
tamacodex overlay status
tamacodex overlay mute
tamacodex overlay quiet
tamacodex preview --port 8765
```

## 备注

Tamacodex 不会 patch Codex App 内部。它使用 custom pet package contract、本地 plugin hooks、本地 session-log adaptation，以及一个受监督的 macOS sidecar。

MIT。欢迎 PR，也欢迎奇怪但可爱的 Tamacodex 孵化点子。
