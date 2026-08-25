# inference — 치수 추정 추론 API

물류 상품 사진 3장(shot1 cam1/2/3)에서 가로·세로·높이(cm)를 추정한다.
코드 원천은 허깅페이스 `althdgk/cj_A.LTS_AI_B_2026`에서 가져온 B담당 배포 패키지이고, 이 저장소가 이후 수정의 단일 원천이다.

| 파일 | 역할 |
|---|---|
| `server.py` | FastAPI 서버. 끝의 Mangum 핸들러는 Lambda 배포에서만 활성화 |
| `dimension.py` | 모델 로드·전처리·추론 (`DimensionModel`) |
| `inference.py` | CLI 단독 실행 도구 (`python inference.py 1.jpg 2.jpg 3.jpg`) |
| `requirements.txt` | CPU 전용 torch 포함 의존성 |

모델 가중치(`model.safetensors` 53MB)와 `config.json`은 커밋하지 않는다 — 같은 허깅페이스 저장소에서 받는다.

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
| `MODEL_DIR` | `.` | config.json·model.safetensors 위치 |
| `N_THREADS` | cpu_count | torch 스레드 수. 컨테이너 CPU 쿼터에 맞춰 반드시 명시 |
| `API_KEY` | (없음) | 설정 시 X-API-Key 헤더 검사, 미설정 시 인증 없음 |
