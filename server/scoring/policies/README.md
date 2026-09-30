# Detector Scoring Policies

탐지기별 점수 정책을 구현하는 폴더입니다.

## 담당

송희:
- noclip.py
- aimbot.py
- autopaint.py
- godmode.py

A 담당자:
- localguard.py
- whistle.py
- hide_anywhere.py
- esp.py

## 공통 규칙

1. 각 담당자는 자신의 탐지기 정책과 테스트를 작성합니다.
2. 입력은 Shared의 공통 7필드 이벤트를 기준으로 합니다.
3. 탐지기의 원시 점수를 임의로 변경하지 않습니다.
4. 서로 다른 탐지기의 점수를 임의로 합산하지 않습니다.
5. 공통 policy.py와 storage.py는 사전 협의 없이 수정하지 않습니다.
6. 실제 전송되는 module 이름을 확인하여 사용합니다.

## 현재 상태

기존 policy.py에 구현된 점수 분석 기능은 그대로 유지합니다.

탐지기별 정책 함수의 공통 인터페이스와 등록 방식은
별도 작업으로 구현합니다.

ESP 및 일부 탐지기의 점수 정책은 실제 코드 확인 후 확정합니다.
