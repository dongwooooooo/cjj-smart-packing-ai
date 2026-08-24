#!/bin/bash
# Lambda 컨테이너 이미지를 빌드해 ECR 로 push 한다.
#
# 환경변수 (인자가 아니라 환경변수로 받는다):
#   AWS_ACCOUNT_ID  AWS 계정 번호 12자리 (필수)
#   AWS_REGION      리전 (기본값: ap-northeast-2)
#   ECR_REPO        ECR 저장소 이름 (기본값: logistics-dimension-api)
#   IMAGE_TAG       이미지 태그 (기본값: 현재 커밋 short sha)
#
# 사용법:
#   AWS_ACCOUNT_ID=123456789012 bash deploy/lambda/build_push.sh
#
# 허깅페이스 토큰은 이 스크립트가 받지 않는다. 모델 굽기 단계를 켠 뒤에는
# docker build 에 `--secret id=hf_token,src=...` 를 직접 붙인다 (아래 참고).
# 토큰을 CLI 인자나 환경변수로 스크립트에 흘리면 `ps aux` 와 셸 히스토리에
# 남는다.
set -euo pipefail

: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID 환경변수가 필요합니다. 예: AWS_ACCOUNT_ID=123456789012 bash deploy/lambda/build_push.sh}"
AWS_REGION="${AWS_REGION:-ap-northeast-2}"
ECR_REPO="${ECR_REPO:-logistics-dimension-api}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# 태그 기본값은 커밋 short sha. git 저장소가 아니면 latest 로 떨어진다.
if [ -z "${IMAGE_TAG:-}" ]; then
  IMAGE_TAG="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo latest)"
fi

REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

echo "== 1/4 ECR 로그인 (${AWS_REGION})"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

echo "== 2/4 ECR 저장소 확인"
if aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1; then
  echo "  ${ECR_REPO} 이미 있음 — 건너뜀"
else
  echo "  ${ECR_REPO} 없음 — 생성"
  aws ecr create-repository \
    --region "$AWS_REGION" \
    --repository-name "$ECR_REPO" \
    --image-scanning-configuration scanOnPush=true >/dev/null
fi

echo "== 3/4 이미지 빌드 (${IMAGE_URI})"
# 빌드 컨텍스트는 레포 루트다. Dockerfile 의 COPY 가 inference/ 를 참조한다.
#
# Lambda 는 linux/amd64 만 받는다. Apple Silicon 에서 --platform 없이 빌드하면
# arm64 이미지가 만들어져 함수 생성 시 거부된다.
#
# 모델 굽기 단계를 켠 뒤에는 아래 명령에 secret 마운트를 추가한다:
#   --secret id=hf_token,src="$HOME/.hf_token"
DOCKER_BUILDKIT=1 docker build \
  --platform linux/amd64 \
  --provenance=false \
  -f "${SCRIPT_DIR}/Dockerfile" \
  -t "$IMAGE_URI" \
  "$REPO_ROOT"

echo "== 4/4 push"
docker push "$IMAGE_URI"

echo
echo "완료: ${IMAGE_URI}"
echo
echo "Lambda 함수 이미지 갱신:"
echo "  aws lambda update-function-code \\"
echo "    --region ${AWS_REGION} \\"
echo "    --function-name ${ECR_REPO} \\"
echo "    --image-uri ${IMAGE_URI}"
