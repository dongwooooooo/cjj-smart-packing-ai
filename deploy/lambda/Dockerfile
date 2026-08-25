# syntax=docker/dockerfile:1.7
#
# Lambda 컨테이너 이미지 — 치수 추정 API.
#
# 2단계 빌드다.
#   builder — torch 를 설치하고, 허깅페이스에서 model.safetensors 를 받아
#             model.onnx 로 변환한다. 이 스테이지는 최종 이미지에 남지 않는다.
#   runtime — onnxruntime 만 설치하고 builder 가 만든 model.onnx 를 받아 쓴다.
#
# 모델 업로더는 허깅페이스에 지금처럼 model.safetensors 만 올리면 된다. ONNX 변환은
# 빌드가 알아서 한다. 변환 뒤 export_onnx.py 가 torch 출력과 대조하고, 어긋나면
# 빌드를 실패시킨다.
#
# 빌드 컨텍스트는 레포 루트다. COPY 경로가 모두 `inference/...` 로 시작하는 이유.
#   docker build -f deploy/lambda/Dockerfile .
#
# BuildKit 이 필요하다 (`--mount=type=secret`). Docker 23 이상이면 기본 활성화.

# ===========================================================================
# stage 1 — builder: 모델 내려받기 + ONNX 변환
# ===========================================================================
FROM python:3.12-slim AS builder

ENV MODEL_DIR=/opt/model
ENV MODEL_SOURCE=althdgk/cj_A.LTS_AI_B_2026

WORKDIR /build

# ---------------------------------------------------------------------------
# 1) 변환용 의존성 (torch·timm·onnx·onnxruntime)
# ---------------------------------------------------------------------------
# requirements.txt 첫 줄의 --extra-index-url 이 CPU 전용 torch 휠을 받는다.
# PyPI 기본 torch 는 CUDA 런타임을 함께 받아 몇 GB 커지므로 이 줄을 지우면 안 된다.
# (여기서 커지는 건 builder 스테이지뿐이라 최종 이미지 크기와는 무관하지만,
#  빌드 시간과 캐시 용량에는 그대로 영향을 준다.)
COPY inference/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# 2) 모델 가중치 내려받기
# ---------------------------------------------------------------------------
# 토큰은 ARG/ENV 로 받지 않는다. ARG/ENV 로 받으면 값이 이미지 레이어와
# `docker history` 에 그대로 남아, 이미지를 받을 수 있는 사람은 누구나 토큰을
# 읽을 수 있다. BuildKit secret mount 는 빌드 중에만 tmpfs 로 마운트되고
# 레이어에 기록되지 않는다.
#
# 여기에 더해 이 스테이지 자체가 최종 이미지에 포함되지 않는다. 아래 runtime
# 스테이지는 model.onnx 와 config.json 두 파일만 골라 가져간다.
#
# 빌드 시:
#   docker build --secret id=hf_token,src=$HOME/.hf_token \
#     -f deploy/lambda/Dockerfile .
#
RUN --mount=type=secret,id=hf_token \
    HF_TOKEN="$(cat /run/secrets/hf_token)" \
    python -c "\
import os; \
from huggingface_hub import snapshot_download; \
snapshot_download( \
    repo_id=os.environ['MODEL_SOURCE'], \
    local_dir=os.environ['MODEL_DIR'], \
    token=os.environ['HF_TOKEN'], \
    allow_patterns=['model.safetensors', 'config.json'], \
)"

# ---------------------------------------------------------------------------
# 3) ONNX 변환 + 출력 대조
# ---------------------------------------------------------------------------
# export_onnx.py 가 dimension.py 를 임포트하므로 코드를 함께 복사한다.
# 변환 직후 torch 출력과 비교해 max diff 가 1e-4 를 넘으면 여기서 빌드가 멈춘다.
COPY inference/ ./
RUN python export_onnx.py "$MODEL_DIR" "$MODEL_DIR/model.onnx"

# ===========================================================================
# stage 2 — runtime: onnxruntime 만 싣는 Lambda 이미지
# ===========================================================================
FROM public.ecr.aws/lambda/python:3.12

# ---------------------------------------------------------------------------
# 4) 런타임 의존성
# ---------------------------------------------------------------------------
# requirements-runtime.txt 에는 torch 가 없다. dimension.py 가 model.onnx 를
# 보면 OnnxDimensionModel 을 고르고 torch 를 임포트하지 않는다.
COPY inference/requirements-runtime.txt ${LAMBDA_TASK_ROOT}/requirements-runtime.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements-runtime.txt

# ---------------------------------------------------------------------------
# 5) 변환된 모델
# ---------------------------------------------------------------------------
# 두 파일만 가져온다. model.safetensors 는 최종 이미지에 넣지 않는다 — 런타임에
# 쓰이지 않고, 토큰으로 받은 원본이 이미지에 남을 이유도 없다.
ENV MODEL_DIR=/opt/model
COPY --from=builder /opt/model/model.onnx /opt/model/config.json /opt/model/

# ---------------------------------------------------------------------------
# 6) 애플리케이션 코드
# ---------------------------------------------------------------------------
# 가장 자주 바뀌는 레이어라 마지막에 둔다.
COPY inference/ ${LAMBDA_TASK_ROOT}/

# server.py 의 `handler = Mangum(app)` 를 Lambda 진입점으로 지정한다.
CMD ["server.handler"]
