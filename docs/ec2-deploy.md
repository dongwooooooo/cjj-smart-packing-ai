# EC2 배포

치수 추정 API를 EC2(Ubuntu) 위에 상시 서버로 올리는 방법이다. 스크립트는 `deploy/ec2/`에 있다.

역할 분담: **추론 코드는 이 저장소 `inference/`가 원천**이고, 허깅페이스에서는 모델 파일(model.safetensors, config.json)만 받는다. `setup.sh`가 셋업 중에 ONNX 변환을 1회 실행해, 서비스는 ONNX Runtime 백엔드(torch 대비 약 4배 빠름, 로컬 실측)로 기동한다. 변환용 torch는 서버 venv에만 설치되고, 모델 업로더는 계속 torch(safetensors)만 올리면 된다.

## 사전 준비

### 허깅페이스 토큰

`deploy/ec2/setup.sh`는 모델 파일을 허깅페이스 비공개 저장소에서 내려받는다. 토큰은 fine-grained 토큰으로 발급하고, 권한은 읽기 전용(Read)만 준다.

1. 허깅페이스 설정 → Access Tokens → Create new token → Token type을 **Fine-grained**로 선택
2. Repository permissions에서 `althdgk/cj_A.LTS_AI_B_2026`에 **Read** 권한만 추가
3. 그 외 권한(쓰기, 다른 저장소 접근 등)은 모두 끈 채로 발급

발급한 토큰은 인스턴스에 파일로 저장하지 않고 실행할 때 환경변수로만 넘긴다. 아래 "배포 절차" 참고.

### 보안 그룹

- 인바운드 8000 포트는 API를 실제로 호출하는 주체의 IP 대역으로만 연다.
- `0.0.0.0/0`으로 열지 않는다. 열려 있으면 인터넷 전체에서 모델을 호출할 수 있고, `API_KEY`를 안 걸어두면 누구나 호출 가능한 상태가 된다.
- SSH(22)는 별도로 관리자 IP 대역만 허용한다.

### 인스턴스 사양

- CPU 전용 추론이라 GPU 인스턴스가 필요 없다.
- RAM 2GiB 미만이면 `setup.sh`가 자동으로 2G 스왑을 만든다 — torch 설치·변환 중 메모리 부족으로 죽는 걸 막기 위해서다. 상시 트래픽을 받을 거면 RAM 2GiB 이상을 권장한다.
- 루트 볼륨은 최소 20GiB — 변환용 torch와 모델 파일을 합치면 수 GB를 차지한다.

## 배포 절차

로컬(레포를 받은 PC)에서 배포 파일과 추론 코드를 함께 올린 뒤, 접속해서 실행한다.

```bash
scp -r -i <키페어> deploy/ec2/setup.sh deploy/ec2/dimension-api.service inference ubuntu@<인스턴스IP>:~/
ssh -i <키페어> ubuntu@<인스턴스IP>

# 인스턴스 안에서
export HF_TOKEN=hf_xxx      # 발급한 fine-grained 토큰
export API_KEY=xxx          # 호출자 인증용 (선택, 비우면 인증 없이 열림)
bash ~/setup.sh
```

`export`로 넘기면 토큰이 명령어 인자로 남지 않아 `ps aux`나 `~/.bash_history`에 노출되지 않는다. 스크립트는 마지막에 자체적으로 `curl http://127.0.0.1:8000/health`를 호출해 기동을 확인하고 결과를 출력한다.

셋업이 만드는 배치:

| 경로 | 내용 |
|---|---|
| `~/app/` | 레포 `inference/`의 추론 코드 |
| `~/model/` | 허깅페이스에서 받은 model.safetensors·config.json + 변환된 model.onnx |
| `~/venv/` | 파이썬 환경 (변환용 torch 포함) |

**코드가 바뀌었을 때**: 레포에서 `inference/`를 다시 scp로 올리고 `setup.sh`를 재실행하면 된다(이미 있는 venv·모델은 재사용). 인스턴스 안에서 코드를 직접 수정하지 않는다 — 수정은 레포 → PR로.

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

## 장애 대응

### 기동 실패

`setup.sh`가 헬스 체크에 실패하면 `sudo journalctl -u dimension-api -n 50`으로 원인을 확인한다. 메모리 부족(OOM)으로 죽었다면 인스턴스 사양을 올리거나 스왑 크기를 키운다.

### ONNX 변환 실패

`export_onnx.py`는 변환 후 torch 출력과 ONNX 출력을 대조해 오차가 크면 스스로 실패한다. 실패하면 모델 구조가 바뀐 경우이므로 `inference/dimension.py`·`export_onnx.py`를 모델 변경에 맞춰 갱신해야 한다. 변환 없이 급히 띄워야 하면 `~/model/model.onnx`를 지우고 서비스를 재시작한다 — `load_model`이 torch 백엔드로 폴백한다(느리지만 동작).

## 흰 돔 전용 고속 모델

구 저장소(`ek09/logistics-dimension-dome-fast`)가 삭제되면서 함께 제거했다(2026-08-25). 다시 필요해지면 새 저장소 확보 후 setup.sh와 같은 방식으로 추가한다.

## 모델 저장 경로가 바뀔 때

모델을 다른 허깅페이스 저장소로 옮기면 `MODEL_SOURCE` 환경변수만 바꾸면 된다. 저장소 종류 자체가 바뀌면(S3 등) `setup.sh`의 다운로드 부분 코드도 함께 고친다.

```bash
export MODEL_SOURCE=althdgk/다음-저장소명   # 새 저장소명
bash ~/setup.sh
```
