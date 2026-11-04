#!/usr/bin/env bash
set -euo pipefail
meeting_python="${1:-python3}"
meeting_env="${2:-${HOME}/.cache/codex-meeting-audio-env}"
meeting_skill="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"${meeting_python}" - <<'PY'
import platform,sys
if platform.system() != "Darwin" or platform.machine() != "arm64":
    raise SystemExit("음성 전사 도구는 Apple Silicon Mac용입니다. 제공된 전사문에는 SKILL.md 작성 규칙을 사용할 수 있습니다.")
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 이상이 필요합니다.")
PY
if [ ! -x "${meeting_env}/bin/python" ]; then
  "${meeting_python}" -m venv "${meeting_env}"
fi
"${meeting_env}/bin/python" -m pip install -r "${meeting_skill}/requirements.txt"
printf '\n가상환경 경로\n%s\n' "${meeting_env}"
