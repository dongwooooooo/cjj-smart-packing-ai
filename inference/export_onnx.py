"""model.safetensors -> model.onnx 변환.

    python export_onnx.py <model_dir> [out_path]

model_dir의 config.json + model.safetensors를 dimension.DimensionModel로 읽어
ONNX 그래프로 내보낸다. out_path를 안 주면 `<model_dir>/model.onnx`.

변환 뒤 같은 입력으로 torch 출력과 ONNX 출력을 비교한다. max diff가 1e-4를
넘으면 비정상 종료한다 — 이미지 빌드 중에 돌리므로, 어긋난 그래프가 조용히
배포되는 것을 여기서 막는다.

모델 업로더는 허깅페이스에 model.safetensors만 올리면 된다. 변환은 이 스크립트가
Lambda 이미지 빌드 단계에서 수행한다 (deploy/lambda/Dockerfile의 builder 스테이지).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# 배치 크기 1 고정. predict()는 항상 사진 3장을 한 번에 넣는 단건 호출이라
# 동적 축을 열어둘 이유가 없고, 고정 shape 쪽이 그래프 최적화에 유리하다.
OPSET = 17
TOLERANCE = 1e-4


def export(model_dir: str | Path, out_path: str | Path | None = None,
           tolerance: float = TOLERANCE) -> tuple[Path, float]:
    import torch

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dimension import ONNX_FILENAME, DimensionModel

    d = Path(model_dir)
    out = Path(out_path) if out_path else d / ONNX_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)

    model = DimensionModel(d, threads=None)
    n_views = len(model.view_order)
    images = torch.from_numpy(
        np.random.default_rng(0).standard_normal((1, n_views, 3, model.h, model.w),
                                                 dtype=np.float32))
    mask = torch.ones(1, n_views)

    print(f"[export] {d}/model.safetensors -> {out}")
    print(f"[export] input images=(1,{n_views},3,{model.h},{model.w}) mask=(1,{n_views})")
    # dynamo=False로 기존 TorchScript 기반 exporter를 쓴다. torch 2.9+의 기본
    # dynamo exporter는 timm 백본에서 그래프가 달라질 수 있어 검증된 쪽으로 고정한다.
    torch.onnx.export(
        model.net, (images, mask), str(out),
        input_names=["images", "mask"], output_names=["pred"],
        opset_version=OPSET, dynamo=False,
    )

    import onnxruntime as ort

    sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    got = sess.run(None, {"images": images.numpy(), "mask": mask.numpy()})[0]
    with torch.no_grad():
        ref = model.net(images, mask).numpy()

    diff = float(np.abs(ref - got).max())
    size_mb = out.stat().st_size / 1e6
    print(f"[export] {out.name} {size_mb:.1f}MB, torch 대비 max diff {diff:.2e}")
    # NaN이면 아래 비교가 False가 되어 실패로 떨어진다 (diff > tol 로 쓰면 통과해버린다).
    if not diff <= tolerance:
        raise SystemExit(
            f"[export] 실패: max diff {diff:.2e} > 허용치 {tolerance:.0e}. "
            "변환된 그래프가 torch 출력과 다릅니다 — 이미지에 넣으면 안 됩니다.")
    print("[export] 검증 통과")
    return out, diff


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    export(argv[0], argv[1] if len(argv) > 1 else None)


if __name__ == "__main__":
    main(sys.argv[1:])
