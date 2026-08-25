# Lambda 컨테이너 배포

치수 추정 API를 AWS Lambda 컨테이너 이미지로 올리는 방법입니다.

관련 파일:

| 파일 | 역할 |
|---|---|
| [`deploy/lambda/Dockerfile`](../deploy/lambda/Dockerfile) | 2단계 이미지 정의 (변환 스테이지 + 런타임 스테이지) |
| [`deploy/lambda/Dockerfile.dockerignore`](../deploy/lambda/Dockerfile.dockerignore) | 이 이미지 전용 제외 목록 (`__pycache__`, `.venv` 등) |
| [`deploy/lambda/build_push.sh`](../deploy/lambda/build_push.sh) | 이미지 빌드 → ECR push |
| [`inference/`](../inference/) | API 코드 (`server.py`, `dimension.py`, `export_onnx.py`) |
| [`inference/requirements.txt`](../inference/requirements.txt) | 변환 스테이지용 — torch·timm·onnx |
| [`inference/requirements-runtime.txt`](../inference/requirements-runtime.txt) | 런타임 스테이지용 — onnxruntime, torch 없음 |

## 머지 순서

이 브랜치(`feature/lambda-serving`)는 `feature/inference-api` 위에 쌓여 있습니다.
`inference/` 폴더가 없으면 이미지 빌드가 되지 않으므로, **inference-api MR을 먼저
머지한 뒤** 이 MR을 머지합니다.

## 왜 컨테이너 이미지인가

Lambda는 zip 패키지 배포와 컨테이너 이미지 배포를 모두 지원합니다. 여기서는
컨테이너 이미지를 씁니다.

zip 배포는 압축 해제 기준 250MB 제한이 있습니다. 컨테이너 이미지는 10GB까지
허용되므로 의존성과 모델을 함께 담을 수 있습니다.

## 이미지가 2단계로 빌드되는 이유

추론은 ONNX Runtime으로 합니다. 그런데 학습·업로드는 torch 형식(`model.safetensors`)
그대로입니다. 이 둘을 잇는 변환을 이미지 빌드가 대신합니다.

| 스테이지 | 베이스 | 하는 일 | 최종 이미지에 남는가 |
|---|---|---|---|
| `builder` | `python:3.12-slim` | torch 설치 → 허깅페이스에서 `model.safetensors` 다운로드 → `export_onnx.py`로 `model.onnx` 변환 → torch 출력과 대조 | 아니오 |
| `runtime` | `public.ecr.aws/lambda/python:3.12` | onnxruntime만 설치, `model.onnx`와 `config.json`만 받아 서비스 | 예 |

여기서 세 가지가 갈립니다.

1. **모델 업로더가 하던 일은 그대로입니다.** 허깅페이스에 `model.safetensors`만
   올리면 됩니다. ONNX 변환을 사람이 돌려서 올리는 절차를 만들지 않습니다.
2. **torch가 최종 이미지에 들어가지 않습니다.** torch·timm·safetensors는 변환
   스테이지에서만 쓰이고, 런타임 스테이지는 `requirements-runtime.txt`만
   설치합니다.
3. **`model.safetensors`도 최종 이미지에 들어가지 않습니다.** 런타임 스테이지는
   `COPY --from=builder`로 `model.onnx`와 `config.json` 두 파일만 골라 가져갑니다.
   토큰으로 받은 원본 가중치가 이미지에 남지 않습니다.

### 변환이 틀리면 빌드가 멈춥니다

`export_onnx.py`는 변환 직후 같은 입력을 torch 모델과 ONNX 세션에 각각 넣고 출력을
비교합니다. `max diff`가 `1e-4`를 넘으면 비정상 종료하고, 그러면 `docker build`가
그 자리에서 실패합니다. 어긋난 그래프가 조용히 ECR로 올라가는 경로를 막는 장치입니다.

로컬 실측(macOS, threads=4)에서는 `max diff 7.45e-08`, 예측값은 소수 1자리까지
torch 경로와 같았습니다. 같은 사진 3장 기준 `predict()`는 1,070ms → 185ms,
모델 로드는 3.10s → 0.22s였습니다. **Lambda에서의 이미지 크기·콜드스타트·응답
시간은 재측정 예정입니다.**

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

### CPU 전용 torch 저장소를 쓰는 이유

`inference/requirements.txt` 첫 줄의 `--extra-index-url` 은 지우면 안 됩니다.

PyPI 기본 `torch` 는 CUDA 런타임을 함께 받아 몇 GB 커집니다. 변환 스테이지는 최종
이미지에 남지 않으므로 이미지 크기에는 영향이 없지만, 빌드 시간과 빌드 캐시 용량이
그만큼 늘어납니다. Lambda에도 CI 러너에도 GPU는 없습니다.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

B담당 배포 패키지는 `torchvision` 을 쓰지 않습니다.

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

> 빌드 스테이지가 허깅페이스에서 가중치를 받으므로 `docker build` 에
> `--secret id=hf_token,src="$HOME/.hf_token"` 이 필요합니다. `build_push.sh` 는
> 토큰을 인자로 받지 않습니다 — 인자나 환경변수로 넘기면 `ps aux` 와 셸 히스토리에
> 남기 때문입니다. 스크립트의 `docker build` 줄에 직접 붙입니다.

> 변환 스테이지에서 torch를 설치하므로 첫 빌드는 시간이 걸립니다. 두 번째부터는
> `requirements.txt` 가 바뀌지 않는 한 그 레이어가 캐시에 남습니다.

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
| `N_THREADS` | 6 | 추론 스레드 수 — ONNX Runtime의 `intra_op_num_threads` 로 들어간다. 배정 vCPU를 넘기면 스레드끼리 CPU를 뺏어 느려진다 |

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
# {"status":"ok","threads":6,"load_sec":0.2,"auth":"required"}

# 추론 요청 — 이미지 3장 업로드
curl -X POST "${FURL}predict" \
  -H "X-API-Key: <발급한 키>" \
  -F "images=@front.jpg" \
  -F "images=@side.jpg" \
  -F "images=@top.jpg"
```

첫 호출은 콜드스타트라 수 초 걸립니다. 이어지는 호출은 빨라집니다.

어느 추론 백엔드로 떴는지는 CloudWatch 로그 첫 줄에서 확인합니다. 배포 이미지는
항상 아래처럼 찍혀야 합니다.

```
[dimension] ONNX Runtime 백엔드 — /opt/model/model.onnx
```

`torch 백엔드` 로 찍혔다면 `/opt/model/model.onnx` 가 이미지에 안 들어간 것이고,
런타임 스테이지에는 torch가 없으므로 모델 로드에서 바로 실패합니다.

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

- 이미지 크기를 줄인다 — torch·timm·`model.safetensors` 를 변환 스테이지에 두고
  최종 이미지에서 뺐다. pip 캐시도 `--no-cache-dir` 로 남기지 않는다 (Dockerfile에
  적용돼 있음)
- 모델 로드를 모듈 import 시점에 한 번만 하고 요청마다 다시 하지 않는다
- provisioned concurrency를 설정한다 — 지정한 수만큼 실행 환경을 미리 띄워 둬서
  콜드스타트를 없앤다. 대신 요청이 없어도 과금된다

로컬(macOS)에서는 모델 로드가 3.10s에서 0.22s로 줄었습니다. **Lambda에서의 이미지
크기와 콜드스타트 실측은 재측정 예정** — 첫 호출과 이후 호출의 응답 시간, `docker
images` 기준 이미지 크기를 여기에 기록합니다.

## 제약

| 제약 | 값 | 영향 |
|---|---|---|
| vCPU 상한 | 약 6 (메모리 10,240MB 기준) | `N_THREADS` 를 6 이하로 둔다. 더 올려도 성능이 늘지 않고 스레드 경합만 생긴다 |
| 함수 실행 시간 | 최대 15분 | 요청당 처리로는 충분하다. 배치 작업을 Lambda에 넣을 때만 문제가 된다 |
| 이미지 크기 | 최대 10GB | onnxruntime + `model.onnx`(53MB) 라 여유가 크다. 실측은 재측정 예정 |
| Function URL 요청 크기 | 6MB | 이미지 3장 multipart 합계가 이 한도를 넘으면 요청 자체가 거부된다 |
| API Gateway 요청 크기 | 10MB | Function URL보다 여유가 있다 |
| Lambda 이벤트 페이로드 | 동기 호출 6MB | 통로와 무관하게 걸리는 한도 |

요청 크기 한도가 실제 병목이 될 수 있습니다. 원본 사진 3장이면 6MB를 넘기 쉽습니다.
대응은 두 가지입니다 — 클라이언트에서 업로드 전에 이미지를 줄이거나, 이미지를 S3에
올린 뒤 키만 전달하도록 인터페이스를 바꾸는 방식입니다. 어느 쪽으로 갈지는 미정.
