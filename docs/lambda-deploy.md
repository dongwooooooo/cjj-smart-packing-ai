# Lambda 컨테이너 배포

치수 추정 API를 AWS Lambda 컨테이너 이미지로 올리는 방법입니다.

관련 파일:

| 파일 | 역할 |
|---|---|
| [`deploy/lambda/Dockerfile`](../deploy/lambda/Dockerfile) | Lambda 런타임 이미지 정의 |
| [`deploy/lambda/Dockerfile.dockerignore`](../deploy/lambda/Dockerfile.dockerignore) | 이 이미지 전용 제외 목록 (`__pycache__`, `.venv` 등) |
| [`deploy/lambda/build_push.sh`](../deploy/lambda/build_push.sh) | 이미지 빌드 → ECR push |
| [`inference/`](../inference/) | API 코드 (`server.py`, `dimension.py`, `requirements.txt`) |

## 머지 순서

이 브랜치(`feature/lambda-serving`)는 `feature/inference-api` 위에 쌓여 있습니다.
`inference/` 폴더가 없으면 이미지 빌드가 되지 않으므로, **inference-api MR을 먼저
머지한 뒤** 이 MR을 머지합니다.

## 왜 컨테이너 이미지인가

Lambda는 zip 패키지 배포와 컨테이너 이미지 배포를 모두 지원합니다. 여기서는
컨테이너 이미지를 씁니다.

zip 배포는 압축 해제 기준 250MB 제한이 있습니다. torch와 torchvision만 CPU 빌드로
받아도 이 한도를 넘습니다. 컨테이너 이미지는 10GB까지 허용되므로 의존성과 모델
가중치(2~500MB)를 함께 담을 수 있습니다.

## 왜 모델을 빌드 타임에 굽는가

모델 가중치를 런타임에 허깅페이스에서 받는 방식과 비교했을 때 세 가지가 달라집니다.

1. 콜드스타트마다 다운로드하지 않습니다. Lambda 실행 환경은 요청이 없으면 회수되고,
   회수된 뒤 첫 요청에서 새 환경이 뜹니다. 런타임 다운로드 방식이면 그때마다
   수백 MB를 다시 받습니다.
2. 런타임에 허깅페이스 토큰이 필요 없습니다. 함수 환경변수에 토큰을 넣지 않아도
   되므로, 콘솔 조회 권한만 있는 사람에게 토큰이 노출되지 않습니다.
3. 허깅페이스 장애가 서비스 장애로 이어지지 않습니다. 이미 구워진 이미지로 뜨기
   때문에 외부 의존이 요청 경로에서 빠집니다.

대신 모델을 바꾸려면 이미지를 다시 빌드해야 합니다.

### 토큰을 ARG/ENV로 받지 않는 이유

`ARG HF_TOKEN` 이나 `ENV HF_TOKEN=...` 으로 토큰을 넘기면 값이 이미지 레이어와
`docker history` 출력에 그대로 남습니다. ECR 이미지를 pull 할 수 있는 사람은 누구나
토큰을 읽을 수 있습니다.

Dockerfile은 BuildKit secret mount를 씁니다.

```dockerfile
RUN --mount=type=secret,id=hf_token \
    HF_TOKEN="$(cat /run/secrets/hf_token)" \
    python -c "..."
```

이 방식은 빌드 중에만 `/run/secrets/hf_token` 으로 마운트되고 레이어에 기록되지
않습니다. 빌드할 때 토큰 파일을 지정합니다.

```bash
docker build --secret id=hf_token,src="$HOME/.hf_token" -f deploy/lambda/Dockerfile .
```

## 현재 상태 — 스켈레톤

`inference/dimension.py` 가 아직 `NotImplementedError` 를 던지는 스켈레톤입니다.
그래서 Dockerfile의 두 단계가 주석 처리돼 있습니다.

| 주석 처리된 단계 | 해제 시점 |
|---|---|
| torch·torchvision CPU 설치 | `inference/requirements.txt` 에 torch 계열을 실제로 추가할 때 |
| 모델 가중치 굽기 (`snapshot_download`) | 실제 추론 코드를 반영하고 HF 토큰이 준비됐을 때 |

지금 상태로도 이미지 빌드와 배포는 됩니다. `/health` 는 정상 응답하고 `/predict` 는
501을 반환합니다. 배포 경로를 먼저 검증하는 용도로 쓸 수 있습니다.

### torch와 torchvision을 같이 받아야 하는 이유

PyPI 기본 `torch` 는 CUDA 런타임을 함께 받아 이미지가 몇 GB 커집니다. Lambda에는
GPU가 없으므로 CPU 전용 저장소에서 받습니다.

이때 `torch` 와 `torchvision` 을 반드시 같은 명령으로, 같은 저장소에서 받아야 합니다.
한쪽만 CPU 빌드가 되면 ABI가 어긋나 기동 시
`operator torchvision::nms does not exist` 로 실패합니다.

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

## 최초 1회 절차

### 사전 준비

- AWS CLI v2 설치, `aws configure` 완료
- Docker 실행 중 (BuildKit 필요 — Docker 23 이상이면 기본 활성화)
- IAM 권한: ECR push, Lambda 함수 생성·수정

### 1. 이미지 빌드와 push

ECR 저장소는 스크립트가 없으면 만듭니다. 따로 생성하지 않아도 됩니다.

```bash
AWS_ACCOUNT_ID=123456789012 bash deploy/lambda/build_push.sh
```

기본값은 리전 `ap-northeast-2`, 저장소 `logistics-dimension-api`, 태그는 현재 커밋
short sha입니다. 바꾸려면 환경변수로 넘깁니다.

```bash
AWS_ACCOUNT_ID=123456789012 \
AWS_REGION=ap-northeast-2 \
ECR_REPO=logistics-dimension-api \
IMAGE_TAG=v1 \
bash deploy/lambda/build_push.sh
```

스크립트가 마지막에 이미지 URI를 출력합니다. 다음 단계에서 씁니다.

> Apple Silicon 맥에서 빌드해도 됩니다. 스크립트가 `--platform linux/amd64` 를
> 붙입니다. 이 옵션 없이 빌드하면 arm64 이미지가 만들어져 함수 생성이 거부됩니다.

### 2. 실행 역할 생성

Lambda 함수가 쓸 IAM 역할입니다. 이미 쓰는 역할이 있으면 건너뜁니다.

```bash
aws iam create-role \
  --role-name logistics-dimension-api-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "lambda.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

aws iam attach-role-policy \
  --role-name logistics-dimension-api-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```

### 3. 함수 생성

```bash
aws lambda create-function \
  --region ap-northeast-2 \
  --function-name logistics-dimension-api \
  --package-type Image \
  --code ImageUri=123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/logistics-dimension-api:<TAG> \
  --role arn:aws:iam::123456789012:role/logistics-dimension-api-role \
  --memory-size 10240 \
  --timeout 60 \
  --environment 'Variables={API_KEY=<발급한 키>,N_THREADS=6}'
```

| 설정 | 값 | 이유 |
|---|---|---|
| `--memory-size` | 10240 | Lambda는 메모리에 비례해 vCPU를 배정한다. 10,240MB에서 약 6 vCPU로 상한이다 |
| `--timeout` | 60 | 콜드스타트에서 모델 로드 시간이 붙는다. 기본값 3초로는 첫 요청이 타임아웃된다 |
| `API_KEY` | 발급한 키 | `/predict` 의 `X-API-Key` 헤더를 검증한다. 설정하지 않으면 인증 없이 열린다 |
| `N_THREADS` | 6 | torch 스레드 수. 배정 vCPU를 넘기면 스레드끼리 CPU를 뺏어 느려진다 |

API 키를 환경변수 평문으로 넣는 대신 Secrets Manager나 SSM Parameter Store에서
읽는 방식도 가능합니다. 운영 반영 시점에 결정합니다.

### 4. 호출 통로 연결

둘 중 하나를 고릅니다.

**Function URL** — 함수에 HTTPS 주소를 직접 붙입니다. 설정이 한 줄이고 추가 비용이
없습니다.

```bash
aws lambda create-function-url-config \
  --region ap-northeast-2 \
  --function-name logistics-dimension-api \
  --auth-type NONE

# auth-type NONE 은 URL을 아는 누구나 호출할 수 있다는 뜻이다.
# 인증은 애플리케이션의 X-API-Key 검증에만 의존하게 되므로, API_KEY를
# 반드시 설정한다.
aws lambda add-permission \
  --region ap-northeast-2 \
  --function-name logistics-dimension-api \
  --statement-id FunctionURLAllowPublicAccess \
  --action lambda:InvokeFunctionUrl \
  --principal '*' \
  --function-url-auth-type NONE
```

**API Gateway (HTTP API)** — 커스텀 도메인, 사용량 제한(usage plan), WAF 연동이
필요하면 이쪽입니다. 설정 항목이 늘고 요청당 비용이 붙습니다.

### 5. 동작 확인

```bash
FURL=$(aws lambda get-function-url-config \
  --region ap-northeast-2 \
  --function-name logistics-dimension-api \
  --query FunctionUrl --output text)

# 상태 확인 — 인증 불필요
curl "${FURL}health"
# {"status":"ok","model_loaded":false}

# 추론 요청 — 이미지 3장 업로드 (스켈레톤 단계에서는 501 반환)
curl -X POST "${FURL}predict" \
  -H "X-API-Key: <발급한 키>" \
  -F "images=@front.jpg" \
  -F "images=@side.jpg" \
  -F "images=@top.jpg"
```

첫 호출은 콜드스타트라 수 초 걸립니다. 이어지는 호출은 빨라집니다.

## 이미지 갱신 절차

코드나 모델을 바꾼 뒤에는 두 단계입니다.

```bash
# 1) 새 이미지 빌드와 push — 태그는 커밋 sha로 자동 생성
AWS_ACCOUNT_ID=123456789012 bash deploy/lambda/build_push.sh

# 2) 함수가 새 이미지를 보게 한다
aws lambda update-function-code \
  --region ap-northeast-2 \
  --function-name logistics-dimension-api \
  --image-uri 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/logistics-dimension-api:<새 TAG>
```

`build_push.sh` 가 마지막에 이 명령을 그대로 출력하므로 복사해 쓰면 됩니다.

`:latest` 같은 고정 태그로 덮어쓰지 않고 커밋 sha 태그를 쓰는 이유는 되돌리기
때문입니다. 문제가 생기면 이전 sha 태그로 `update-function-code` 를 한 번 더
실행하면 롤백됩니다.

메모리·타임아웃·환경변수를 바꿀 때는 코드 갱신과 별개 명령입니다.

```bash
aws lambda update-function-configuration \
  --region ap-northeast-2 \
  --function-name logistics-dimension-api \
  --memory-size 10240 \
  --timeout 60 \
  --environment 'Variables={API_KEY=<키>,N_THREADS=6}'
```

> `--environment` 는 기존 값에 더하는 방식이 아니라 통째로 교체합니다. 일부만
> 바꿀 때도 유지할 변수를 전부 나열해야 합니다.

## 콜드스타트 특성

새 실행 환경이 뜰 때 이미지를 내려받아 압축을 풀고, 모델을 메모리로 올린 뒤에야
첫 요청이 처리됩니다. 이미지가 클수록, 모델 로드가 무거울수록 이 시간이 깁니다.

한 번 뜬 실행 환경은 이어지는 요청에 재사용되고, 유휴 상태가 이어지면 회수됩니다.
트래픽이 띄엄띄엄한 패턴에서는 콜드스타트를 자주 만나게 됩니다.

줄이는 방법:

- 이미지 크기를 줄인다 — torch는 CPU 저장소에서 받고, `--no-cache-dir` 로 pip
  캐시를 남기지 않는다 (Dockerfile에 적용돼 있음)
- 모델 로드를 모듈 import 시점에 한 번만 하고 요청마다 다시 하지 않는다
- provisioned concurrency를 설정한다 — 지정한 수만큼 실행 환경을 미리 띄워 둬서
  콜드스타트를 없앤다. 대신 요청이 없어도 과금된다

실측 수치는 미확인 — 실제 모델을 반영한 뒤 첫 호출과 이후 호출의 응답 시간을
측정해 여기에 기록합니다.

## 제약

| 제약 | 값 | 영향 |
|---|---|---|
| vCPU 상한 | 약 6 (메모리 10,240MB 기준) | `N_THREADS` 를 6 이하로 둔다. 더 올려도 성능이 늘지 않고 스레드 경합만 생긴다 |
| 함수 실행 시간 | 최대 15분 | 요청당 처리로는 충분하다. 배치 작업을 Lambda에 넣을 때만 문제가 된다 |
| 이미지 크기 | 최대 10GB | torch CPU 빌드 + 모델 가중치는 들어간다. 여유가 없어지면 불필요한 의존성부터 정리한다 |
| Function URL 요청 크기 | 6MB | 이미지 3장 multipart 합계가 이 한도를 넘으면 요청 자체가 거부된다 |
| API Gateway 요청 크기 | 10MB | Function URL보다 여유가 있다 |
| Lambda 이벤트 페이로드 | 동기 호출 6MB | 통로와 무관하게 걸리는 한도 |

요청 크기 한도가 실제 병목이 될 수 있습니다. 원본 사진 3장이면 6MB를 넘기 쉽습니다.
대응은 두 가지입니다 — 클라이언트에서 업로드 전에 이미지를 줄이거나, 이미지를 S3에
올린 뒤 키만 전달하도록 인터페이스를 바꾸는 방식입니다. 어느 쪽으로 갈지는 미정.
