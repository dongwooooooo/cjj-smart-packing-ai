# eval — 고정셋 평가와 배포 전 정확도 게이트

- `eval_variants.py`: 검증셋(VS, 품목당 shot1 cam1~3)으로 FP32/FP16/INT8 변형의 MAE·±3cm·지연을 잰다. 전처리는 `inference/dimension.py` 를 그대로 쓴다.
- `gate.py`: 기준선(현행 모델) 대비 비열화 + 절대 상한으로 승격 여부를 정한다. 종료 코드로 파이프라인에 연결한다.
- `fixtures/`: 2026-09-15 EC2(c7i-flex.large, ORT 1.30, 2스레드) 실측 결과. `test_gate.py` 가 이 값으로 판정을 고정한다.


## 파이프라인 연결 (2026-09-16)

`.github/workflows/build-deploy.yml` 의 `eval-gate` 잡이 이 하네스를 돌린다.

1. push 된 서빙 이미지에서 `/opt/model/model.onnx` 를 꺼낸다(변환을 다시 하지 않는다).
2. `s3://$EVAL_BUCKET/$EVAL_PREFIX` 의 고정셋(`VS/`, `index/items.csv`)을 받는다. 접두사는 저장소 변수 `EVAL_PREFIX` 로 정한다(운영값 `vs2024` = 검증셋 2,024품목. 기본값 `smoke` 는 시연 상품 11종, 파이프라인 동작 확인용).
3. `eval_variants.py --variants fp32` 로 후보를 평가하고 `gate.py` 로 `baselines/$EVAL_PREFIX/summary.json` 과 비교한다. 기준선이 없으면 후보를 기준선으로 등록하고 통과시킨다.
4. 통과하면 `deploy-lambda` 가 새 버전을 발행해 `live` 별칭을 옮기고, 후보 요약을 기준선으로 승격한다. 차단되면 별칭은 그대로다.

결과는 `results/$EVAL_PREFIX/{이미지 태그}/` 에 쌓이고 잡 요약에 판정 근거가 남는다.
