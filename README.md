# Audio Meeting Minutes

한국어 회의 녹음이나 전사문을 요약·상세 회의록으로 정리하는 Codex 스킬입니다. 의견 차이, 결정 사항, 제안과 미결 사항을 구분하고, 주요 내용을 원음의 해당 시각으로 연결합니다.

음성 전사는 Apple Silicon Mac에서 로컬로 실행합니다. 요약과 상세 회의록은 Codex가 전체 전사를 읽고 작성합니다.

## 설치

Codex에 저장소 주소와 함께 아래처럼 요청합니다.

> https://github.com/quanttraderkim/audio-meeting-minutes 를 내 Codex 스킬로 설치해줘. 전사에 필요한 실행환경도 확인해줘.

직접 설치할 때는 다음 명령을 사용합니다.

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
git clone https://github.com/quanttraderkim/audio-meeting-minutes.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/audio-meeting-minutes"
```

설치 후 새 채팅에서 사용합니다.

> $audio-meeting-minutes 다운로드 폴더의 회의녹음.m4a를 전사해서 회의록 만들어줘. 참석자는 A, B, C야. 의견이 갈린 부분은 각각 정리해줘.

회의일과 발언자를 알고 있으면 함께 알려주세요. 참석자 명단과 음성의 실명 연결은 따로 확인하며, 파일 생성일을 실제 회의일로 확정하지 않습니다.

이미 Git으로 설치했다면 설치 폴더에서 `git pull --ff-only`를 실행한 뒤 새 채팅에서 사용합니다.

## 실행 환경

- 음성 전사 도구는 Apple Silicon Mac과 Python 3.10 이상이 필요합니다.
- 첫 실행 시 Python 패키지와 공개 Whisper 모델을 내려받습니다. 모델 다운로드 용량과 실행 시간은 선택한 모델에 따라 달라집니다.
- 다른 OS에서도 전사문을 제공하면 같은 회의록 작성 규칙을 적용할 수 있습니다.

다른 Python 실행 파일이나 가상환경 경로를 지정할 수 있습니다.

```bash
bash scripts/setup.sh /path/to/python3 /path/to/meeting-env
```

인자를 생략하면 python3와 ~/.cache/codex-meeting-audio-env를 사용합니다. 자세한 절차는 [SKILL.md](SKILL.md)에 있습니다.

## 화자 확인

> $audio-meeting-minutes 이 회의에서 누가 말했는지 불확실한 부분을 들을 수 있게 해줘. 확인한 발언자만 회의록에 표시해줘.

전사 후 화자 구간을 로컬에서 분리합니다. 확인이 필요한 구간은 미리보기에서 듣고, 확인한 이름을 해당 구간의 회의록에 반영합니다. 화자 동의를 받고 음성 특징을 로컬에 등록하면 이후 회의에서 참석자 중 비교 후보를 보여줍니다. 실명은 구간별 확인을 거쳐 반영합니다. 이 기능을 사용할 때만 별도 패키지와 모델을 설치합니다. [설치와 사용 절차](references/speaker-review.md)

## 생성되는 자료

| 파일 | 내용 |
|---|---|
| shared.md | 공유용 요약 |
| details.md | 주제별 상세 논의와 의견 차이 |
| transcript.txt | 시간별 전사 |
| transcript.html | 전사 검색과 원음 재생 |
| index.html | 요약·상세 회의록 미리보기 |
| speaker-review.html | 화자 확인을 요청했을 때 생성되는 구간별 듣기 화면 |
| source.json | 원본 해시, 경로와 회의 메타데이터 |

전사·검증·재전사 JSON과 변환한 오디오도 결과 폴더에 저장됩니다. 저장되는 원본 경로, 참석자, 음성과 전사에는 사용자가 처리한 정보가 담기므로 결과 폴더를 로컬에서 관리합니다. 저장소의 .gitignore는 outputs 폴더와 알려진 결과 파일을 제외합니다.

기본 스크립트는 음성을 공개 모델 서비스로 전송하는 API를 사용하지 않으며, HTML 미리보기 서버는 127.0.0.1에서 엽니다. 다른 채팅 전달이나 외부 게시에는 해당 사용자의 요청이 필요합니다.

## 검증 범위

macOS arm64와 Python 3.12에서 짧은 한국어 음성을 사용해 전사, 동일 원본 이어하기, 구간 재전사, 다른 원본·전체 무음 차단, 원본 보존, HTML과 오디오 구간 응답을 확인했습니다.

화자 확인은 macOS arm64에서 별도로 만든 합성 음성으로 확인했습니다. 구간 분리, 확인 기록, 로컬 등록·삭제, 다른 녹음의 후보 표시, 짧은 테스트용 표본 제외를 점검했습니다.

전체 전사를 검토하고 중요한 숫자·날짜·가격·합의 여부를 원음과 대조하는 과정은 Codex와 사용자가 수행합니다. 다른 기기에서는 설치와 실행을 확인해야 하며, 개별 회의록은 원음 검토가 필요합니다.

## 업데이트 내역

날짜별 변경 사항은 [CHANGELOG.md](CHANGELOG.md)에 정리합니다.

## 라이선스

[MIT License](LICENSE). 설치되는 Python 패키지와 다운로드한 모델에는 각 배포물의 라이선스가 적용됩니다.
