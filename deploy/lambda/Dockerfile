# syntax=docker/dockerfile:1.7
#
# Lambda 컨테이너 이미지 — 치수 추정 API.
#
# 빌드 컨텍스트는 레포 루트다. COPY 경로가 모두 `inference/...` 로 시작하는 이유.
#   docker build -f deploy/lambda/Dockerfile .
#
# BuildKit 이 필요하다 (`--mount=type=secret`). Docker 23 이상이면 기본 활성화.

FROM public.ecr.aws/lambda/python:3.12

# ---------------------------------------------------------------------------
# 1) Python 의존성
# ---------------------------------------------------------------------------
# requirements.txt 만 먼저 복사한다. 코드만 바뀐 재빌드에서 이 레이어가
# 캐시에 남아 pip install 을 건너뛴다.
COPY inference/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt

RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# torch 계열은 실제 추론 코드를 반영할 때 아래 줄의 주석을 해제한다.
#
# PyPI 기본 torch 는 CUDA 런타임을 함께 받아 이미지가 몇 GB 커진다. Lambda 는
# GPU 가 없으므로 CPU 전용 저장소에서 받는다. 이때 torch 와 torchvision 을
# 반드시 같은 명령으로, 같은 저장소에서 받아야 한다 — 한쪽만 CPU 빌드가 되면
# ABI 가 어긋나 기동 시 "operator torchvision::nms does not exist" 로 죽는다.
# RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# ---------------------------------------------------------------------------
# 2) 모델 가중치 굽기
# ---------------------------------------------------------------------------
# 실제 추론 코드를 반영할 때 아래 RUN 의 주석을 해제한다.
#
# 토큰은 ARG/ENV 로 받지 않는다. ARG/ENV 로 받으면 값이 이미지 레이어와
# `docker history` 에 그대로 남아, 이미지를 받을 수 있는 사람은 누구나 토큰을
# 읽을 수 있다. BuildKit secret mount 는 빌드 중에만 tmpfs 로 마운트되고
# 레이어에 기록되지 않는다.
#
# 빌드 시:
#   docker build --secret id=hf_token,src=$HOME/.hf_token \
#     -f deploy/lambda/Dockerfile .
#
# ENV MODEL_DIR=/opt/model
# RUN --mount=type=secret,id=hf_token \
#     HF_TOKEN="$(cat /run/secrets/hf_token)" \
#     python -c "\
# import os; \
# from huggingface_hub import snapshot_download; \
# snapshot_download( \
#     repo_id=os.environ.get('MODEL_SOURCE', 'ek09/logistics-dimension-3view'), \
#     local_dir=os.environ['MODEL_DIR'], \
#     token=os.environ['HF_TOKEN'], \
# )"

# ---------------------------------------------------------------------------
# 3) 애플리케이션 코드
# ---------------------------------------------------------------------------
# 가장 자주 바뀌는 레이어라 마지막에 둔다.
COPY inference/ ${LAMBDA_TASK_ROOT}/

# server.py 의 `handler = Mangum(app)` 를 Lambda 진입점으로 지정한다.
CMD ["server.handler"]
