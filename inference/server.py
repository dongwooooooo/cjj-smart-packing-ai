"""FastAPI inference server skeleton.

Mirrors the API surface of the production service so this skeleton can
be swapped for the real implementation (from HF
ek09/logistics-dimension-3view) without changing callers.

Endpoints:
    GET  /health   - liveness check, no auth required.
    POST /predict  - multipart upload, field name "images", expects
                     exactly 3 files. Currently returns 501 because the
                     real estimation logic is not implemented yet.

Auth:
    If the API_KEY environment variable is set, requests to protected
    endpoints must include a matching X-API-Key header. If API_KEY is
    unset, no auth is enforced.
"""

import logging
import os
import secrets

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from dimension import estimate_dimensions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inference.server")

API_KEY = os.environ.get("API_KEY")
N_THREADS = os.environ.get("N_THREADS")

if N_THREADS:
    logger.info("N_THREADS=%s (torch thread count not yet applied in skeleton)", N_THREADS)
else:
    logger.info("N_THREADS not set; torch thread count left at default")

if API_KEY:
    logger.info("API_KEY is set; /predict requires X-API-Key header")
else:
    logger.info("API_KEY is not set; /predict runs without auth")

app = FastAPI(title="logistics-dimension-3view (skeleton)")

REQUIRED_IMAGE_COUNT = 3
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def verify_api_key(x_api_key: str | None) -> None:
    """Raise 401 if API_KEY is configured and the header doesn't match."""
    if not API_KEY:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


def validate_images(images: list[UploadFile]) -> None:
    """Validate image count and content type before running inference."""
    if len(images) != REQUIRED_IMAGE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"Expected exactly {REQUIRED_IMAGE_COUNT} images, got {len(images)}",
        )
    for image in images:
        if image.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported content type '{image.content_type}' for file '{image.filename}'",
            )


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness check. No auth required."""
    return JSONResponse({"status": "ok", "model_loaded": False})


@app.post("/predict")
async def predict(
    images: list[UploadFile] = File(...),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> JSONResponse:
    """Estimate package dimensions from 3 uploaded view images."""
    verify_api_key(x_api_key)
    validate_images(images)

    try:
        estimate_dimensions(images)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # Unreachable until estimate_dimensions is implemented, kept for
    # interface parity with the eventual real response shape.
    raise HTTPException(status_code=501, detail="Not implemented")


# AWS Lambda handler, only active when mangum is installed.
try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:
    pass
