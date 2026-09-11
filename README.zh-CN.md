# TamaHermes

一个会跟着 agent 工作一起成长的桌面小宠物。现在它是
[Hermes Agent](https://github.com/NousResearch/hermes-agent) 的宠物。

TamaHermes 由 [TamaCodex](https://github.com/Alichua/TamaCodex)（MIT，见下方署名）
移植而来，美术和成长模型不变，成长信号改由 Hermes 自己的 hook 提供。原来的 Codex
目标仍然可用，做法没有改动。

文档只维护英文版，并以英文版为准:
**[README.md](README.md)** 和 **[README.hermes.md](README.hermes.md)**

## 快速开始

```bash
git clone https://github.com/ahrazzle/TamaHermes.git
cd TamaHermes
./hermes/install-hermes.sh --line toast --machine aurora
hermes plugins enable tamahermes
```

加上 `--petdex-activate` 可以让它同时浮在桌面上。
`./hermes/install-all-profiles.sh` 会一次安装到所有 Hermes profile。

## 说明

这份中文文档原来是一份旧英文 README 的翻译。旧版以 Codex 为主，内容已经过时，
所以这里只保留入口。

MIT。
