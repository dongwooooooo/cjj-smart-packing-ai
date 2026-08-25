#!/bin/bash
# 흰 촬영 돔 배경 전용 고속 모델을 같은 인스턴스의 별도 포트에 추가로 올린다.
#
# 경고: 이 모델은 흰 촬영 돔 배경 전용이다. 다른 배경에서 쓰면 부피 오차가
# 394~519% 까지 벌어진다. 촬영 환경이 흰 돔이 아니면 이 스크립트를 쓰지 않는다.
#
# 사전 조건: setup.sh 로 메인 API(기본 포트 8000)가 이미 떠 있고 ~/venv 가
# 준비돼 있어야 한다. 이 스크립트는 패키지를 다시 설치하지 않고 venv 를 그대로 쓴다.
#
# 필수/선택 환경변수 (인자가 아니라 환경변수로 받는다):
#   HF_TOKEN      허깅페이스 토큰 (필수)
#   API_KEY       API 인증 키, X-API-Key 헤더 검증용 (선택, 비우면 인증 없음)
#   MODEL_SOURCE  허깅페이스 모델 저장소 (기본값: ek09/logistics-dimension-dome-fast)
#   PORT          서비스 포트 (기본값: 8001)
#
# 사용법:
#   HF_TOKEN=hf_xxx bash run_dome.sh
#
# 토큰을 CLI 인자로 넘기지 않고 환경변수로 받는 이유: 인자로 넘기면 같은 서버의
# 다른 사용자가 `ps aux` 로 토큰을 그대로 볼 수 있고, 터미널에 직접 입력하면
# ~/.bash_history 에도 남는다. 환경변수는 두 경로 어디에도 노출되지 않는다.
set -euo pipefail

: "${HF_TOKEN:?HF_TOKEN 환경변수가 필요합니다. 예: HF_TOKEN=hf_xxx bash run_dome.sh}"
API_KEY="${API_KEY:-}"
MODEL_SOURCE="${MODEL_SOURCE:-ek09/logistics-dimension-dome-fast}"
PORT="${PORT:-8001}"

HOME_DIR="$HOME"
MODEL_DIR="$HOME_DIR/model_dome"

if [ ! -x "$HOME_DIR/venv/bin/uvicorn" ]; then
  echo "  $HOME_DIR/venv 가 없습니다. 먼저 setup.sh 를 실행하세요." >&2
  exit 1
fi

echo "== 1/2 모델·코드 내려받기 ($MODEL_SOURCE)"
"$HOME_DIR/venv/bin/python3" - "$MODEL_SOURCE" "$MODEL_DIR" <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

repo, local_dir = sys.argv[1], sys.argv[2]
p = snapshot_download(repo, token=os.environ["HF_TOKEN"], local_dir=local_dir)
print("  받음:", p)
PY

echo "== 2/2 서버 기동 (포트 $PORT)"
cd "$MODEL_DIR"
CORES=$(nproc)
N_THREADS="$CORES" API_KEY="$API_KEY" nohup "$HOME_DIR/venv/bin/uvicorn" server:app \
  --host 0.0.0.0 --port "$PORT" > "$HOME_DIR/api_dome.log" 2>&1 &
disown

sleep 20
if curl -fs "http://127.0.0.1:${PORT}/health" >/dev/null; then
  echo "  기동 성공 — http://127.0.0.1:${PORT}"
else
  echo "  기동 실패 — $HOME_DIR/api_dome.log 를 확인하세요" >&2
  exit 1
fi
