# 별도 작업하는 방법

여러 사람이 이 저장소를 동시에 건드릴 때의 규칙이다. 브랜치를 어떻게 파고, 커밋을 어떻게 쓰고, 어디를 고쳐야 배포까지 이어지는지를 담는다.

## 코드를 고치는 위치

추론 API 코드의 원천은 이 저장소의 `inference/`다. EC2 인스턴스나 Lambda 콘솔에서 직접 고치지 않는다.

| 하려는 일 | 고치는 곳 | 반영 경로 |
|---|---|---|
| 추론 로직·API 응답 변경 | `inference/` | PR 머지 후 `build-deploy.yml`이 이미지를 다시 굽고 Lambda에 반영 |
| Lambda 이미지 구성 변경 | `deploy/lambda/Dockerfile` | 같음 |
| EC2 서버 구성 변경 | `deploy/ec2/` | [ec2-deploy.md](ec2-deploy.md) 참고 |
| 파이프라인 자체 변경 | `.github/workflows/` | [cicd.md](cicd.md) 참고 |

AWS 인스턴스에서 직접 고친 코드는 다음 배포 때 이미지에 덮여 사라진다. 급한 상황에서 인스턴스를 직접 만졌다면 같은 내용을 저장소에도 반영해 둔다.

## 브랜치 규칙

main에 직접 커밋하지 않는다. 브랜치를 파고 PR로 머지한다.

```
<type>/<짧은-설명>
```

| type | 언제 | 예시 |
|---|---|---|
| `feature/` | 기능 추가 | `feature/lambda-serving` |
| `fix/` | 버그 수정 | `fix/predict-content-type` |
| `chore/` | 설정·의존성·잡무 | `chore/pin-fastapi-version` |
| `docs/` | 문서만 수정 | `docs/ec2-troubleshooting` |

브랜치를 팔 때는 어디서 분기하는지 확인한다. main에서 파는 것이 기본이고, 아직 머지되지 않은 다른 브랜치의 결과물이 필요할 때만 그 브랜치에서 판다.

```bash
# 기본
git checkout main && git pull && git checkout -b feature/my-work

# 앞 브랜치의 파일이 필요할 때
git checkout feature/lambda-serving && git checkout -b feature/my-work
```

## 커밋 메시지 형식

```
<type>(<scope>): 제목

- 본문 요점
- 왜 그렇게 했는지
```

- type은 브랜치 type과 같은 목록을 쓴다. `feat`, `fix`, `chore`, `docs`.
- scope는 건드린 영역이다. `inference`, `lambda`, `ec2`, `cicd`, `docs`.
- 제목은 한국어 한 줄. 무엇을 했는지 쓴다.
- 본문에는 무엇을 왜 바꿨는지 쓴다. 어떻게 바꿨는지는 코드가 말한다.

```
feat(lambda): Lambda 컨테이너 이미지·배포 스크립트·적용 문서

- 모델 가중치를 빌드 타임에 이미지에 굽는 방식 채택
- 토큰은 BuildKit secret 으로 전달해 레이어에 남지 않게 함
```

## 현재 열린 브랜치와 머지 순서

브랜치 네 개가 열려 있고, 그중 셋은 서로 위에 쌓여 있다.

```
main
└─ feature/inference-api      추론 API 스켈레톤 (inference/)
   └─ feature/lambda-serving  Lambda 이미지·배포 스크립트 (deploy/lambda/)
      └─ feature/cicd         GitHub Actions·작업 방법 문서

main
└─ feature/ec2-serving        EC2 배포 트랙 (deploy/ec2/)
```

쌓인 셋은 아래에서 위로 머지한다. `feature/inference-api` 다음 `feature/lambda-serving`, 그다음 `feature/cicd`. 순서를 건너뛰면 앞 브랜치가 만든 파일이 main에 없는 상태로 뒤 브랜치가 들어가, 예컨대 워크플로가 참조하는 `deploy/lambda/Dockerfile`이 없어 빌드가 실패한다.

`feature/ec2-serving`은 main에서 바로 분기했고 다른 브랜치와 겹치는 파일이 없다. 순서에 관계없이 아무 때나 머지한다.

앞 브랜치가 머지된 뒤에는 남은 브랜치를 main 기준으로 다시 맞춘다.

```bash
git checkout feature/cicd
git fetch origin
git rebase origin/main
```

## 로컬 검증

PR을 올리기 전에 CI가 하는 검사를 로컬에서 돌려 본다. `ci.yml`과 같은 명령이다.

```bash
# Python 문법
find inference -name '*.py' -not -path '*/__pycache__/*' -print0 \
  | xargs -0 python3 -m py_compile

# 셸 스크립트 문법
find deploy -name '*.sh' -exec bash -n {} \;

# 워크플로 YAML 문법
python3 -c "import yaml,pathlib; [yaml.safe_load(p.read_text()) for p in pathlib.Path('.github/workflows').glob('*.yml')]"
```

추론 코드를 고쳤다면 서버를 띄워 확인한다. 스켈레톤 단계에서는 `/health`가 `{"status":"ok","model_loaded":false}`를 돌려주고 `/predict`는 501을 돌려준다.

```bash
cd inference
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --port 8000

# 다른 터미널에서
curl -s localhost:8000/health
```

Lambda 이미지를 고쳤다면 빌드까지 확인한다. Apple Silicon에서도 `--platform linux/amd64`를 빼지 않는다.

```bash
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 --provenance=false \
  -f deploy/lambda/Dockerfile -t dimension-api:local .
```

## PR을 올릴 때

1. 커밋을 정리한다. 실험하다 남은 커밋은 합친다.
2. main 기준으로 rebase한다.
3. PR 제목은 커밋 메시지와 같은 형식으로 쓴다.
4. 본문에는 무엇을 바꿨는지, 어떻게 확인했는지 쓴다.
5. CI가 통과한 것을 확인하고 리뷰를 요청한다.

`main` 머지 후 `inference/**` 또는 `deploy/lambda/**`가 바뀌었다면 `build-deploy.yml`이 자동으로 돌아 이미지를 굽고 Lambda를 갱신한다. 진행 상황은 Actions 탭에서 본다.

## 머지 후 할 일

- [ ] `docs/README.md`의 상태 표를 갱신한다. 네 문서 모두 "작성 예정"으로 남아 있다. 각 브랜치가 머지될 때마다 해당 줄을 실제 상태로 바꾼다. 브랜치별로 이 표를 고치면 머지할 때마다 같은 줄이 충돌하므로, 브랜치 안에서는 건드리지 않는다.
- [ ] `feature/cicd` 머지 후에는 [cicd.md](cicd.md)의 "사전 세팅 절차"를 수행한다. IAM 역할과 GitHub 시크릿이 없으면 첫 실행이 자격증명 단계에서 실패한다.
