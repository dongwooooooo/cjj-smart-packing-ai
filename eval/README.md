# eval — 고정셋 평가와 배포 전 정확도 게이트

- `eval_variants.py`: 검증셋(VS, 품목당 shot1 cam1~3)으로 FP32/FP16/INT8 변형의 MAE·±3cm·지연을 잰다. 전처리는 `inference/dimension.py` 를 그대로 쓴다.
- `gate.py`: 기준선(현행 모델) 대비 비열화 + 절대 상한으로 승격 여부를 정한다. 종료 코드로 파이프라인에 연결한다.
- `fixtures/`: 2026-09-15 EC2(c7i-flex.large, ORT 1.30, 2스레드) 실측 결과. `test_gate.py` 가 이 값으로 판정을 고정한다.

파이프라인 연결(평가 데이터 저장소 결정 뒤): 빌드 → 변환 대조 게이트 → `eval_variants.py` → `gate.py` 통과 시 `aws lambda update-alias --name live`.
