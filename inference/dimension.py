"""물류 물품 치수 추정 (B담당, k_under=1.6) — 백엔드에 그대로 붙이는 모듈.

서버 기동 시 한 번 로드하고, 요청마다 predict()를 부른다.

    from dimension import DimensionModel

    model = DimensionModel.from_pretrained("<repo_id>", token=os.environ["HF_TOKEN"])
    r = model.predict([img1, img2, img3])
    # {'length': 9.7, 'width': 9.7, 'height': 18.4, 'unit': 'cm',
    #  'views_used': 3, 'warnings': []}

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
import torch
import torch.nn as nn
from PIL import Image
from safetensors.torch import load_file

_VIEW_RE = re.compile(r"_(\d+)_(\d+)\.[a-zA-Z]+$")


class _Net(nn.Module):
    """학습 때 쓴 구조를 그대로 재현한다 (뷰별 백본 공유 -> concat -> MLP).
    work/misong/model.py의 MultiViewDimensionModel과 동일 — 여기서는 배포
    패키지가 torch+timm 외 이 저장소 코드에 의존하지 않도록 다시 정의한다."""

    def __init__(self, cfg):
        super().__init__()
        import timm
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


class DimensionModel:
    def __init__(self, model_dir: str | Path, threads: int | None = 16):
        d = Path(model_dir)
        self.cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
        # 컨테이너 CPU 쿼터보다 많은 스레드를 잡으면 경합으로 크게 느려진다.
        # 반드시 실제 할당량에 맞춰 지정할 것.
        if threads:
            torch.set_num_threads(threads)

        self.net = _Net(self.cfg)
        self.net.load_state_dict(load_file(d / "model.safetensors"), strict=True)
        self.net.eval()

        self.view_order = [tuple(v) for v in self.cfg["view_order"]]
        self.h, self.w = self.cfg["input_size"]
        self._mean = torch.tensor(self.cfg["imagenet_mean"]).view(3, 1, 1)
        self._std = torch.tensor(self.cfg["imagenet_std"]).view(3, 1, 1)
        self._tgt_mean = torch.tensor(self.cfg["tgt_mean"])
        self._tgt_std = torch.tensor(self.cfg["tgt_std"])

    @classmethod
    def from_pretrained(cls, repo_id: str, token: str | None = None, **kw):
        from huggingface_hub import snapshot_download
        return cls(snapshot_download(repo_id, token=token), **kw)

    # ------------------------------------------------------------------ 입력
    def _to_image(self, src) -> Image.Image:
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        return Image.open(src).convert("RGB")

    def _preprocess(self, im: Image.Image) -> torch.Tensor:
        arr = np.asarray(im.resize((self.w, self.h), Image.BILINEAR), dtype=np.float32) / 255.0
        return (torch.from_numpy(arr).permute(2, 0, 1) - self._mean) / self._std

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

    def _to_cm(self, out: torch.Tensor) -> torch.Tensor:
        return out * self._tgt_std + self._tgt_mean

    # -------------------------------------------------------------------- 추론
    @torch.no_grad()
    def predict(self, images: Sequence, views: Sequence | None = None) -> dict:
        """사진들 -> {"length","width","height","unit","views_used","warnings"}.

        views를 주면 그 순서대로 슬롯에 배치한다. 안 주면 파일명에서 읽고,
        그것도 없으면 순서대로 채우며 경고를 남긴다.
        """
        warnings = []
        rgb = torch.stack([self._preprocess(self._to_image(s)) for s in images])

        slots, free = [], list(range(len(self.view_order)))
        for j, src in enumerate(images):
            s = self._slot(src, views[j] if views else None)
            if s is None:
                warnings.append(
                    f"{j+1}번째 사진의 촬영 각도를 알 수 없어 순서대로 배치했습니다. "
                    "슬롯이 어긋나면 오차가 커집니다 — views 인자를 주세요.")
                s = next(i for i in free if i not in slots)
            slots.append(s)

        x = torch.zeros(len(self.view_order), 3, self.h, self.w)
        keep = torch.zeros(len(self.view_order))
        for j, s in enumerate(slots):
            x[s] = rgb[j]
            keep[s] = 1.0

        pred = self._to_cm(self.net(x.unsqueeze(0), keep.unsqueeze(0)))[0]
        l, w, h = (round(float(v), 1) for v in pred)
        return {"length": l, "width": w, "height": h, "unit": "cm",
                "views_used": int(keep.sum()), "warnings": warnings}
