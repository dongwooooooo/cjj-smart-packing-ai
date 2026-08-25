#!/bin/bash
# Ubuntu 에서 치수 추정 API 를 systemd 서비스로 올린다. (ubuntu 계정 기준)
#
# 코드는 이 저장소의 inference/ 가 원천이고, 허깅페이스에서는 모델 파일
# (model.safetensors, config.json)만 받는다. 셋업 중에 export_onnx.py 로
# ONNX 변환을 1회 실행해, 서비스는 ONNX Runtime 백엔드로 기동한다.
# 변환용 torch 는 이 서버의 venv 에만 설치된다 — 모델 업로더는 계속
# torch(safetensors)만 올리면 된다.
#
# 필수/선택 환경변수 (인자가 아니라 환경변수로 받는다):
#   HF_TOKEN      허깅페이스 토큰 (필수)
#   API_KEY       API 인증 키, X-API-Key 헤더 검증용 (선택, 비우면 인증 없음)
#   MODEL_SOURCE  허깅페이스 모델 저장소 (기본값: althdgk/cj_A.LTS_AI_B_2026)
#   PORT          서비스 포트 (기본값: 8000)
#
# 사용법 (레포의 deploy/ec2/ 와 inference/ 를 함께 업로드한 상태에서):
#   HF_TOKEN=hf_xxx API_KEY=xxx bash setup.sh
#
# 토큰을 CLI 인자로 넘기지 않고 환경변수로 받는 이유: 인자로 넘기면 같은 서버의
# 다른 사용자가 `ps aux` 로 토큰을 그대로 볼 수 있고, 터미널에 직접 입력하면
# ~/.bash_history 에도 남는다. 환경변수는 두 경로 어디에도 노출되지 않는다.
set -euo pipefail

: "${HF_TOKEN:?HF_TOKEN 환경변수가 필요합니다. 예: HF_TOKEN=hf_xxx bash setup.sh}"
API_KEY="${API_KEY:-}"
MODEL_SOURCE="${MODEL_SOURCE:-althdgk/cj_A.LTS_AI_B_2026}"
PORT="${PORT:-8000}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="$HOME"
APP_DIR="$HOME_DIR/app"
MODEL_DIR="$HOME_DIR/model"

# 레포에서 함께 업로드된 추론 코드 위치를 찾는다.
# 배치 예: ~/inference/ (scp -r) 또는 setup.sh 와 같은 폴더의 inference/
if [ -d "$SCRIPT_DIR/inference" ]; then
  SRC_DIR="$SCRIPT_DIR/inference"
elif [ -d "$HOME_DIR/inference" ]; then
  SRC_DIR="$HOME_DIR/inference"
else
  echo "inference/ 폴더가 없습니다. 레포의 inference/ 를 함께 업로드하세요." >&2
  echo "  scp -r -i <키> <레포>/inference <레포>/deploy/ec2/* ubuntu@<IP>:~/" >&2
  exit 1
fi

echo "== 1/6 스왑 확인"
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
MEM_GIB=$((MEM_KB / 1024 / 1024))
if [ "$MEM_GIB" -lt 2 ] && ! swapon --show | grep -q .; then
  echo "  RAM ${MEM_GIB}GiB 미만이고 스왑이 없음 — 2G 스왑 생성"
  sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab >/dev/null
else
  echo "  스왑 불필요 또는 이미 있음 — 건너뜀"
fi

echo "== 2/6 시스템 패키지"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip >/dev/null

echo "== 3/6 코드 배치 (레포 inference/ → $APP_DIR)"
mkdir -p "$APP_DIR"
cp "$SRC_DIR"/*.py "$SRC_DIR"/requirements*.txt "$APP_DIR/"

echo "== 4/6 모델 내려받기 ($MODEL_SOURCE — 모델 파일만)"
python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q huggingface_hub
"$HOME_DIR/venv/bin/python3" - "$MODEL_SOURCE" "$MODEL_DIR" <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

repo, local_dir = sys.argv[1], sys.argv[2]
p = snapshot_download(repo, token=os.environ["HF_TOKEN"], local_dir=local_dir,
                      allow_patterns=["model.safetensors", "config.json"])
print("  받음:", p)
PY

echo "== 5/6 파이썬 패키지 + ONNX 변환 (변환용 torch 포함 — 5분 안팎)"
# 의존성 목록은 레포 requirements.txt 가 원천. 첫 줄의 --extra-index-url 이
# CPU 전용 torch 휠을 받는다 — PyPI 기본 torch 는 CUDA 런타임까지 끌어온다.
"$HOME_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
"$HOME_DIR/venv/bin/python3" "$APP_DIR/export_onnx.py" "$MODEL_DIR"

echo "== 6/6 systemd 서비스 설치·기동"
sudo sed "s/--port 8000/--port ${PORT}/" "$SCRIPT_DIR/dimension-api.service" \
  | sudo tee /etc/systemd/system/dimension-api.service >/dev/null

{
  echo "API_KEY=${API_KEY}"
  echo "N_THREADS=$(nproc)"
  echo "MODEL_DIR=${MODEL_DIR}"
} | sudo tee /etc/dimension-api.env >/dev/null
sudo chmod 600 /etc/dimension-api.env

sudo systemctl daemon-reload
sudo systemctl enable dimension-api >/dev/null 2>&1
sudo systemctl restart dimension-api

echo "== 헬스 체크"
for i in $(seq 1 10); do
  if curl -fs "http://127.0.0.1:${PORT}/health" >/dev/null; then
    echo "  기동 성공 — http://127.0.0.1:${PORT}"
    exit 0
  fi
  sleep 3
done
echo "  기동 실패 — sudo journalctl -u dimension-api -n 50 으로 로그를 확인하세요" >&2
exit 1
