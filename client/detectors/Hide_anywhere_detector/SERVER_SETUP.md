# 서버 전송 연결

매 점수 샘플을 로컬 events.jsonl에 먼저 기록한 뒤, 팀 logger.py의
send_detection(event, event_id=...)에 넘긴다. 0점도 전송한다.
전송 오류는 raw 로그에 남기며 로컬 수집을 계속한다. 종료 시 flush_client와
shutdown_client를 각각 최대 5초 요청한다.

## 필요한 팀 라이브러리

첨부된 logger.py에는 상대 import가 있으므로 파일 하나만 복사하면 실행되지 않는다.
원래 패키지의 __init__.py, client.py, config.py, errors.py, storage.py 및 이들이
참조하는 나머지 파일을 함께 설치하거나 수집기 옆에 둬야 한다.

예: 수집기 옆에 detection_logger/ 패키지를 두고 그 안에 logger.py와 모든
동반 파일을 배치한다. 해당 패키지가 요구하는 의존성도 설치한다.

## 설정 파일

client_config.json은 팀 라이브러리의 ClientConfig 생성자 키와 값을 그대로 담는
JSON 객체다. 서버 URL, 인증, 재시도 등 설정 필드의 정확한 이름은 config.py에서
확인해야 한다. 현재 첨부에는 config.py가 없어 필드 이름이나 서버 프로토콜을
임의로 가정하지 않았다. 인증 값이 있는 설정 파일은 공유하지 않는다.

## 실행

```powershell
py -3.12 .\mecha_logger.py --pid 1234 --session-id hide_anywhere_005 --player-id player_042 --module hide_anywhere --play-label CHEAT --server-logger-module detection_logger.logger --server-config .\client_config.json
```

원래 패키지 이름이 다르면 --server-logger-module을 원래 import 경로로 바꾼다.
--server-config를 생략하면 기존처럼 로컬 수집만 한다. 초기 설정 실패 시 명확한
오류 기록과 종료 코드 1을 남긴다. --code는 수집을 오래 막을 수 있어 생략한다.

server_queued는 클라이언트 큐 접수 의미이며 서버 도착 보증이 아니다.
flush/shutdown의 complete=false 또는 enqueue_error가 있으면 로컬 events.jsonl을
보존하고 팀 라이브러리의 재시도/영속 큐 정책과 서버 수신 결과를 확인한다.
어댑터 자체는 자동 재전송하거나 전달 성공을 추정하지 않는다.

## 검증 범위

```powershell
py -3.12 -m unittest -v test_detector test_server_bridge
```

팀 API 호출, 이벤트 전달, 고유 ID, 오류 처리, 종료 처리는 모의 API로 검증한다.
실제 서버와 통신 검증은 전체 팀 패키지와 유효한 ClientConfig가 필요하다.
이번 변경은 기존 점수 계산 및 manifest 시간 작성 방식을 변경하지 않는다.
