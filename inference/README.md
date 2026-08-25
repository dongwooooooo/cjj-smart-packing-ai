# inference — 치수 추정 추론 API

물류 상품 사진 3장(shot1 cam1/2/3)에서 가로·세로·높이(cm)를 추정한다.
코드 원천은 허깅페이스 `althdgk/cj_A.LTS_AI_B_2026`에서 가져온 B담당 배포 패키지이고, 이 저장소가 이후 수정의 단일 원천이다.

| 파일 | 역할 |
|---|---|
| `server.py` | FastAPI 서버. 끝의 Mangum 핸들러는 Lambda 배포에서만 활성화 |
| `dimension.py` | 모델 로드·전처리·추론 (`load_model`, `OnnxDimensionModel`, `DimensionModel`) |
| `export_onnx.py` | `model.safetensors` → `model.onnx` 변환 + 출력 대조 |
| `inference.py` | CLI 단독 실행 도구 (`python inference.py 1.jpg 2.jpg 3.jpg`, torch 경로) |
| `requirements.txt` | 변환·로컬 검증용 전체 의존성 (CPU 전용 torch 포함) |
| `requirements-runtime.txt` | 추론 런타임 전용 의존성 (torch 없음, onnxruntime) |

모델 가중치(`model.safetensors` 53MB), 변환 결과(`model.onnx` 53MB), `config.json`은 커밋하지 않는다 — 가중치는 같은 허깅페이스 저장소에서 받고, `model.onnx`는 받은 가중치에서 만든다.

## 추론 백엔드 두 가지

`load_model(model_dir, threads)`가 `model_dir`을 보고 고른다. 어느 쪽을 골랐는지 기동 로그에 찍힌다.

| 조건 | 고르는 백엔드 | 필요한 의존성 |
|---|---|---|
| `model_dir/model.onnx` 있음 | `OnnxDimensionModel` | `requirements-runtime.txt` (onnxruntime) |
| 없음 | `DimensionModel` | `requirements.txt` (torch·timm·safetensors) |

`predict()` 시그니처와 응답은 두 백엔드가 같다. 전처리·슬롯 배치·후처리는 numpy로 한 벌만 두고 공유하고, 백본 실행만 갈라진다.

**모델 업로더는 지금처럼 `model.safetensors`만 허깅페이스에 올리면 된다.** 배포 이미지에 들어가는 `model.onnx`는 Lambda 이미지 빌드 중에 `export_onnx.py`가 만든다. 자세한 흐름은 [docs/lambda-deploy.md](../docs/lambda-deploy.md).

## 로컬 실행

```bash
cd inference
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 모델 받기 (읽기 전용 토큰 필요)
python3 -c "from huggingface_hub import snapshot_download; \
snapshot_download('althdgk/cj_A.LTS_AI_B_2026', token='<HF_TOKEN>', \
allow_patterns=['model.safetensors','config.json'], local_dir='.')"

N_THREADS=4 API_KEY=test uvicorn server:app --host 0.0.0.0 --port 8000
```

여기까지만 하면 `model.onnx`가 없으므로 torch 경로로 뜬다. 배포 이미지와 같은 ONNX 경로로 확인하려면 변환을 한 번 돌린다.

```bash
python export_onnx.py .          # -> ./model.onnx, torch 출력과 대조까지 수행
```

변환 결과가 torch 출력과 `max diff 1e-4`를 넘게 벌어지면 스크립트가 비정상 종료한다. 다시 서버를 띄우면 `model.onnx`를 찾아 ONNX 경로로 뜬다.

배포 이미지와 같은 의존성만으로 확인하려면 별도 가상환경에 `requirements-runtime.txt`만 설치한다. torch 없이 임포트·추론이 되는지 여기서 걸러진다. 단, `model.onnx`를 만들려면 torch가 필요하므로 변환은 위 환경에서 먼저 끝내야 한다.

## 호출

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/predict \
  -H "X-API-Key: test" \
  -F "images=@1.jpg" -F "images=@2.jpg" -F "images=@3.jpg" \
  -F "views=1-1,1-2,1-3"
```

응답: `{"length","width","height","unit":"cm","views_used","warnings","elapsed_ms"}`

`views`를 생략하면 파일명 `..._{shot}_{cam}.jpg`에서 슬롯을 읽고, 그것도 없으면 순서대로 배치하며 경고를 남긴다. 슬롯이 어긋나면 오차가 커지므로 지정을 권장.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MODEL_DIR` | `.` | `config.json`과 `model.onnx`(없으면 `model.safetensors`) 위치 |
| `N_THREADS` | cpu_count | 추론 스레드 수. ONNX 경로는 `intra_op_num_threads`, torch 경로는 `set_num_threads`로 들어간다. 컨테이너 CPU 쿼터에 맞춰 반드시 명시 |
| `API_KEY` | (없음) | 설정 시 X-API-Key 헤더 검사, 미설정 시 인증 없음 |

## 로컬 실측 (2026-08-25, macOS, threads=4)

같은 사진 3장, 워밍업 1회 뒤 5회 중앙값.

| 항목 | torch | ONNX Runtime |
|---|---|---|
| `predict()` | 1,070ms | 185ms |
| 모델 로드 | 3.10s | 0.22s |
| 예측값 | 61.6 / 55.8 / 4.0 cm | 61.6 / 55.8 / 4.0 cm |

`torch.onnx.export` 직후 대조에서 원시 출력 `max diff 7.45e-08`. 배포 환경(Lambda) 수치는 재측정 예정.
