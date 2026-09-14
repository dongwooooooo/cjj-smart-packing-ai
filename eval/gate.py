"""배포 전 정확도 게이트 — 후보 모델의 고정셋 평가 결과를 기준선과 비교해 승격 여부를 정한다.

사용:
  python gate.py --baseline fixtures/summary_fp32_fp16.json:fp32 --candidate results/summary.json:int8_static [--thresholds thresholds.json]
종료 코드 0 = 통과(승격 가능), 1 = 차단. 판정 근거를 JSON 으로 stdout 에 찍는다.

판정은 정확도만 본다. 지연은 기록만 한다 — "빨라졌지만 떨어진" 후보를 표에 남기기 위해서다.
기준은 두 겹이다.
  1) 비열화: 축별 MAE 악화 <= max_mae_regression_cm, 3축 ±3cm 정답률 하락 <= max_within3_drop
  2) 절대 상한: 축별 MAE <= abs_mae_cm (박스 추천 마진 3cm 에서 가져온 잠정값)
"""
import argparse, json, sys

DEFAULT_THRESHOLDS = {"max_mae_regression_cm": 0.2, "max_within3_drop": 0.02, "abs_mae_cm": 3.0}


def load(spec):
    path, _, variant = spec.partition(":")
    data = json.load(open(path))
    summary = data["summary"] if "summary" in data else data
    if variant:
        return summary[variant]
    if len(summary) != 1:
        raise SystemExit(f"{path}: variant 를 지정하라 (있는 것: {list(summary)})")
    return next(iter(summary.values()))


def judge(baseline, candidate, th):
    reasons = []
    for axis in ("length", "width", "height"):
        b, c = baseline["mae_cm"][axis], candidate["mae_cm"][axis]
        if c - b > th["max_mae_regression_cm"]:
            reasons.append(f"{axis} MAE {b:.2f}->{c:.2f} (+{c-b:.2f} > {th['max_mae_regression_cm']})")
        if c > th["abs_mae_cm"]:
            reasons.append(f"{axis} MAE {c:.2f} > 상한 {th['abs_mae_cm']}")
    drop = baseline["within_3cm_all"] - candidate["within_3cm_all"]
    if drop > th["max_within3_drop"]:
        reasons.append(f"3축 ±3cm {baseline['within_3cm_all']*100:.1f}%->{candidate['within_3cm_all']*100:.1f}% (-{drop*100:.1f}%p > {th['max_within3_drop']*100:.0f}%p)")
    return {"pass": not reasons, "reasons": reasons,
            "latency_ms": {"baseline_p50": baseline["latency_ms"]["p50"], "candidate_p50": candidate["latency_ms"]["p50"]},
            "speedup": round(baseline["latency_ms"]["p50"] / candidate["latency_ms"]["p50"], 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True); ap.add_argument("--candidate", required=True)
    ap.add_argument("--thresholds", default=None)
    a = ap.parse_args()
    th = dict(DEFAULT_THRESHOLDS)
    if a.thresholds:
        th.update(json.load(open(a.thresholds)))
    verdict = judge(load(a.baseline), load(a.candidate), th)
    print(json.dumps(verdict, ensure_ascii=False, indent=1))
    sys.exit(0 if verdict["pass"] else 1)


if __name__ == "__main__":
    main()
