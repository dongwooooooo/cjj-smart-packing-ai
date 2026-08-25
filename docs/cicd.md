# GitHub Actions 파이프라인 사용법

이 저장소의 자동화는 워크플로 파일 세 개다. PR을 검사하는 `ci.yml`, main 머지 이후 이미지를 만들어 Lambda에 반영하는 `build-deploy.yml`, 같은 코드를 EC2 인스턴스에 배포하는 `deploy-ec2.yml`.

## 머지 순서

이 브랜치(`feature/cicd`)는 `feature/lambda-serving` 위에 있다. 순서는 `feature/inference-api` → `feature/lambda-serving` → `feature/cicd`. 앞 브랜치가 머지되기 전에 이 브랜치를 main으로 보내면 워크플로가 참조하는 `deploy/lambda/Dockerfile`과 `inference/`가 main에 없어 빌드가 실패한다.

## 파이프라인 개요

| 워크플로 | 언제 도는가 | 무엇을 하는가 | AWS 접근 |
|---|---|---|---|
| `ci.yml` | main 대상 PR이 열리거나 갱신될 때 | Python 문법 검사, 셸 스크립트 문법 검사, 워크플로 YAML 문법 검사 | 없음 |
| `build-deploy.yml` | main에 push되고 `inference/**` 또는 `deploy/lambda/**`가 바뀌었을 때. Actions 탭에서 수동 실행도 가능 | 컨테이너 이미지를 빌드해 ECR에 push하고, Lambda 함수가 새 이미지를 보게 갱신 | OIDC로 역할 위임 |
| `deploy-ec2.yml` | main에 push되고 `inference/**` 또는 `deploy/ec2/**`가 바뀌었을 때. Actions 탭에서 수동 실행도 가능 | 추론 코드와 셋업 스크립트를 EC2 인스턴스로 보내고 `setup.sh`를 실행 | OIDC로 역할 위임 + SSM Run Command |

Lambda와 EC2를 워크플로 파일 하나로 합치지 않았다. GitHub은 `paths` 필터를 워크플로 단위로만 적용하고 잡 단위로는 적용하지 않는다. 한 파일에 두 잡을 넣으면 `deploy/lambda/`만 고친 커밋도 EC2에서 pip 설치와 ONNX 변환을 다시 돌리고, `deploy/ec2/`만 고친 커밋도 수 GB 이미지를 빌드한다. 동시 실행 제어(`concurrency`)와 수동 실행 입력값도 두 트랙이 서로 다르다.

PR 단계에서 이미지를 빌드하지 않는다. torch와 모델 가중치를 켜면 이미지가 수 GB가 되고 AWS 자격증명이 필요해서, PR마다 돌리기에는 비용과 권한 부담이 크다. 이미지 빌드는 머지 이후에만 한다.

### build-deploy.yml 잡 2개

**build-push** — 저장소를 체크아웃하고, OIDC로 AWS 역할을 위임받고, ECR에 로그인한 뒤 `deploy/lambda/Dockerfile`로 이미지를 빌드해 push한다.

- 빌드 컨텍스트는 저장소 루트다. Dockerfile의 `COPY`가 `inference/...`를 참조하기 때문이다.
- 태그는 두 개가 붙는다. 커밋 short sha(예: `c8e91fd`)와 `latest`.
- `platforms: linux/amd64`와 `provenance: false`를 지정한다. Lambda는 arm64 이미지를 거부하고, Buildx가 기본으로 붙이는 OCI 증명 매니페스트도 거부한다.
- 허깅페이스 토큰은 `secret-envs`로 BuildKit secret에 전달한다. Dockerfile이 `--mount=type=secret,id=hf_token`으로 받으므로 토큰이 이미지 레이어나 `docker history`에 남지 않는다. 토큰 값 대신 환경변수 이름을 넘기는 방식이라, `HF_TOKEN`을 아직 등록하지 않은 상태에서도 빌드 단계가 그대로 돈다. 모델 다운로드 줄이 주석인 스켈레톤 단계에서는 토큰이 필요 없기 때문이다.

**deploy-lambda** — build-push가 만든 이미지 URI를 받아 `aws lambda update-function-code`를 호출한다. 갱신 뒤 `aws lambda wait function-updated-v2`로 반영이 끝날 때까지 기다린다. 이 대기가 없으면 갱신 직후 호출이 아직 이전 코드에 닿는다.

함수가 아직 없으면 실패하지 않고 안내만 남기고 넘어간다. 함수 최초 생성은 [lambda-deploy.md](lambda-deploy.md)의 "최초 1회 절차"에서 한 번만 수행한다.

### deploy-ec2.yml 잡 1개

**deploy-ec2** — SSH 키도 로컬 `.env`도 쓰지 않는다. 러너가 OIDC로 AWS 역할을 위임받아 SSM Run Command를 호출하고, 인스턴스에 이미 떠 있는 SSM 에이전트가 명령을 실행한다. 배포 담당자의 PC에 키페어나 토큰이 있을 필요가 없다.

단계 순서는 다음과 같다.

1. **인스턴스 확인** — `ssm describe-instance-information`으로 대상이 SSM 관리 목록에 `Online` 상태로 있는지 본다. 인스턴스가 꺼져 있거나, 인스턴스 프로파일이 없거나, 에이전트가 등록되지 않은 세 경우가 모두 여기서 같은 모습으로 잡힌다. 확인 없이 명령을 보내면 `InvalidInstanceId`만 돌아와 원인을 알 수 없다.
2. **파일 묶기** — `deploy/ec2/setup.sh`, `deploy/ec2/dimension-api.service`, `inference/*.py`, `inference/requirements*.txt` 8개를 tar.gz로 묶고 base64로 인코딩한다. 2026-08-25 기준 base64 결과가 15,732바이트다. 45,000바이트를 넘으면 전송 전에 실패시킨다.
3. **명령 전송** — base64 문자열을 `AWS-RunShellScript` 명령 본문에 그대로 실어 보낸다. 인스턴스는 S3에서도 git에서도 아무것도 내려받지 않는다. 인스턴스가 하는 일은 base64 해제, tar 해제, 파일 배치, `setup.sh` 실행뿐이다.
4. **대기와 로그** — `ssm wait command-executed`로 종료를 기다리고 `ssm get-command-invocation`으로 표준 출력·표준 에러를 잡 로그에 찍는다. 상태가 `Success`가 아니면 잡을 실패시킨다.

인스턴스에서의 파일 배치는 `setup.sh`가 기대하는 위치를 그대로 따른다. `~/setup.sh`, `~/dimension-api.service`, `~/inference/`. `~/inference/`는 매번 지우고 다시 만든다 — 레포에서 삭제된 파일이 인스턴스에만 남는 걸 막기 위해서다.

실행 사용자를 신경 써야 한다. SSM 에이전트는 명령을 root로 실행하는데, root의 `$HOME`은 `/root`이고 `setup.sh`는 `$HOME/app`·`$HOME/venv`에 설치한다. 반면 `dimension-api.service`는 `/home/ubuntu/app`과 `User=ubuntu`로 고정돼 있다. 그대로 root로 돌리면 코드는 `/root/app`에 깔리고 서비스는 `/home/ubuntu/app`을 찾다가 기동에 실패한다. 그래서 마지막 실행만 `sudo -H -u ubuntu`로 내려간다.

명령 본문은 POSIX sh로 쓴다. SSM 에이전트가 스크립트를 `/bin/sh`로 넘기고 Ubuntu의 `/bin/sh`는 dash라, `set -o pipefail`이나 `[[ ]]` 같은 bash 문법은 이 층위에서 쓸 수 없다. bash가 필요한 부분은 `bash -c`로 따로 감싼다.

`ssm wait command-executed`는 5초 간격 20회, 즉 100초까지만 기다리고 포기한다. `setup.sh`는 torch 설치와 ONNX 변환에 몇 분이 걸리므로 이 대기를 30분 상한의 루프 안에서 반복 호출한다.

### 기본값

| 항목 | 값 | 바꾸는 곳 |
|---|---|---|
| 리전 | `ap-northeast-2` | 각 워크플로의 `env.AWS_REGION` |
| ECR 저장소 | `logistics-dimension-api` | `build-deploy.yml`의 `env.ECR_REPO` |
| Lambda 함수 이름 | `logistics-dimension-api` | 저장소 변수 `LAMBDA_FUNCTION_NAME` (없으면 기본값) |
| EC2 인스턴스 ID | `i-0c7dac45358b3fafc` | 저장소 변수 `EC2_INSTANCE_ID`, 또는 수동 실행 시 `instance_id` 입력값 (둘 다 없으면 기본값) |
| SSM 실행 제한 시간 | 1800초 | `deploy-ec2.yml`의 `env.SSM_EXECUTION_TIMEOUT` |
| 전송 묶음 크기 상한 | 45,000바이트 | `deploy-ec2.yml`의 `env.MAX_PAYLOAD_BYTES` |

ECR 저장소는 파이프라인이 만들지 않는다. 없으면 빌드 전에 안내 메시지와 함께 실패한다. CI 역할에 저장소 생성 권한을 주지 않기 위해서다.

## 사전 세팅 절차

한 번만 하면 된다. AWS 쪽 세 단계, GitHub 쪽 한 단계. EC2 배포를 쓰지 않는다면 3번은 건너뛰어도 된다.

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

`permissions-policy.json` — ECR push, Lambda 갱신, EC2 배포용 SSM 호출에 필요한 권한만 담는다:

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
    },
    {
      "Sid": "SsmSendCommand",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:<AWS_ACCOUNT_ID>:instance/i-0c7dac45358b3fafc",
        "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "SsmReadResult",
      "Effect": "Allow",
      "Action": [
        "ssm:DescribeInstanceInformation",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands"
      ],
      "Resource": "*"
    }
  ]
}
```

`ssm:SendCommand`는 리소스 두 개를 함께 요구한다. 명령을 받을 인스턴스와, 실행할 문서(`AWS-RunShellScript`)다. 인스턴스 ARN은 `ssm`이 아니라 `ec2` 서비스 네임스페이스이고, AWS 관리 문서의 ARN에는 계정 ID 자리가 비어 있다(`:ap-northeast-2::document/`). 둘 중 하나라도 빠지면 `AccessDeniedException`이 난다.

`SsmReadResult`의 네 액션은 리소스 단위 제한을 지원하지 않아 `*`로 둔다. 조회 전용이고 실행 권한은 없다. 인스턴스를 늘리면 `SsmSendCommand`의 인스턴스 ARN만 추가한다.

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

### 3. AWS — EC2 인스턴스 프로파일 (EC2 배포용)

SSM 에이전트는 Ubuntu AMI에 snap 패키지로 이미 들어 있다. 설치할 것은 없고, 인스턴스가 SSM 서비스에 자신을 등록할 수 있게 IAM 역할을 붙이기만 하면 된다. 역할이 없으면 에이전트는 떠 있어도 관리 목록에 나타나지 않는다.

```bash
cat > ec2-trust.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

aws iam create-role \
  --role-name logistics-dimension-ec2 \
  --assume-role-policy-document file://ec2-trust.json

aws iam attach-role-policy \
  --role-name logistics-dimension-ec2 \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

aws iam create-instance-profile --instance-profile-name logistics-dimension-ec2
aws iam add-role-to-instance-profile \
  --instance-profile-name logistics-dimension-ec2 \
  --role-name logistics-dimension-ec2

aws ec2 associate-iam-instance-profile \
  --region ap-northeast-2 \
  --instance-id i-0c7dac45358b3fafc \
  --iam-instance-profile Name=logistics-dimension-ec2
```

`AmazonSSMManagedInstanceCore`는 AWS 관리형 정책이고, 에이전트가 SSM·EC2 Messages 엔드포인트와 통신하는 데 필요한 권한만 담고 있다. 배포에 필요한 인스턴스 권한은 이게 전부다 — 모델 파일은 허깅페이스에서 받고 코드는 명령 본문에 실려 오므로 S3나 ECR 읽기 권한은 필요 없다.

역할을 붙인 뒤 등록까지 보통 1~2분 걸린다. 확인:

```bash
aws ssm describe-instance-information \
  --region ap-northeast-2 \
  --filters Key=InstanceIds,Values=i-0c7dac45358b3fafc \
  --query 'InstanceInformationList[0].[InstanceId,PingStatus,AgentVersion]' \
  --output text
```

`PingStatus`가 `Online`이면 준비된 상태다. 계속 비어 있으면 인스턴스를 재부팅하거나 `sudo snap restart amazon-ssm-agent`로 에이전트를 다시 띄운다. 아웃바운드 443이 막혀 있어도 등록되지 않는다 — 에이전트가 SSM 엔드포인트로 먼저 연결을 여는 구조라 인바운드 규칙은 열 필요가 없다.

### 4. GitHub — 시크릿·변수 등록

저장소 Settings → Secrets and variables → Actions.

**Secrets** 탭:

| 이름 | 값 | 비고 |
|---|---|---|
| `AWS_ROLE_ARN` | 위에서 확인한 역할 ARN | 예: `arn:aws:iam::123456789012:role/github-actions-logistics-dimension` |
| `HF_TOKEN` | 허깅페이스 액세스 토큰 | fine-grained, 해당 모델 저장소 읽기 전용으로 발급. Dockerfile의 모델 다운로드 단계를 켜는 시점부터 필요. EC2 배포에서는 처음부터 필요하며, 없으면 `deploy-ec2`가 즉시 실패한다 |
| `EC2_API_KEY` | EC2 API 호출자 인증 키 | `X-API-Key` 헤더 검증용. 등록하지 않으면 인증 없이 열린 상태로 배포되고 잡 로그에 경고가 남는다 |

`HF_TOKEN`은 fine-grained 토큰으로 발급하고 권한은 대상 저장소 읽기만 준다. 쓰기 권한이 붙은 토큰이나 계정 전체 토큰은 등록하지 않는다. 이 토큰은 CI 빌드 중에만 쓰이고 실행 중인 Lambda에는 존재하지 않는다 — 모델을 빌드 타임에 이미지에 굽기 때문이다.

**Variables** 탭 (선택):

| 이름 | 값 | 비고 |
|---|---|---|
| `LAMBDA_FUNCTION_NAME` | 함수 이름 | 등록하지 않으면 `logistics-dimension-api` |
| `EC2_INSTANCE_ID` | 배포 대상 인스턴스 ID | 등록하지 않으면 `i-0c7dac45358b3fafc`. 인스턴스를 교체하면 워크플로 파일 대신 이 변수를 고친다 |

액세스 키(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) 방식도 동작하지만, 만료되지 않는 자격증명이 저장소에 남으므로 쓰지 않는다.

## 토큰 노출 범위 (EC2 배포)

`deploy-ec2`는 `HF_TOKEN`과 `EC2_API_KEY`를 SSM 명령 본문에 평문으로 실어 보낸다. AWS는 Run Command 이력을 30일간 보관하고, 이력을 지우는 API는 없다. 이 계정에서 `ssm:ListCommands`나 `ssm:GetCommandInvocation` 권한을 가진 사람은 콘솔의 Systems Manager → Run Command → 실행 이력에서 두 값을 그대로 읽을 수 있다. 30일이 지나면 자동으로 사라진다.

SSM에는 명령 파라미터를 가리는 기능이 없다. `AWS-RunShellScript`의 `commands`는 평범한 문자열 목록이고, 값을 표시하지 않게 표시할 방법이 문서에도 API에도 없다. stdin으로 넣는 경로도 없다 — 에이전트는 파라미터를 스크립트 파일로 만들어 실행하기 때문에, 어떤 형태로 넣든 파라미터를 거친다. base64로 감싸도 노출 범위는 같다. 되돌리는 데 키가 필요 없는 인코딩이라 이력을 읽을 수 있는 사람에게는 평문과 다르지 않다.

인스턴스 안쪽 노출은 막아 뒀다. 토큰은 0600 파일에 쓰고 그 파일을 읽자마자 지운 뒤 `setup.sh`를 실행한다. 토큰이 명령행 인자로 넘어가지 않으므로 같은 서버의 다른 사용자가 `ps aux`로 볼 수 없고, 셸 이력에도 남지 않는다. 다만 SSM 에이전트가 만드는 스크립트 파일(`/var/lib/amazon/ssm/` 아래)에는 명령 본문이 그대로 남는다. 이 디렉터리는 root 전용이라 일반 사용자는 읽을 수 없다.

대안은 SSM Parameter Store를 한 번 거치는 방식이다. 워크플로가 `ssm:PutParameter`로 두 값을 `SecureString`에 넣고, 명령 본문에는 파라미터 이름만 싣고, 인스턴스가 `aws ssm get-parameter --with-decryption`으로 꺼내 쓴다. 이러면 명령 이력에 남는 건 이름뿐이다. 대신 세팅이 늘어난다.

| 항목 | 지금 방식 | Parameter Store 경유 |
|---|---|---|
| 명령 이력에 남는 값 | 토큰 평문 (30일) | 파라미터 이름만 |
| CI 역할 추가 권한 | 없음 | `ssm:PutParameter` (해당 파라미터 ARN) |
| 인스턴스 추가 권한 | 없음 | `ssm:GetParameter` + `kms:Decrypt` |
| 값을 볼 수 있는 사람 | Run Command 이력 조회 권한자 | 해당 파라미터 조회 권한자 |

토큰이 읽기 전용 fine-grained이고 AWS 계정 접근자가 팀 내부로 한정된 지금 단계에서는 현재 방식을 유지한다. 계정에 외부 협력사 IAM 사용자가 생기거나 토큰 권한이 넓어지면 Parameter Store 경유로 바꾼다. `EC2_API_KEY`는 노출되면 회전 비용이 낮다 — 값을 바꾸고 워크플로를 다시 돌리면 끝난다.

## 수동 실행 방법

### build-deploy

Actions 탭 → 왼쪽 목록에서 `build-deploy` → 오른쪽 **Run workflow**.

- 브랜치를 고르고 실행한다. `image_tag` 입력을 비워 두면 해당 커밋의 short sha가 태그가 된다.
- 특정 태그로 굽고 싶으면 `image_tag`에 값을 넣는다. 영문·숫자·`.`·`_`·`-`만 허용하고, 그 외 문자는 실행 초반에 거부된다.

main이 아닌 브랜치에서 수동 실행하려면 IAM 신뢰 정책의 `sub` 조건이 `repo:cj-ai-sw/ai:*`여야 한다. `refs/heads/main`으로 좁혀 뒀다면 위임 단계에서 막힌다.

### deploy-ec2 — 코드는 그대로고 모델만 바뀐 경우

허깅페이스에 새 가중치를 올렸는데 레포는 손대지 않은 상황이다. 커밋이 없으니 push 트리거가 걸리지 않고, 인스턴스는 계속 예전 모델로 돈다.

Actions 탭 → 왼쪽 목록에서 `deploy-ec2` → 오른쪽 **Run workflow** → 브랜치 `main` → **Run workflow** 버튼.

`instance_id` 입력은 비워 둔다. 저장소 변수 `EC2_INSTANCE_ID`, 그다음 기본값 `i-0c7dac45358b3fafc` 순으로 채워진다. 다른 인스턴스에 시험 배포할 때만 값을 넣는다.

실행하면 `setup.sh`가 처음부터 다시 돌면서 허깅페이스에서 모델 파일을 새로 받고, ONNX 변환을 다시 하고, 서비스를 재시작한다. venv는 이미 있는 걸 재사용하므로 pip 설치 단계는 빠르게 지나간다. 전체 5분 안팎이고, 재시작 순간의 짧은 끊김을 빼면 그동안 서비스는 계속 응답한다.

인스턴스에 직접 접속해 모델을 바꾸지 않는다. 접속해서 고치면 다음 자동 배포가 그 변경을 덮어쓰고, 무엇이 올라가 있는지 레포만 봐서는 알 수 없게 된다.

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
| `인스턴스 ... 가 SSM 관리 대상이 아닙니다 (PingStatus=None)` | 인스턴스 정지, 인스턴스 프로파일 미연결, 에이전트 미등록 중 하나 | 사전 세팅 3번 수행 후 `describe-instance-information`으로 `Online` 확인 |
| `AccessDeniedException ... ssm:SendCommand` | CI 역할 정책에 인스턴스 ARN 또는 문서 ARN이 빠짐 | `SsmSendCommand` 문의 리소스 두 줄 모두 확인 |
| `시크릿 HF_TOKEN 이 없습니다` | 시크릿 미등록 | Settings → Secrets에 등록 |
| SSM 커맨드가 `Failed` | 인스턴스에서 `setup.sh`가 실패 | `Show instance output` 단계의 출력부터 본다. 대부분 모델 다운로드 실패(토큰 권한)나 메모리 부족 |
| `1800초 안에 끝나지 않았습니다` | 셋업이 상한을 넘김 | 커맨드는 인스턴스에서 계속 돌고 있을 수 있다. 콘솔 Run Command에서 상태 확인 후 `env.POLL_TIMEOUT_SECONDS` 조정 |
| 잡은 성공인데 API 응답이 없음 | 서비스는 떴으나 보안 그룹이 막음 | 인바운드 8000이 호출자 IP 대역에 열려 있는지 확인 ([ec2-deploy.md](ec2-deploy.md)) |

빌드 로그는 Actions 탭에서 해당 실행 → 잡 → 단계 순으로 펼쳐 본다. push된 이미지 URI는 실행 요약(Summary)에 남는다. EC2 배포는 실행 요약에 인스턴스 ID와 SSM 커맨드 ID가 남는다 — 콘솔에서 같은 커맨드를 찾을 때 쓴다.

인스턴스 출력은 API가 24,000자에서 자른다. `setup.sh` 출력은 평소 이 한도에 한참 못 미치지만, pip이 경고를 쏟아내는 실패 상황에서는 뒷부분이 잘릴 수 있다. 그때는 인스턴스에서 `sudo journalctl -u dimension-api -n 100`을 본다.

### 파일이 커지면

`deploy-ec2`는 코드 파일을 SSM 명령 본문에 인라인으로 싣는다. 묶음이 45,000바이트(base64 기준)를 넘으면 전송 전에 실패한다. 현재 15,732바이트이므로 여유는 있지만, `inference/`에 큰 파일이 들어오면 상한에 닿는다.

그때는 상한만 올리지 말고 전달 방식을 바꾼다. S3를 쓸 수 없는 제약에서 남는 선택지는 두 개다. 인스턴스가 레포에서 직접 받게 하거나(배포 전용 읽기 토큰 필요), Lambda 트랙처럼 ECR 이미지로 만들어 인스턴스가 pull 하게 하는 방식이다. 어느 쪽이든 인스턴스에 자격증명이 하나 더 생기므로 그 시점에 다시 판단한다.

## 모델 저장 경로가 바뀔 때

모델 가중치를 허깅페이스가 아닌 다른 곳(S3 등)에서 받게 되면 고칠 곳은 두 군데다.

1. `deploy/lambda/Dockerfile`의 모델 다운로드 단계 — 현재 `snapshot_download`로 받는 부분을 새 경로에서 받는 명령으로 교체한다.
2. 인증 정보 — `HF_TOKEN` 시크릿을 새 저장소에 맞는 것으로 바꾸거나, S3로 옮긴다면 시크릿 대신 IAM 역할에 해당 버킷 읽기 권한을 추가하고 secret 전달 줄을 제거한다.

워크플로의 나머지 부분(빌드·push·Lambda 갱신)은 그대로 둔다. 모델을 빌드 타임에 이미지에 굽는 구조 자체가 바뀌지 않기 때문이다.

## 아직 안 한 것

- **빌드 캐시** — 현재 캐시 설정이 없다. torch 설치와 모델 다운로드를 켜면 매 빌드가 처음부터 돌아 시간이 길어진다. GitHub Actions 캐시는 저장소당 10GB 제한이 있어 이 이미지 크기에는 맞지 않고, ECR을 캐시 저장소로 쓰는 방식(`type=registry`)이 대안이다. 실제 빌드 시간을 측정한 뒤 판단한다.
- **배포 후 동작 확인** — 갱신 뒤 `/health` 호출로 확인하는 단계가 없다. 함수 URL이 생긴 뒤에 추가한다. EC2 쪽은 `setup.sh`가 인스턴스 안에서 `127.0.0.1:8000/health`를 확인하므로 이 항목에 해당하지 않는다.
- **EC2 다중 인스턴스** — `deploy-ec2`는 인스턴스 하나에만 보낸다. 여러 대로 늘리면 태그 기반 타깃(`--targets Key=tag:...`)과 순차 배포 비율(`--max-errors`, `--max-concurrency`)을 함께 정해야 한다.
- **롤백** — 배포가 실패하면 인스턴스는 새 코드를 받은 상태로 남는다. 이전 커밋으로 되돌리려면 해당 커밋에서 `deploy-ec2`를 수동 실행한다. 자동 롤백은 없다.
- **토큰 전달 경로** — SSM 명령 이력에 토큰이 30일간 평문으로 남는다. 위 "토큰 노출 범위" 참고.
