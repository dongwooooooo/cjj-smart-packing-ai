"""치수 추정 API 서버 (B담당, k_under=1.6).

    uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1

워커는 1개로 둔다. 프로세스마다 모델을 따로 올려 메모리가 늘어난다. 동시 요청을
늘리려면 워커가 아니라 인스턴스를 키우는 편이 낫다(A담당 server.py와 동일한 이유).

안전마진(safe)/신뢰도(confidence) 필드는 이번 배포에 포함하지 않는다
(2026-08-24 결정 — calibration.json/safety.json 표본 부족으로 노이즈해서 제외).
"""
import os
import time
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from dimension import load_model

MODEL_DIR = os.environ.get("MODEL_DIR", ".")
# 추론 스레드 수 (ONNX Runtime intra_op / torch set_num_threads — 백엔드에 따라 적용).
# 컨테이너/인스턴스에 할당된 CPU 수보다 많이 잡으면 스레드 경합으로 크게 느려진다.
# 반드시 명시적으로 지정할 것 — 기본값(os.cpu_count())은 호스트 전체 코어 수를 볼 수
# 있어 컨테이너 쿼터보다 클 위험이 있다.
THREADS = int(os.environ.get("N_THREADS", os.cpu_count() or 2))
# API_KEY를 환경변수로 주면 X-API-Key 헤더를 검사한다. 안 주면 검사하지 않는다.
# 주소만 알면 누구나 호출할 수 있으므로, 팀원에게 공유할 때는 반드시 설정할 것.
API_KEY = os.environ.get("API_KEY", "")


def check_key(x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "X-API-Key 헤더가 없거나 틀렸습니다")


app = FastAPI(title="물류 물품 치수 추정 (B)", version="1.0")
_t0 = time.time()
MODEL = load_model(MODEL_DIR, threads=THREADS)
LOAD_SEC = time.time() - _t0


@app.get("/health")
def health():
    return {"status": "ok", "threads": THREADS, "load_sec": round(LOAD_SEC, 2),
            "auth": "required" if API_KEY else "none"}


@app.post("/predict", dependencies=[Depends(check_key)])
async def predict(
    images: List[UploadFile] = File(..., description="사진 3장 (shot1 cam1/2/3)"),
    views: Optional[str] = Form(None, description='촬영 각도, 예: "1-1,1-2,1-3"'),
):
    """사진 → 가로·세로·높이(cm).

    views를 주면 그 순서대로 슬롯에 배치한다. 안 주면 파일명
    `..._{shot}_{cam}.jpg`에서 읽고, 그것도 없으면 순서대로 채우며 경고를 남긴다.
    슬롯이 어긋나면 오차가 커지므로 되도록 지정할 것.
    """
    if len(images) != 3:
        raise HTTPException(400, "사진은 3장이어야 합니다 (shot1 cam1/2/3)")
    blobs = [await f.read() for f in images]
    names = [f.filename or "" for f in images]

    v = None
    if views:
        try:
            v = [tuple(int(x) for x in tok.strip().split("-")) for tok in views.split(",")]
        except ValueError:
            raise HTTPException(400, 'views 형식이 잘못됐습니다. 예: "1-1,1-2,1-3"')
        if len(v) != len(images):
            raise HTTPException(400, "views 개수가 사진 수와 다릅니다")

    t = time.time()
    srcs = [b if not n else _Named(b, n) for b, n in zip(blobs, names)]
    r = MODEL.predict(srcs, views=v)
    r["elapsed_ms"] = round((time.time() - t) * 1000)
    return r


class _Named(bytes):
    """파일명을 달고 다니는 bytes — 슬롯 자동 배치에 쓴다."""

    def __new__(cls, data, name):
        o = super().__new__(cls, data)
        o.name = name
        return o


# Lambda entrypoint — active only when mangum is installed (deploy/lambda image).
try:
    from mangum import Mangum
    handler = Mangum(app)
except ImportError:
    pass
