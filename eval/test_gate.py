"""gate.judge 단위 테스트 — 실제 EC2 평가 결과(fixtures)로 판정을 고정한다."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from gate import judge, DEFAULT_THRESHOLDS

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def summ(name, variant):
    return json.load(open(os.path.join(FX, name)))["summary"][variant]


def test_fp16_passes_with_no_gain():
    v = judge(summ("summary_fp32_fp16.json", "fp32"), summ("summary_fp32_fp16.json", "fp16"), DEFAULT_THRESHOLDS)
    assert v["pass"] and v["reasons"] == []
    assert 0.9 < v["speedup"] < 1.1


def test_int8_static_blocked_by_within3_and_abs_mae():
    v = judge(summ("summary_fp32_fp16.json", "fp32"), summ("summary_int8_static.json", "int8_static"), DEFAULT_THRESHOLDS)
    assert not v["pass"]
    assert any("±3cm" in r for r in v["reasons"])          # 62.5% -> 22.6%
    assert any("상한" in r for r in v["reasons"])           # length 3.57 > 3.0
    assert v["speedup"] > 1.4                               # 빨라졌지만 차단


def test_int8_dynamic_blocked():
    v = judge(summ("summary_fp32_fp16.json", "fp32"), summ("summary_int8_dynamic.json", "int8_dynamic"), DEFAULT_THRESHOLDS)
    assert not v["pass"] and len(v["reasons"]) >= 4


def test_small_regression_within_tolerance_passes():
    base = summ("summary_fp32_fp16.json", "fp32")
    cand = json.loads(json.dumps(base)); cand["mae_cm"]["length"] += 0.19; cand["within_3cm_all"] -= 0.019
    assert judge(base, cand, DEFAULT_THRESHOLDS)["pass"]


def test_regression_just_over_tolerance_blocks():
    base = summ("summary_fp32_fp16.json", "fp32")
    cand = json.loads(json.dumps(base)); cand["mae_cm"]["width"] += 0.21
    assert not judge(base, cand, DEFAULT_THRESHOLDS)["pass"]
