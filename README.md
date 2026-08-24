# ai — 물류 치수 추정 추론 API·배포

물류 상품 3면 사진에서 치수를 추정하는 추론 API 코드와 AWS 배포 구성을 관리하는 저장소다.
모델 가중치는 이 저장소에 두지 않는다 — 현재는 허깅페이스 비공개 저장소에서 받고, 추후 저장 경로가 바뀌면 배포 스크립트의 환경변수만 교체한다.

## 저장소 구조

```
ai/
├── inference/        # 추론 API 코드 (FastAPI) — 단일 원천
├── deploy/
│   ├── ec2/          # EC2(Amazon Linux 2023) 배포 스크립트·systemd 유닛
│   └── lambda/       # Lambda 컨테이너 이미지 (Dockerfile, Mangum)
├── .github/workflows/ # CI/CD (GitHub Actions)
└── docs/             # 배포·CI/CD·작업 방법 문서
```

## 모델·코드 흐름

| 구분 | 위치 | 비고 |
|---|---|---|
| 추론 API 코드 | `inference/` | 이 저장소가 원천. AWS 인스턴스에서 직접 수정하지 않는다 |
| 모델 가중치 | 허깅페이스 `ek09/logistics-dimension-3view` (비공개) | `MODEL_SOURCE` 환경변수로 교체 가능 |
| 흰 돔 전용 고속 모델 | `ek09/logistics-dimension-dome-fast` (비공개) | 흰 촬영 돔 배경 전용 — 다른 배경에서 부피 오차 394~519% |

## 배포 트랙 2개

- **EC2**: 상시 서버. [docs/ec2-deploy.md](docs/ec2-deploy.md)
- **Lambda**: 간헐 트래픽용 서버리스(컨테이너 이미지, 최대 10GB 메모리 ≈ 6 vCPU). [docs/lambda-deploy.md](docs/lambda-deploy.md)

CI/CD 사용법은 [docs/cicd.md](docs/cicd.md), 브랜치·MR 작업 방법은 [docs/workflow.md](docs/workflow.md) 참고.

## 문서 언어

문서·커밋 메시지는 한국어, 코드·코드 주석은 영어로 작성한다.
