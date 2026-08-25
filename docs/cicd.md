# GitHub Actions 파이프라인 사용법

이 저장소의 자동화는 워크플로 파일 두 개다. PR을 검사하는 `ci.yml`, main 머지 이후 이미지를 만들어 Lambda에 반영하는 `build-deploy.yml`.

## 머지 순서

이 브랜치(`feature/cicd`)는 `feature/lambda-serving` 위에 있다. 순서는 `feature/inference-api` → `feature/lambda-serving` → `feature/cicd`. 앞 브랜치가 머지되기 전에 이 브랜치를 main으로 보내면 워크플로가 참조하는 `deploy/lambda/Dockerfile`과 `inference/`가 main에 없어 빌드가 실패한다.

## 파이프라인 개요

| 워크플로 | 언제 도는가 | 무엇을 하는가 | AWS 접근 |
|---|---|---|---|
| `ci.yml` | main 대상 PR이 열리거나 갱신될 때 | Python 문법 검사, 셸 스크립트 문법 검사, 워크플로 YAML 문법 검사 | 없음 |
| `build-deploy.yml` | main에 push되고 `inference/**` 또는 `deploy/lambda/**`가 바뀌었을 때. Actions 탭에서 수동 실행도 가능 | 컨테이너 이미지를 빌드해 ECR에 push하고, Lambda 함수가 새 이미지를 보게 갱신 | OIDC로 역할 위임 |

PR 단계에서 이미지를 빌드하지 않는다. torch와 모델 가중치를 켜면 이미지가 수 GB가 되고 AWS 자격증명이 필요해서, PR마다 돌리기에는 비용과 권한 부담이 크다. 이미지 빌드는 머지 이후에만 한다.

### build-deploy.yml 잡 2개

**build-push** — 저장소를 체크아웃하고, OIDC로 AWS 역할을 위임받고, ECR에 로그인한 뒤 `deploy/lambda/Dockerfile`로 이미지를 빌드해 push한다.

- 빌드 컨텍스트는 저장소 루트다. Dockerfile의 `COPY`가 `inference/...`를 참조하기 때문이다.
- 태그는 두 개가 붙는다. 커밋 short sha(예: `c8e91fd`)와 `latest`.
- `platforms: linux/amd64`와 `provenance: false`를 지정한다. Lambda는 arm64 이미지를 거부하고, Buildx가 기본으로 붙이는 OCI 증명 매니페스트도 거부한다.
- 허깅페이스 토큰은 `secret-envs`로 BuildKit secret에 전달한다. Dockerfile이 `--mount=type=secret,id=hf_token`으로 받으므로 토큰이 이미지 레이어나 `docker history`에 남지 않는다. 토큰 값 대신 환경변수 이름을 넘기는 방식이라, `HF_TOKEN`을 아직 등록하지 않은 상태에서도 빌드 단계가 그대로 돈다. 모델 다운로드 줄이 주석인 스켈레톤 단계에서는 토큰이 필요 없기 때문이다.

**deploy-lambda** — build-push가 만든 이미지 URI를 받아 `aws lambda update-function-code`를 호출한다. 갱신 뒤 `aws lambda wait function-updated-v2`로 반영이 끝날 때까지 기다린다. 이 대기가 없으면 갱신 직후 호출이 아직 이전 코드에 닿는다.

함수가 아직 없으면 실패하지 않고 안내만 남기고 넘어간다. 함수 최초 생성은 [lambda-deploy.md](lambda-deploy.md)의 "최초 1회 절차"에서 한 번만 수행한다.

### 기본값

| 항목 | 값 | 바꾸는 곳 |
|---|---|---|
| 리전 | `ap-northeast-2` | `build-deploy.yml`의 `env.AWS_REGION` |
| ECR 저장소 | `logistics-dimension-api` | `build-deploy.yml`의 `env.ECR_REPO` |
| Lambda 함수 이름 | `logistics-dimension-api` | 저장소 변수 `LAMBDA_FUNCTION_NAME` (없으면 기본값) |

ECR 저장소는 파이프라인이 만들지 않는다. 없으면 빌드 전에 안내 메시지와 함께 실패한다. CI 역할에 저장소 생성 권한을 주지 않기 위해서다.

## 사전 세팅 절차

한 번만 하면 된다. AWS 쪽 두 단계, GitHub 쪽 한 단계.

### 1. AWS — OIDC 신뢰 관계 등록

GitHub Actions가 액세스 키 없이 AWS 역할을 빌려 쓸 수 있게, GitHub를 신뢰할 수 있는 발급자로 계정에 등록한다. 계정당 한 번만 하면 되고, 이미 등록돼 있으면 건너뛴다.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

이미 있는지 확인:

```bash
aws iam list-open-id-connect-providers
```

### 2. AWS — IAM 역할 생성

GitHub Actions가 위임받을 역할이다. 신뢰 정책에 저장소 조건을 넣어, 이 저장소의 워크플로만 이 역할을 쓸 수 있게 제한한다.

`trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<AWS_ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:cj-ai-sw/ai:*",
            "repo:cj-ai-sw@316033991/ai@1340552961:*"
          ]
        }
      }
    }
  ]
}
```

이 조직의 토큰은 `sub` 클레임에 소유자·저장소 ID가 붙은 형식(`repo:cj-ai-sw@316033991/ai@1340552961:ref:...`)으로 발급된다(2026-08-25 실측). 이름만 쓴 패턴은 매칭에 실패해 `Not authorized to perform sts:AssumeRoleWithWebIdentity`가 나므로 두 형식을 모두 넣는다. ID는 토큰의 `repository_owner_id`·`repository_id` 클레임 값이다.

역할을 쓸 수 있는 범위는 `sub` 조건이 정한다. `:*`는 이 저장소의 모든 브랜치·태그·환경을 허용한다. main 머지에서만 쓰게 좁히려면 `repo:cj-ai-sw/ai:ref:refs/heads/main`으로 바꾼다. 다만 그렇게 하면 `workflow_dispatch`를 다른 브랜치에서 돌릴 때 위임이 거부된다.

`permissions-policy.json` — ECR push와 Lambda 갱신에 필요한 권한만 담는다:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "EcrPush",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:CompleteLayerUpload",
        "ecr:DescribeRepositories",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:ap-northeast-2:<AWS_ACCOUNT_ID>:repository/logistics-dimension-api"
    },
    {
      "Sid": "LambdaUpdate",
      "Effect": "Allow",
      "Action": [
        "lambda:GetFunction",
        "lambda:UpdateFunctionCode"
      ],
      "Resource": "arn:aws:lambda:ap-northeast-2:<AWS_ACCOUNT_ID>:function:logistics-dimension-api"
    }
  ]
}
```

역할 생성과 정책 연결:

```bash
aws iam create-role \
  --role-name github-actions-logistics-dimension \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name github-actions-logistics-dimension \
  --policy-name ecr-push-lambda-update \
  --policy-document file://permissions-policy.json

# 다음 단계에서 등록할 ARN 확인
aws iam get-role \
  --role-name github-actions-logistics-dimension \
  --query 'Role.Arn' --output text
```

### 3. GitHub — 시크릿·변수 등록

저장소 Settings → Secrets and variables → Actions.

**Secrets** 탭:

| 이름 | 값 | 비고 |
|---|---|---|
| `AWS_ROLE_ARN` | 위에서 확인한 역할 ARN | 예: `arn:aws:iam::123456789012:role/github-actions-logistics-dimension` |
| `HF_TOKEN` | 허깅페이스 액세스 토큰 | fine-grained, 해당 모델 저장소 읽기 전용으로 발급. Dockerfile의 모델 다운로드 단계를 켜는 시점부터 필요 |

`HF_TOKEN`은 fine-grained 토큰으로 발급하고 권한은 대상 저장소 읽기만 준다. 쓰기 권한이 붙은 토큰이나 계정 전체 토큰은 등록하지 않는다. 이 토큰은 CI 빌드 중에만 쓰이고 실행 중인 Lambda에는 존재하지 않는다 — 모델을 빌드 타임에 이미지에 굽기 때문이다.

**Variables** 탭 (선택):

| 이름 | 값 | 비고 |
|---|---|---|
| `LAMBDA_FUNCTION_NAME` | 함수 이름 | 등록하지 않으면 `logistics-dimension-api` |

액세스 키(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) 방식도 동작하지만, 만료되지 않는 자격증명이 저장소에 남으므로 쓰지 않는다.

## 수동 실행 방법

Actions 탭 → 왼쪽 목록에서 `build-deploy` → 오른쪽 **Run workflow**.

- 브랜치를 고르고 실행한다. `image_tag` 입력을 비워 두면 해당 커밋의 short sha가 태그가 된다.
- 특정 태그로 굽고 싶으면 `image_tag`에 값을 넣는다. 영문·숫자·`.`·`_`·`-`만 허용하고, 그 외 문자는 실행 초반에 거부된다.

main이 아닌 브랜치에서 수동 실행하려면 IAM 신뢰 정책의 `sub` 조건이 `repo:cj-ai-sw/ai:*`여야 한다. `refs/heads/main`으로 좁혀 뒀다면 위임 단계에서 막힌다.

## 실패 시 확인 지점

| 증상 | 원인 | 조치 |
|---|---|---|
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | 신뢰 정책의 `sub` 조건이 실행 브랜치와 안 맞거나, 워크플로에 `id-token: write` 권한이 없음 | 신뢰 정책의 저장소·브랜치 조건 확인 |
| `Could not load credentials from any providers` | `AWS_ROLE_ARN` 시크릿 미등록 또는 오타 | Settings → Secrets에서 값 확인 |
| `ECR 저장소 '...' 가 리전 ...에 없습니다` | 저장소 미생성 | [lambda-deploy.md](lambda-deploy.md)의 최초 1회 절차 수행 |
| push 단계에서 `denied` | IAM 정책의 ECR 리소스 ARN이 실제 저장소와 불일치 | 권한 정책의 리전·계정·저장소 이름 확인 |
| `Lambda 함수 없음` 안내 후 종료 | 함수 미생성 (실패 아님) | 최초 생성 절차 수행. 이후 실행부터 자동 갱신 |
| `InvalidParameterValueException: The image manifest ... is not supported` | 이미지가 arm64이거나 증명 매니페스트가 붙음 | `platforms: linux/amd64`, `provenance: false` 유지 확인 |
| 함수 갱신은 됐는데 응답이 예전 그대로 | 반영 대기 없이 호출 | `wait function-updated-v2` 단계 로그 확인 |

빌드 로그는 Actions 탭에서 해당 실행 → 잡 → 단계 순으로 펼쳐 본다. push된 이미지 URI는 실행 요약(Summary)에 남는다.

## 모델 저장 경로가 바뀔 때

모델 가중치를 허깅페이스가 아닌 다른 곳(S3 등)에서 받게 되면 고칠 곳은 두 군데다.

1. `deploy/lambda/Dockerfile`의 모델 다운로드 단계 — 현재 `snapshot_download`로 받는 부분을 새 경로에서 받는 명령으로 교체한다.
2. 인증 정보 — `HF_TOKEN` 시크릿을 새 저장소에 맞는 것으로 바꾸거나, S3로 옮긴다면 시크릿 대신 IAM 역할에 해당 버킷 읽기 권한을 추가하고 secret 전달 줄을 제거한다.

워크플로의 나머지 부분(빌드·push·Lambda 갱신)은 그대로 둔다. 모델을 빌드 타임에 이미지에 굽는 구조 자체가 바뀌지 않기 때문이다.

## 아직 안 한 것

- **빌드 캐시** — 현재 캐시 설정이 없다. torch 설치와 모델 다운로드를 켜면 매 빌드가 처음부터 돌아 시간이 길어진다. GitHub Actions 캐시는 저장소당 10GB 제한이 있어 이 이미지 크기에는 맞지 않고, ECR을 캐시 저장소로 쓰는 방식(`type=registry`)이 대안이다. 실제 빌드 시간을 측정한 뒤 판단한다.
- **배포 후 동작 확인** — 갱신 뒤 `/health` 호출로 확인하는 단계가 없다. 함수 URL이 생긴 뒤에 추가한다.
- **EC2 트랙 연동** — 이 파이프라인은 Lambda만 다룬다. EC2 배포는 [ec2-deploy.md](ec2-deploy.md) 참고.
