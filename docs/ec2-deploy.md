# EC2 배포

치수 추정 API를 EC2(Amazon Linux 2023) 위에 상시 서버로 올리는 방법이다. 스크립트는 `deploy/ec2/`에 있다.

## 사전 준비

### 허깅페이스 토큰

`deploy/ec2/setup.sh`와 `deploy/ec2/run_dome.sh`는 모델을 허깅페이스 비공개 저장소에서 내려받는다. 토큰은 fine-grained 토큰으로 발급하고, 권한은 읽기 전용(Read)만 준다.

1. 허깅페이스 설정 → Access Tokens → Create new token → Token type을 **Fine-grained**로 선택
2. Repository permissions에서 아래 저장소 2개에 **Read** 권한만 추가
   - `althdgk/cj_A.LTS_AI_B_2026` (메인 모델)
   - `ek09/logistics-dimension-dome-fast` (흰 돔 전용 모델 — 띄울 경우에만. 소유 계정이 달라 별도 권한 부여 필요)
3. 그 외 권한(쓰기, 다른 저장소 접근 등)은 모두 끈 채로 발급

발급한 토큰은 인스턴스에 파일로 저장하지 않고 실행할 때 환경변수로만 넘긴다. 아래 "배포 절차" 참고.

### 보안 그룹

- 인바운드 8000(메인 모델), 8001(흰 돔 전용 모델) 포트는 API를 실제로 호출하는 주체의 IP 대역으로만 연다.
- `0.0.0.0/0`으로 열지 않는다. 열려 있으면 인터넷 전체에서 모델을 호출할 수 있고, `API_KEY`를 안 걸어두면 누구나 호출 가능한 상태가 된다.
- SSH(22)는 별도로 관리자 IP 대역만 허용한다.

### 인스턴스 사양

- CPU 전용 추론이라 GPU 인스턴스가 필요 없다.
- `t3.micro`(RAM 1GiB)로 띄우면 `setup.sh`가 자동으로 2G 스왑을 만든다 — torch/torchvision 설치와 모델 로딩 중 메모리 부족으로 죽는 걸 막기 위해서다. 다만 스왑은 디스크 I/O라 느리므로, 상시 트래픽을 받을 거면 `t3.small`(RAM 2GiB) 이상을 권장한다.
- 루트 볼륨은 최소 20GiB — torch/torchvision 등 파이썬 패키지와 모델 가중치를 합치면 수 GB를 차지한다.

## 배포 절차

인스턴스에 SSH로 접속한 뒤 저장소를 내려받고 실행한다.

```bash
git clone <이 저장소 URL>
cd ai/deploy/ec2

export HF_TOKEN=hf_xxx      # 발급한 fine-grained 토큰
export API_KEY=xxx          # 호출자 인증용 (선택, 비우면 인증 없이 열림)
bash setup.sh
```

`export`로 넘기면 토큰이 명령어 인자로 남지 않아 `ps aux`나 `~/.bash_history`에 노출되지 않는다. 스크립트는 마지막에 자체적으로 `curl http://127.0.0.1:8000/health`를 호출해 기동을 확인하고 결과를 출력한다.

흰 촬영 돔 배경 전용 고속 모델을 같은 인스턴스에 추가로 띄우려면 `setup.sh` 완료 후 이어서 실행한다.

```bash
export HF_TOKEN=hf_xxx
bash run_dome.sh
```

이 모델은 흰 촬영 돔 배경 전용이다. 다른 배경 사진에 쓰면 부피 오차가 394~519%까지 벌어지므로, 촬영 환경이 흰 돔인 경우에만 사용한다.

### predict 호출 예시

```bash
curl -X POST http://<인스턴스IP>:8000/predict \
  -H "X-API-Key: xxx" \
  -F "images=@1.jpg" \
  -F "images=@2.jpg" \
  -F "images=@3.jpg"
```

`images` 필드로 3면 사진 3장을 보낸다. 응답 형식은 `inference/server.py` 정의를 따른다.

## 운영

systemd로 기동하므로 서버 재부팅 시 `dimension-api` 서비스가 자동으로 다시 뜬다.

```bash
sudo systemctl status dimension-api    # 상태 확인
sudo systemctl restart dimension-api   # 재시작
sudo journalctl -u dimension-api -f    # 실시간 로그
sudo journalctl -u dimension-api -n 100  # 최근 로그 100줄
```

`run_dome.sh`로 띄운 포트 8001 프로세스는 systemd가 아니라 `nohup`으로 떠 있다. 로그는 `~/api_dome.log`, 종료는 `pkill -f "uvicorn server:app.*8001"`로 한다.

## 장애 대응

### `operator torchvision::nms does not exist`

torch와 torchvision의 빌드가 서로 안 맞을 때 나는 에러다. torch를 CPU 전용 저장소(`https://download.pytorch.org/whl/cpu`)에서 받은 뒤 다른 패키지(`segmentation-models-pytorch` 등)를 설치하면, 그 패키지가 의존성으로 torchvision을 PyPI 기본 저장소(CUDA 빌드)에서 새로 끌어와 버전 짝이 어긋난다.

`setup.sh`는 torch와 torchvision을 같은 `pip install` 명령 한 줄로, 같은 CPU 저장소에서 함께 받아 이 문제를 피한다. 수동으로 패키지를 추가·재설치할 때도 이 순서를 지킨다.

### 기동 실패

`setup.sh`가 헬스 체크에 실패하면 `sudo journalctl -u dimension-api -n 50`으로 원인을 확인한다. 메모리 부족(OOM)으로 죽었다면 인스턴스 사양을 올리거나 스왑 크기를 키운다.

## 모델 저장 경로가 바뀔 때

모델을 허깅페이스가 아닌 다른 곳(S3 등)에 두게 되면 `MODEL_SOURCE` 환경변수만 바꾸면 된다. 단, 현재 `setup.sh`/`run_dome.sh`는 허깅페이스 `snapshot_download`를 호출하는 구조라, 저장소 종류 자체가 바뀌면(S3 등) 다운로드 부분 코드도 함께 고쳐야 한다.

```bash
export MODEL_SOURCE=althdgk/다음-저장소명   # 새 저장소명
bash setup.sh
```
