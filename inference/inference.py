"""사진 3장 -> 가로·세로·높이(cm).

  python inference.py 사진1.jpg 사진2.jpg 사진3.jpg

사진마다 들어갈 슬롯이 정해져 있다. 파일명이 `..._{shot}_{cam}.jpg` 형식이면 자동으로
배치되고, 아니면 주는 순서대로 채운다 -- 그 경우 오차가 커질 수 있다.
"""
import json
import re
import sys

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from safetensors.torch import load_file

CFG = json.load(open("config.json", encoding="utf-8"))
H, W = CFG["input_size"]
VIEW_ORDER = [tuple(v) for v in CFG["view_order"]]
NV = len(VIEW_ORDER)


def preprocess(path):
    im = Image.open(path).convert("RGB").resize((W, H), Image.BILINEAR)
    x = torch.from_numpy(np.asarray(im).copy()).float().permute(2, 0, 1) / 255.0
    mean = torch.tensor(CFG["imagenet_mean"]).view(3, 1, 1)
    std = torch.tensor(CFG["imagenet_std"]).view(3, 1, 1)
    return (x - mean) / std


def slot_of(path):
    m = re.search(r"_(\d+)_(\d+)\.[a-zA-Z]+$", path)
    if not m:
        return None
    v = (int(m.group(1)), int(m.group(2)))
    return VIEW_ORDER.index(v) if v in VIEW_ORDER else None


def main(paths):
    import timm

    sd = load_file("model.safetensors")
    fd = CFG["feat_dim"]
    backbone = timm.create_model(CFG["backbone"], pretrained=False, num_classes=0,
                                 in_chans=CFG.get("in_chans", 3))
    reduce_ = nn.Linear(fd * NV, 512)
    head = nn.Sequential(nn.Linear(512, 256), nn.ReLU(), nn.Dropout(0.0), nn.Linear(256, 3))
    for name, mod in [("backbone", backbone), ("reduce", reduce_), ("head", head)]:
        mod.load_state_dict({k[len(name) + 1:]: v for k, v in sd.items()
                             if k.startswith(name + ".")})
        mod.eval()

    rgb = torch.stack([preprocess(p) for p in paths])
    with torch.no_grad():
        x = torch.zeros(NV, CFG.get("in_chans", 3), H, W)
        keep = torch.zeros(NV)
        free = list(range(NV))
        for j, p in enumerate(paths):
            i = slot_of(p)
            if i is None:
                i = free[j]
                print(f"경고: {p} 의 촬영 각도를 알 수 없어 순서대로 배치했습니다", file=sys.stderr)
            x[i] = rgb[j]
            keep[i] = 1.0

        f = backbone(x).view(1, NV * fd) * keep.repeat_interleave(fd)[None]
        out = head(reduce_(f))

    tm = torch.tensor(CFG["tgt_mean"]); ts = torch.tensor(CFG["tgt_std"])
    l, w, h = (out[0] * ts + tm).tolist()
    print(f"length {l:.1f} cm - width {w:.1f} cm - height {h:.1f} cm")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
