# Inference API

이 폴더가 추론 API의 단일 원천(single source of truth)입니다. 배포용 컨테이너는
이 폴더의 `server.py`, `dimension.py`, `requirements.txt`를 그대로 사용합니다.

## 현재 상태 — 스켈레톤

실제 추정 로직은 아직 없습니다. `/predict`는 이미지 개수·형식 검증과 API 키 검증까지만
수행하고, 이후 `dimension.estimate_dimensions()`를 호출하면서 `NotImplementedError`를
받아 501 Not Implemented를 반환합니다.

실제 로직은 허깅페이스 비공개 저장소 `ek09/logistics-dimension-3view`의
`server.py`, `dimension.py`에 있습니다. 접근 토큰이 준비되면 아래 절차로 반영합니다.

1. HF 저장소에서 `server.py`, `dimension.py`를 받는다
2. 이 폴더의 동일 파일과 diff를 뜬다 — 인터페이스(엔드포인트, 인증, 응답 형식)는
   이 폴더 쪽을 기준으로 유지하고, 추정 로직만 병합한다
3. `requirements.txt`에 주석으로 남겨둔 `torch`, `timm`,
   `segmentation-models-pytorch`, `safetensors`, `scipy`, `huggingface_hub`를
   실제로 추가한다
4. `python3 -m py_compile server.py dimension.py`와 로컬 실행으로 재검증한다

## 로컬 실행

```bash
cd inference
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn server:app --host 0.0.0.0 --port 8000
```

### 동작 확인

```bash
# 상태 확인 — 인증 불필요
curl http://localhost:8000/health

# 추론 요청 — 이미지 3장 업로드 (스켈레톤 단계에서는 501 반환)
curl -X POST http://localhost:8000/predict \
  -F "images=@front.jpg" \
  -F "images=@side.jpg" \
  -F "images=@top.jpg"

# API_KEY를 설정했다면 헤더 추가
curl -X POST http://localhost:8000/predict \
  -H "X-API-Key: your-api-key" \
  -F "images=@front.jpg" \
  -F "images=@side.jpg" \
  -F "images=@top.jpg"
```

## 환경 변수

| 변수 | 필수 여부 | 설명 |
|---|---|---|
| `API_KEY` | 선택 | 설정하면 `/predict`가 `X-API-Key` 헤더를 검사한다. 미설정 시 인증 없이 동작 |
| `N_THREADS` | 선택 | torch 스레드 수. 스켈레톤 단계에서는 로그만 남기고 실제로 적용하지 않는다 |
