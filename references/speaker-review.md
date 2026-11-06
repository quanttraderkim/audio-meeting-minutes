# 화자 확인 카드

전사에서 생성한 `audio-16k.wav`, `transcription-reviewed.json`, `source.json`을 사용합니다.

## 설치

`scripts/setup.sh`로 준비한 전사 가상환경에 `sherpa-onnx`를 추가합니다. 아래 예시는 기본 가상환경 경로와 모델 경로를 사용합니다. 모델은 sherpa-onnx 공식 릴리스에서 내려받습니다. [모델 문서](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html)

```bash
"$HOME/.cache/codex-meeting-audio-env/bin/python" -m pip install 'sherpa-onnx==1.13.8'

speaker_models="$HOME/.cache/codex-meeting-audio-models"
mkdir -p "$speaker_models"
(
  set -e
  speaker_download="$(mktemp -d)"
  trap 'rm -rf "$speaker_download"' EXIT
  sherpa_releases="https://github.com/k2-fsa/sherpa-onnx/releases/download"
  curl -fL "$sherpa_releases/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" \
    -o "$speaker_download/segmentation.tar.bz2"
  tar -xOf "$speaker_download/segmentation.tar.bz2" \
    sherpa-onnx-pyannote-segmentation-3-0/model.onnx > "$speaker_download/segmentation.onnx"
  curl -fL "$sherpa_releases/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx" \
    -o "$speaker_download/wespeaker.onnx"
  mv "$speaker_download/segmentation.onnx" "$speaker_download/wespeaker.onnx" "$speaker_models/"
)
```

두 모델의 다운로드 용량은 합계 약 34 MB입니다. 다른 모델 폴더를 사용한다면 `speaker_review.py "<회의 결과 폴더>" --models "<모델 폴더>" prepare …`처럼 하위 명령 앞에 경로를 지정합니다. 화자 분리와 비교는 로컬에서 실행합니다.

## 구간 확인

1. 회의록에서 화자 확인이 중요한 부분을 정하고, 전사 시각을 초 단위 START:END로 지정합니다.
2. `prepare`로 한 사람이 말하는 것으로 분류된 구간을 추립니다. 이미 처리한 녹음과 모델의 해시가 같으면 화자 구분 결과를 재사용합니다. 짧은 응답의 이름만 확인할 때는 `prepare`에 `--min-seconds 1.5`를 추가할 수 있습니다.
3. 같은 미리보기 서버의 `speaker-review.html#<카드 ID>` 듣기 링크를 제공합니다. `render_preview.py`를 다시 실행하면 회의록 화면에도 링크가 생깁니다. 확인 카드 도구를 사용할 수 있으면 시각, 짧은 발언, 참석자 후보와 모르겠음·겹침을 제시합니다. 일반 채팅 답변으로도 확인할 수 있습니다. 내용의 전사가 불확실하면 누가 말했는지와 무엇을 말했는지를 따로 확인합니다.
4. 답변을 `confirm`으로 저장한 뒤 회의록과 미리보기에 해당 구간의 이름을 반영합니다. 모델이 서로 다른 사람의 발언을 같은 그룹으로 묶을 수 있으므로, 같은 그룹에서도 이름을 표시할 구간마다 확인합니다.

```bash
"<가상환경>/bin/python" "<스킬>/scripts/speaker_review.py" "<회의 결과 폴더>" prepare 120:150 240:275
"<가상환경>/bin/python" "<스킬>/scripts/speaker_review.py" "<회의 결과 폴더>" confirm "<카드 ID>" --name "<확인된 이름>"
"<가상환경>/bin/python" "<스킬>/scripts/speaker_review.py" "<회의 결과 폴더>" confirm "<카드 ID>" --status "겹침"
```

## 다음 회의에 재사용

정확한 구간의 화자를 확인한 뒤, 보관 목적에 대해 해당 화자의 동의를 확인합니다. `--allow-reuse`는 재사용에 동의한 단일 화자 구간 중 6초 이상인 경우에 사용합니다. 로컬 테스트에 동의한 구간은 `--test-only`로 저장할 수 있으며, 이후 회의의 후보 비교에서는 제외됩니다.

```bash
"<가상환경>/bin/python" "<스킬>/scripts/speaker_review.py" "<회의 결과 폴더>" enroll "<카드 ID>" --allow-reuse
"<가상환경>/bin/python" "<스킬>/scripts/speaker_review.py" "<회의 결과 폴더>" enroll "<카드 ID>" --test-only
"<가상환경>/bin/python" "<스킬>/scripts/speaker_review.py" "<회의 결과 폴더>" forget "<카드 ID>"
```

스크립트는 해당 원음 구간에서 메모리로 특징을 추출합니다. 등록 파일의 기본 위치는 미리보기 서버 밖의 `~/.local/share/audio-meeting-minutes/speaker-samples`이며, 파일 권한은 0600입니다. 등록 후 이름을 정정하거나 겹침으로 표시하면 해당 구간의 등록도 제거합니다. `forget`은 등록 파일을 삭제하고 이번 회의의 화자 확인은 보존합니다.

다음 회의에서는 다른 원본에서 등록했고 같은 모델을 사용한 음성 특징만 참석자 후보와 비교합니다. 유사도는 후보 순서를 정하는 데 사용하며, 실명은 해당 구간을 확인한 뒤 반영합니다. 음성 특징, 원음, 카드 응답은 로컬에서 관리합니다.
