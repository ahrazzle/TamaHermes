# TamaHermes

에이전트 작업과 함께 자라는 데스크톱 펫입니다. 지금은
[Hermes Agent](https://github.com/NousResearch/hermes-agent)용 펫입니다.

[TamaCodex](https://github.com/Alichua/TamaCodex)(MIT, 아래 크레딧)에서 이식했고 아트와
성장 모델은 같습니다. 성장 신호는 Hermes 자체 훅에서 들어옵니다. 원래의 Codex 대상도
그대로 남아 있습니다.

문서는 영어판만 관리합니다:
**[README.md](README.md)** / **[README.hermes.md](README.hermes.md)**

## 빠른 시작

```bash
git clone https://github.com/ahrazzle/TamaHermes.git
cd TamaHermes
./hermes/install-hermes.sh --line toast --machine aurora
hermes plugins enable tamahermes
```

`--petdex-activate`를 추가하면 데스크톱에도 표시됩니다.
`./hermes/install-all-profiles.sh`는 모든 Hermes 프로필에 한 번에 설치합니다.

## 참고

이 한국어 문서는 예전 영어 README의 번역본이었습니다. 예전 판은 Codex를 전제로 했고
내용이 오래되어 입구만 남겨 두었습니다.

MIT.
