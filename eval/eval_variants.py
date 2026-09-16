"""치수 추정 모델 변형(FP32 / FP16 / INT8 동적 / INT8 정적 PTQ) 정확도·지연 평가.

사용 (EC2, ~/eval):
  python eval_variants.py --vs ~/data/VS --items ~/data/index/items.csv --calib ~/data/TS_calib \
      --model-dir ~/inference --out ~/eval/results --threads 2 [--limit N]

- VS 품목 = 바코드 폴더마다 shot1 cam1/2/3 (`*_1_1.jpg`,`*_1_2.jpg`,`*_1_3.jpg`) 3장이 모두 있는 것.
- 정답 = items.csv (barcode → length/width/height, cm). 없으면 VL 라벨 json 은 쓰지 않는다(재현성: 8/26 평가와 동일 프로토콜).
- 정적 양자화 보정 데이터는 --calib 폴더(TS 일부)에서 뽑는다. VS 로 보정하지 않는다(누수 방지).
- 전처리는 inference/dimension.py 의 OnnxDimensionModel._batch 를 그대로 쓴다(서빙과 동일).
"""
import argparse, csv, glob, json, os, re, statistics, sys, time
import numpy as np
import onnx
import onnxruntime as ort


def pct(v, p):
    v = sorted(v); k = (len(v) - 1) * p / 100; f = int(k); c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def find_items(root):
    """바코드 폴더 → shot1 cam1..3 경로. 폴더명 {KAN}_{barcode}."""
    items = {}
    for d, _, files in os.walk(root):
        cams = {}
        for f in files:
            m = re.match(r"^(\d+)_(\w+)_1_([123])\.jpg$", f)
            if m:
                cams[int(m.group(3))] = os.path.join(d, f); barcode = m.group(2)
        if len(cams) == 3:
            items[barcode] = [cams[1], cams[2], cams[3]]
    return items


def load_gt(path):
    gt = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh)
        cols = {c.lower(): c for c in r.fieldnames}
        bc = next(c for k, c in cols.items() if k in ("barcode", "bar_code"))
        L = cols.get("length") or cols.get("gt_length"); W = cols.get("width") or cols.get("gt_width"); H = cols.get("height") or cols.get("gt_height")
        split = cols.get("split")
        for row in r:
            if split and row[split] not in ("VS", "val", "validation", ""):
                pass
            try:
                gt[str(row[bc])] = (float(row[L]), float(row[W]), float(row[H]))
            except (ValueError, TypeError):
                continue
    return gt


def build_variants(model_dir, out_dir, calib_batches, static_method="minmax", exclude_edges=False, reduce_range=False):
    from onnxruntime.quantization import quantize_dynamic, quantize_static, QuantType, QuantFormat, CalibrationDataReader
    from onnxruntime.quantization.shape_inference import quant_pre_process
    from onnxconverter_common import float16
    src = os.path.join(model_dir, "model.onnx")
    os.makedirs(out_dir, exist_ok=True)
    paths = {"fp32": src}
    p16 = os.path.join(out_dir, "model.fp16.onnx")
    if not os.path.exists(p16):
        onnx.save(float16.convert_float_to_float16(onnx.load(src), keep_io_types=True), p16)
    paths["fp16"] = p16
    pre = os.path.join(out_dir, "model.pre.onnx")
    if not os.path.exists(pre):
        quant_pre_process(src, pre)
    pdyn = os.path.join(out_dir, "model.int8dyn.onnx")
    if not os.path.exists(pdyn):
        quantize_dynamic(pre, pdyn, weight_type=QuantType.QInt8, per_channel=True)
    paths["int8_dynamic"] = pdyn
    pst = os.path.join(out_dir, "model.int8static.onnx")
    if not os.path.exists(pst) and calib_batches:
        class Reader(CalibrationDataReader):
            """보정 배치를 미리 만들지 않고 호출 때마다 한 건씩 전처리한다 (4GB 박스 OOM 방지)."""
            def __init__(self, loaders): self.it = iter(loaders)
            def get_next(self):
                f = next(self.it, None)
                if f is None: return None
                x, m = f()
                return {"images": x, "mask": m}
        from onnxruntime.quantization import CalibrationMethod
        method = {"minmax": CalibrationMethod.MinMax, "percentile": CalibrationMethod.Percentile, "entropy": CalibrationMethod.Entropy}[static_method]
        excl = []
        if exclude_edges:
            # 첫 conv(입력 분포가 가장 넓음)와 회귀 헤드(Gemm/MatMul)는 FP32 로 남긴다 — 양자화 민감 구간 표준 처방
            g = onnx.load(pre).graph
            convs = [n.name for n in g.node if n.op_type == "Conv"]
            heads = [n.name for n in g.node if n.op_type in ("Gemm", "MatMul")]
            excl = convs[:1] + heads
        print(f"static quant: method={static_method} exclude={len(excl)} nodes reduce_range={reduce_range}", flush=True)
        quantize_static(pre, pst, Reader(calib_batches), quant_format=QuantFormat.QDQ,
                        activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8, per_channel=True,
                        reduce_range=reduce_range, calibrate_method=method, nodes_to_exclude=excl)
    if os.path.exists(pst):
        paths["int8_static"] = pst
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", required=True); ap.add_argument("--items", required=True)
    ap.add_argument("--calib", default=None); ap.add_argument("--calib-n", type=int, default=200)
    ap.add_argument("--model-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=2); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--variants", default="fp32,fp16,int8_dynamic,int8_static")
    ap.add_argument("--static-method", default="minmax"); ap.add_argument("--exclude-edges", action="store_true"); ap.add_argument("--reduce-range", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, a.model_dir)
    from dimension import OnnxDimensionModel  # 전처리 재사용

    items = find_items(a.vs); gt = load_gt(a.items)
    keys = sorted(k for k in items if k in gt)
    if a.limit: keys = keys[: a.limit]
    print(f"VS items with 3 views: {len(items)}, with GT: {len(keys)}", flush=True)

    base = OnnxDimensionModel(a.model_dir, threads=a.threads)  # fp32 세션 + 전처리
    views = ["1-1", "1-2", "1-3"]

    def batch(paths):
        x, keep, _ = base._batch(paths, views)
        return x.astype(np.float32), keep.astype(np.float32)

    calib = []
    if a.calib:
        cal_items = find_items(a.calib)
        for k in sorted(cal_items)[: a.calib_n]:
            calib.append((lambda paths: (lambda: batch(paths)))(cal_items[k]))  # 지연 로딩
        print(f"calibration batches: {len(calib)} from {a.calib}", flush=True)

    # fp32 만 평가할 때(배포 게이트)는 변환 라이브러리 없이 서빙 모델을 그대로 쓴다.
    if a.variants.split(",") == ["fp32"]:
        os.makedirs(a.out, exist_ok=True)
        paths = {"fp32": os.path.join(a.model_dir, "model.onnx")}
    else:
        paths = build_variants(a.model_dir, a.out, calib, a.static_method, a.exclude_edges, a.reduce_range)
    tgt_mean = np.array(base.cfg["tgt_mean"]); tgt_std = np.array(base.cfg["tgt_std"])
    so = ort.SessionOptions(); so.intra_op_num_threads = a.threads
    summary = {}; per_item = {}
    for name in a.variants.split(","):
        if name not in paths: print("skip", name); continue
        sess = ort.InferenceSession(paths[name], so, providers=["CPUExecutionProvider"])
        errs = []; lat = []; rows = []
        for i, k in enumerate(keys):
            x, m = batch(items[k])
            t = time.perf_counter(); y = sess.run(None, {"images": x, "mask": m})[0][0]; lat.append((time.perf_counter() - t) * 1000)
            pred = y * tgt_std + tgt_mean  # target_space=norm → cm
            L, W, H = gt[k]; e = np.abs(pred - np.array([L, W, H]))
            errs.append(e); rows.append(dict(barcode=k, gt=[L, W, H], pred=[float(v) for v in pred]))
            if i % 200 == 0: print(f"  {name}: {i}/{len(keys)}", flush=True)
        E = np.array(errs); lat = lat[3:] if len(lat) > 10 else lat
        summary[name] = dict(
            n=len(keys), size_mb=round(os.path.getsize(paths[name]) / 1e6, 1),
            mae_cm=dict(length=float(E[:, 0].mean()), width=float(E[:, 1].mean()), height=float(E[:, 2].mean())),
            within_2cm_all=float((E <= 2).all(axis=1).mean()), within_3cm_all=float((E <= 3).all(axis=1).mean()),
            latency_ms=dict(p50=pct(lat, 50), p95=pct(lat, 95), max=max(lat)),
        )
        per_item[name] = rows
        s = summary[name]
        print(f"[{name}] size={s['size_mb']}MB MAE L/W/H={s['mae_cm']['length']:.2f}/{s['mae_cm']['width']:.2f}/{s['mae_cm']['height']:.2f} "
              f"±3cm(3축)={s['within_3cm_all']*100:.1f}% ±2cm={s['within_2cm_all']*100:.1f}% latency p50={s['latency_ms']['p50']:.0f} p95={s['latency_ms']['p95']:.0f}ms", flush=True)
    os.makedirs(a.out, exist_ok=True)
    json.dump(dict(summary=summary, threads=a.threads, n=len(keys)), open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    json.dump(per_item, open(os.path.join(a.out, "per_item.json"), "w"))
    print("saved", os.path.join(a.out, "summary.json"))


if __name__ == "__main__":
    main()
