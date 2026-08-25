"""물류 물품 치수 추정 (B담당, k_under=1.6) — 백엔드에 그대로 붙이는 모듈.

서버 기동 시 한 번 로드하고, 요청마다 predict()를 부른다.

    from dimension import load_model

    model = load_model("/opt/model", threads=6)
    r = model.predict([img1, img2, img3])
    # {'length': 9.7, 'width': 9.7, 'height': 18.4, 'unit': 'cm',
    #  'views_used': 3, 'warnings': []}

추론 백엔드는 두 가지고, load_model()이 model_dir을 보고 고른다.

  - OnnxDimensionModel — model.onnx가 있으면 이쪽. onnxruntime만 있으면 돌아가고
    torch를 임포트하지 않는다. 배포 이미지(Lambda)가 쓰는 경로다.
  - DimensionModel — model.safetensors + torch. 변환 원본이자 로컬 검증용이다.

model.onnx는 저장소에 커밋하지 않는다. 모델 업로더는 허깅페이스에 여전히
model.safetensors만 올리고, 변환은 export_onnx.py가 이미지 빌드 중에 수행한다.

사진 3장은 **각자 정해진 슬롯**(view_order)에 들어가야 한다. 파일명이
`..._{shot}_{cam}.jpg` 형식이면 자동으로 배치되고, 아니면 views 인자로 직접 준다.
순서를 틀리면 오차가 커진다(work/eunkyung 쪽 A모델 사례: 1.47 -> 4.18cm, 슬롯이
6개라 더 컸음; B는 3슬롯뿐이라 뒤바뀔 조합이 A보다 적지만 원칙은 동일).

A담당(eunkyung) 모델과의 구조 차이 — 그대로 베끼지 않고 B 구조에 맞게 새로 썼다:
  - 세그멘테이션 없음 (in_chans 항상 3, RGB만)
  - 예측 헤드 1개(절대값만, dual head 아님 — target_space는 항상 "norm")
  - confidence/calibration, safety(안전마진) 기능 없음 — 이번 배포에서 완전히 제외하기로
    결정함(2026-08-24). 필요해지면 build_calibration.py를 다시 돌려 추가할 수 있다.
  - sort_lw 없음 — B 학습 데이터의 length/width 라벨은 정렬(가로=긴 변) 처리를 거치지
    않았다(dataset.py의 build_item_records 확인 완료). config.json에도 그대로
    "sort_lw": false로 박아둔다 — A 모델의 값을 그대로 가져오면 예측이 틀어진다.
"""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

_VIEW_RE = re.compile(r"_(\d+)_(\d+)\.[a-zA-Z]+$")

ONNX_FILENAME = "model.onnx"


def _build_net(cfg):
    """학습 때 쓴 구조를 그대로 재현한다 (뷰별 백본 공유 -> concat -> MLP).
    work/misong/model.py의 MultiViewDimensionModel과 동일 — 여기서는 배포
    패키지가 torch+timm 외 이 저장소 코드에 의존하지 않도록 다시 정의한다.

    torch/timm 임포트를 이 함수 안에 둔다. 모듈 최상단에 두면 onnxruntime만
    설치된 배포 이미지에서 dimension 임포트 자체가 실패한다."""
    import timm
    import torch.nn as nn

    class _Net(nn.Module):
        def __init__(self, cfg):
            super().__init__()
            self.n_views = len(cfg["view_order"])
            self.view_dim = cfg["feat_dim"]
            self.backbone = timm.create_model(cfg["backbone"], pretrained=False,
                                              num_classes=0, in_chans=cfg.get("in_chans", 3))
            self.reduce = nn.Linear(self.view_dim * self.n_views, 512)
            self.head = nn.Sequential(
                nn.Linear(512, 256), nn.ReLU(inplace=True),
                nn.Dropout(0.2), nn.Linear(256, 3),
            )

        def forward(self, images, mask):
            """images: (B,V,3,H,W), mask: (B,V) — 누락 뷰는 0."""
            B, V, C, H, W = images.shape
            x = images.view(B * V, C, H, W)
            feats = self.backbone(x).view(B, V, self.view_dim)
            feats = feats * mask.unsqueeze(-1)
            feats = feats.reshape(B, V * self.view_dim)
            return self.head(self.reduce(feats))

    return _Net(cfg)


class _BaseDimensionModel:
    """전처리·슬롯 배치·후처리를 담는다. 백본 실행(_forward)만 하위 클래스가 채운다.

    전처리를 numpy로만 쓴 이유는 두 백엔드가 같은 코드를 공유하게 하려는 것이다.
    torch 경로와 ONNX 경로가 각자 전처리를 들고 있으면 둘이 조금씩 어긋나도
    알아채기 어렵다."""

    def __init__(self, model_dir: str | Path):
        d = Path(model_dir)
        self.cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
        self.view_order = [tuple(v) for v in self.cfg["view_order"]]
        self.h, self.w = self.cfg["input_size"]
        self._mean = np.asarray(self.cfg["imagenet_mean"], dtype=np.float32).reshape(3, 1, 1)
        self._std = np.asarray(self.cfg["imagenet_std"], dtype=np.float32).reshape(3, 1, 1)
        self._tgt_mean = np.asarray(self.cfg["tgt_mean"], dtype=np.float32)
        self._tgt_std = np.asarray(self.cfg["tgt_std"], dtype=np.float32)

    # ------------------------------------------------------------------ 입력
    def _to_image(self, src) -> Image.Image:
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        return Image.open(src).convert("RGB")

    def _preprocess(self, im: Image.Image) -> np.ndarray:
        arr = np.asarray(im.resize((self.w, self.h), Image.BILINEAR), dtype=np.float32) / 255.0
        return (arr.transpose(2, 0, 1) - self._mean) / self._std

    def _slot(self, src, given):
        if given is not None:
            return self.view_order.index(tuple(given)) if tuple(given) in self.view_order else None
        name = getattr(src, "name", src if isinstance(src, (str, os.PathLike)) else None)
        if name is not None:
            m = _VIEW_RE.search(os.path.basename(str(name)))
            if m:
                v = (int(m.group(1)), int(m.group(2)))
                return self.view_order.index(v) if v in self.view_order else None
        return None

    def _batch(self, images: Sequence, views: Sequence | None):
        """사진들 -> ((1,V,3,H,W), (1,V), warnings)."""
        warnings = []
        rgb = np.stack([self._preprocess(self._to_image(s)) for s in images])

        slots, free = [], list(range(len(self.view_order)))
        for j, src in enumerate(images):
            s = self._slot(src, views[j] if views else None)
            if s is None:
                warnings.append(
                    f"{j+1}번째 사진의 촬영 각도를 알 수 없어 순서대로 배치했습니다. "
                    "슬롯이 어긋나면 오차가 커집니다 — views 인자를 주세요.")
                s = next(i for i in free if i not in slots)
            slots.append(s)

        x = np.zeros((len(self.view_order), 3, self.h, self.w), dtype=np.float32)
        keep = np.zeros(len(self.view_order), dtype=np.float32)
        for j, s in enumerate(slots):
            x[s] = rgb[j]
            keep[s] = 1.0
        return x[None], keep[None], warnings

    # -------------------------------------------------------------------- 추론
    def _forward(self, images: np.ndarray, mask: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def predict(self, images: Sequence, views: Sequence | None = None) -> dict:
        """사진들 -> {"length","width","height","unit","views_used","warnings"}.

        views를 주면 그 순서대로 슬롯에 배치한다. 안 주면 파일명에서 읽고,
        그것도 없으면 순서대로 채우며 경고를 남긴다.
        """
        x, keep, warnings = self._batch(images, views)
        pred = self._forward(x, keep)[0] * self._tgt_std + self._tgt_mean
        l, w, h = (round(float(v), 1) for v in pred)
        return {"length": l, "width": w, "height": h, "unit": "cm",
                "views_used": int(keep.sum()), "warnings": warnings}


class DimensionModel(_BaseDimensionModel):
    """torch + model.safetensors 경로. ONNX 변환의 원본이자 로컬 검증 기준이다."""

    def __init__(self, model_dir: str | Path, threads: int | None = 16):
        super().__init__(model_dir)
        import torch
        from safetensors.torch import load_file

        self._torch = torch
        # 컨테이너 CPU 쿼터보다 많은 스레드를 잡으면 경합으로 크게 느려진다.
        # 반드시 실제 할당량에 맞춰 지정할 것.
        if threads:
            torch.set_num_threads(threads)

        self.net = _build_net(self.cfg)
        self.net.load_state_dict(load_file(Path(model_dir) / "model.safetensors"), strict=True)
        self.net.eval()

    @classmethod
    def from_pretrained(cls, repo_id: str, token: str | None = None, **kw):
        from huggingface_hub import snapshot_download
        return cls(snapshot_download(repo_id, token=token), **kw)

    def _forward(self, images: np.ndarray, mask: np.ndarray) -> np.ndarray:
        torch = self._torch
        with torch.no_grad():
            out = self.net(torch.from_numpy(images), torch.from_numpy(mask))
        return out.numpy()


class OnnxDimensionModel(_BaseDimensionModel):
    """onnxruntime + model.onnx 경로. torch를 임포트하지 않는다.

    같은 입력에서 torch 경로와 출력이 일치하는지는 export_onnx.py가 변환 직후
    확인한다 (max diff > 1e-4면 변환 실패로 처리)."""

    def __init__(self, model_dir: str | Path, threads: int | None = 16,
                 onnx_path: str | Path | None = None):
        super().__init__(model_dir)
        import onnxruntime as ort

        so = ort.SessionOptions()
        # torch.set_num_threads와 같은 이유 — 컨테이너 CPU 쿼터에 맞춘다.
        if threads:
            so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.onnx_path = Path(onnx_path) if onnx_path else Path(model_dir) / ONNX_FILENAME
        self.sess = ort.InferenceSession(str(self.onnx_path), so,
                                         providers=["CPUExecutionProvider"])

    def _forward(self, images: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return self.sess.run(None, {"images": images, "mask": mask})[0]


def load_model(model_dir: str | Path = ".", threads: int | None = 16) -> _BaseDimensionModel:
    """model_dir에 model.onnx가 있으면 ONNX 경로, 없으면 torch 경로를 쓴다.

    배포 이미지에는 model.onnx만 들어가므로 항상 ONNX 경로다. 로컬에서
    export_onnx.py를 돌리지 않았으면 torch 경로로 그대로 동작한다."""
    onnx_path = Path(model_dir) / ONNX_FILENAME
    if onnx_path.exists():
        print(f"[dimension] ONNX Runtime 백엔드 — {onnx_path}", flush=True)
        return OnnxDimensionModel(model_dir, threads=threads, onnx_path=onnx_path)
    print(f"[dimension] torch 백엔드 — {onnx_path} 없음", flush=True)
    return DimensionModel(model_dir, threads=threads)
