#!/bin/bash
# Ubuntu 에서 치수 추정 API 를 systemd 서비스로 올린다. (ubuntu 계정 기준)
#
# 필수/선택 환경변수 (인자가 아니라 환경변수로 받는다):
#   HF_TOKEN      허깅페이스 토큰 (필수)
#   API_KEY       API 인증 키, X-API-Key 헤더 검증용 (선택, 비우면 인증 없음)
#   MODEL_SOURCE  허깅페이스 모델 저장소 (기본값: althdgk/cj_A.LTS_AI_B_2026)
#   PORT          서비스 포트 (기본값: 8000)
#
# 사용법:
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

echo "== 1/5 스왑 확인"
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

echo "== 2/5 시스템 패키지"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip >/dev/null

echo "== 3/5 모델·코드 내려받기 ($MODEL_SOURCE)"
python3 -m venv "$HOME_DIR/venv"
"$HOME_DIR/venv/bin/pip" install -q --upgrade pip
"$HOME_DIR/venv/bin/pip" install -q huggingface_hub
"$HOME_DIR/venv/bin/python3" - "$MODEL_SOURCE" "$HOME_DIR/model" <<'PY'
import os
import sys
from huggingface_hub import snapshot_download

repo, local_dir = sys.argv[1], sys.argv[2]
p = snapshot_download(repo, token=os.environ["HF_TOKEN"], local_dir=local_dir)
print("  받음:", p)
PY

echo "== 4/5 파이썬 패키지 (CPU 전용 torch — 5~8분)"
# 의존성 목록은 모델 저장소의 requirements.txt 가 원천이다. 첫 줄의
# --extra-index-url 이 CPU 전용 torch 휠을 받는다 — PyPI 기본 torch 는
# CUDA 런타임까지 끌어와 수 GB 를 차지한다.
"$HOME_DIR/venv/bin/pip" install -q -r "$HOME_DIR/model/requirements.txt"

echo "== 5/5 systemd 서비스 설치·기동"
sudo sed "s/--port 8000/--port ${PORT}/" "$SCRIPT_DIR/dimension-api.service" \
  | sudo tee /etc/systemd/system/dimension-api.service >/dev/null

{
  echo "API_KEY=${API_KEY}"
  echo "N_THREADS=$(nproc)"
} | sudo tee /etc/dimension-api.env >/dev/null
sudo chmod 600 /etc/dimension-api.env

sudo systemctl daemon-reload
sudo systemctl enable --now dimension-api

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
