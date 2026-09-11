# TamaHermes

エージェントの作業と一緒に育つデスクトップペットです。現在は
[Hermes Agent](https://github.com/NousResearch/hermes-agent) 向けのペットです。

[TamaCodex](https://github.com/Alichua/TamaCodex)（MIT、末尾にクレジット）から移植した
もので、アートと成長モデルは同じです。成長のきっかけは Hermes 自身のフックから
供給されます。元の Codex 向けの動作もそのまま残っています。

ドキュメントは英語版のみを保守しています:
**[README.md](README.md)** / **[README.hermes.md](README.hermes.md)**

## クイックスタート

```bash
git clone https://github.com/ahrazzle/TamaHermes.git
cd TamaHermes
./hermes/install-hermes.sh --line toast --machine aurora
hermes plugins enable tamahermes
```

`--petdex-activate` を付けるとデスクトップにも表示されます。
`./hermes/install-all-profiles.sh` はすべての Hermes プロファイルに一度にインストールします。

## 補足

この日本語ページは旧英語 README の翻訳でした。旧版は Codex を前提としており内容が
古くなったため、ここでは入口だけを残しています。

MIT。
