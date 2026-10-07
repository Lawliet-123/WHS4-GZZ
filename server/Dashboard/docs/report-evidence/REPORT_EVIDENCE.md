# 보고용 캡처 근거

이 폴더의 이미지는 종합 Dashboard와 LocalGuard 연동 결과를 설명하기 위한 캡처다. 실제 구현 범위를 넘어선 탐지 성공으로 해석하지 않는다.

## dashboard-time-contract-demo.jpg

2026-10-07 Dashboard-A 시간 계약 반영 후 로컬 정적 DEMO에서 촬영했다. 운영 서버 데이터나 Light 실게임 세션의 검증 화면은 아니다.

- 이벤트 목록·상세에서 동일한 경과·관측·최초 수신 시각을 밀리초까지 표시한다. 세 시계와 서버 저장 순서는 서로 다른 정보다.
- unknown·충돌·기준시각 미제공을 추정으로 채우지 않는다. 이력은 기존 원본 상세 API로 확인하며 Scoring 파생 상태의 ID를 원본 시계로 재사용하지 않는다.
- 합성 Event에 고정된 가상 세션 시작 선언을 넣어 화면을 확인했다. 실제 저장 로그·환경 파일·인증 정보는 포함하지 않았다.
- 프론트 253개 테스트와 일반·공개 빌드, backend 80개 및 Shared 최초 수신 시각 테스트 4개를 통과했다. 운영 배포 SHA 및 지정된 Light 세션의 저장 자료 확인은 별도 검증 범위다.

## dashboard-feedback-deployed.jpg / dashboard-feedback-verdict-deployed.jpg

2026-10-07 공개 [kkinomalo.com/GZZ/](https://kkinomalo.com/GZZ/)에서 촬영한 피드백 반영 화면이다. 소스 `6fb1f15`의 프론트 단독 DEMO 빌드이며 실제 중앙 서버·게임 결과가 아니다.

- 이벤트 직접 상태와 플레이어의 현재 Final Verdict를 분리하고 T+·KST 수신 시각을 함께 표시한다. 서버가 주지 않은 관측 시각을 생성하지 않는다.
- 미제공 Severity 열을 숨기고 플레이어 점수·신뢰도 대신 독립 위험 근거·활성 모듈·중복 보정·평가 상태를 표시한다.
- 판정 탭에서 활성 모듈 이름·대표 이유·최신 raw·실제 서버 임계값·세션 보존 사건을 나눠 표시한다. `GodMode 이력`의 범위를 명시한다.
- 공개 화면에서 Launcher 일부 저하 → 점검 대상 → 해당 세션/플레이어 실행 상태로 이동했다. 판정 탭의 근거 표시도 확인했으며 해당 점검의 브라우저 warning/error는 없었다.
- Vercel `gzz-dashboard-frontend`만 Production으로 갱신했다. 공개 HTML·JS·CSS가 200이며 로컬 빌드와 SHA-256이 동일하다. 기존 홈페이지는 200·기존 제목을 유지한다.
- 공개 HTML meta CSP는 `connect-src 'none'`이다. 실제 로그·DB·환경 파일은 업로드하지 않았다. `/GZZ/` 응답의 HTTP CSP 헤더 적용은 확인되지 않았으므로 meta CSP와 구분한다.

이번 배포는 사람들이 프론트 표시를 확인하고 피드백할 수 있게 하는 목적이다. 운영 backend 배포·새로운 실게임 E2E 완료로 해석하지 않는다.

## dashboard-overview.png

페이지 분리 전 React Dashboard의 데모 화면이다. 이전 레이아웃 기록으로 보존하며 현재 디자인은 SOC JPEG 캡처를 기준으로 확인한다.

## dashboard-pages-overview.jpg / dashboard-events-pages.jpg

2026-10-05 로컬 React 개발 서버에서 촬영한 페이지 분리 후 화면이다. 데이터는 계약 기반 합성 데이터이며 실제 게임 탐지 결과나 운영 서버 연결 성공을 의미하지 않는다. 화면 상단 `DEMO`로 구분한다.

- 종합 현황: 연결 상태, 조회 범위 요약, 검토 대상과 최근 이벤트
- 이벤트: 세션·플레이어·모듈 등 필터, 수신 순서 목록과 상세 패널
- 세션·플레이어·보호 모듈·시스템은 별도 주소의 페이지로 분리
- 플레이어 상세는 판정·타임라인·모듈 신호·실행 상태·사건 이력 탭으로 분리

이번 변경의 검증은 아래 명령으로 수행했다.

```powershell
cd "C:\Users\nojiw\Downloads\WHS4-GZZ-dashboard-react\server\Dashboard"
npm test -- --run
npm run build
```

7개 테스트 파일, 87개 테스트와 빌드가 통과했다. 실제 브라우저에서는 메뉴 이동, 세션→플레이어, 모듈→이벤트, 상세 패널, 직접 주소 새로고침을 확인했다. 390px·900px 폭에서는 필터·긴 ID·탭·모바일 메뉴를 점검했고 임시 화면 크기는 기본값으로 복원했다.

API 취소·지연 응답 방지, 실패 시 마지막 성공 자료 유지, null 점수 처리, 개인정보 숨김에 대한 기존 테스트도 유지했다. 운영 서버·실게임 종단 검증을 이번 디자인 검증에 포함한 것으로 보고하지 않는다.

## dashboard-soc-overview.jpg / dashboard-soc-investigation.jpg

2026-10-06 로컬 React 개발 서버의 dark SOC 화면이다. 데이터는 위와 같은 계약 기반 합성 데이터로 `DEMO`이며 운영 서버나 실제 치트 탐지 검증이 아니다.

- CrowdStrike Falcon / SentinelOne 공식 관제·조사 흐름과 제공된 6쪽 와이어프레임을 참고했다.
- 180px 사이드바, 작은 4개 지표, 중앙 8열 탐지 표, 중립 상태 배지와 2px 모서리를 적용했다.
- 별도 6페이지를 유지하고 개요·이벤트에서 같은 탐지 표를 사용한다.
- 수신 순서·원점수·명시적 severity 정렬과 severity 필터, 데이터 없음 표시를 검증했다.
- 행 선택은 오른쪽 조사 패널로 이어지며 이유·현재 대상 판정·평가 완료 여부·근거·원본을 확인한다.
- severity는 원점수에서 추정하지 않았다. 현재 계약·데모에서 제공되지 않으므로 미제공으로 표시한다.
- 연결 클라이언트는 반환된 healthy/online Launcher의 고유 client_id이며 전역 접속자 수가 아니다.

디자인 단계에서 8개 파일의 테스트 115개와 production build가 통과했다. 기본 데스크톱, 900px, 390px 폭에서 페이지 이동·표 내부 스크롤·모바일 메뉴·조사 패널·긴 ID 넘침을 확인하고 크기 설정을 복원했다. API 장애·관측 부족·null·개인정보 처리 회귀 테스트는 그대로 유지한다. 이 디자인 검증은 실게임 E2E를 포함하지 않는다.

## dashboard-gzz-deployed.jpg

2026-10-06 `https://kkinomalo.com/GZZ/`에서 촬영한 프론트 단독 공개 화면이다. `DEMO` 표시가 있는 합성 데이터이며 실제 중앙 서버 상태나 게임 탐지 결과가 아니다.

- Vercel에는 빌드된 HTML·JS·CSS와 정적 배포 설정만 전송했다.
- 기존 사이트는 재배포하지 않고 `/GZZ` 및 하위 경로만 별도 프론트로 연결했다.
- 공개 모드에서 서버 연결·토큰 입력을 숨겼다. 새로고침·상세·플레이어 이동도 서버 요청 없이 동작한다.
- 배포 설정에는 CSP의 `connect-src 'none'`이 포함되어 있다. 이후 차트 배포 점검에서 정적 파일 직접 전송 시 HTTP 헤더가 적용되지 않는 점을 확인해 공개 빌드에 HTML meta CSP를 추가했다.
- 프론트 단독 모드 회귀 테스트를 추가해 8개 파일 117개 테스트와 일반 빌드·`build:gzz`가 통과했다.
- 실제 도메인에서 6페이지 이동, 이벤트 상세 → 플레이어 판정 이동, 주소 새로고침을 확인했다. 브라우저 콘솔 오류는 없었다.
- 표의 접근성용 숨김 제목이 페이지 전체 가로 스크롤을 만들던 문제를 수정했다. 표 내부 스크롤은 유지한다.
- `/GZZ`, `/GZZ/`, JS·CSS의 200 응답과 올바른 MIME을 확인하고, 기존 홈페이지의 200 응답과 `스쿨캠핑 | 10월 신청` 제목을 확인했다.

이 배포는 프론트 시연용이며 백엔드 연동 완료·실게임 탐지 검증·Git push를 의미하지 않는다.

## dashboard-analytics-deployed.jpg / dashboard-session-flow-deployed.jpg

2026-10-06 같은 공개 도메인에서 그래프와 상태 색상을 추가한 화면이다. 합성 데이터의 DEMO이며 기존 실제 테스트 로그를 공개하지 않는다.

- 대상 판정 분포: 불러온 세션·플레이어 쌍별 SUSPICIOUS·INCONCLUSIVE·NO_ACTIVE_EVIDENCE·UNKNOWN 집계와 판정별 목록 이동
- 탐지기별 관측: 0점 포함, 운영 이벤트 제외, Event ID 중복 제거, DLL 하위 모듈과 외부 접근 분리, 탐지기별 목록 이동
- 세션 관측 흐름: 한 세션의 고정 서버 수신 순번 구간별 건수와 0점 이하·양수 관측 구분. 시간 추세나 확정된 핵 사용 건수가 아니다.
- 서버 판정의 의심은 빨강, 보류는 노랑으로 강조한다. 원점수로 심각도를 추정하지 않는다.
- 10개 파일 149개 테스트, 일반 production·공개 frontend 빌드 통과
- 기본 데스크톱·900px·390px에서 문서 전체 가로 넘침 없음, 모바일 메뉴와 차트 목록 이동 확인
- 기존 홈페이지와 새 HTML·JS·CSS의 200 응답 확인. 배포는 별도 GZZ 프론트 프로젝트만 대상으로 한다.

시각화는 기존 API 응답의 로드된 범위로 계산하며 추가 그래프 API나 비교 가능한 모듈 점수를 새로 가정하지 않는다. 공개 화면의 네트워크 차단은 frontend 빌드의 HTML meta CSP로 적용한다. 일반 LIVE 빌드는 그대로 유지한다.

## dashboard-live-synthetic.png / dashboard-live-investigation.png / dashboard-live-outage.png

2026-10-06 최신 main `31dc833`의 실제 `server.main`과 React 개발 화면을 loopback HTTP로 연결한 캡처다. 화면의 LIVE는 **실제 API 연결 방식**을 뜻한다. 모든 Event와 heartbeat는 `synthetic=true`인 합성 입력이며 실제 게임·치트·운영 로그를 사용하지 않았다.

- 초기 6개 Event와 heartbeat-only 대상을 포함한 4개 판정: SUSPICIOUS / INCONCLUSIVE / NO_ACTIVE_EVIDENCE / UNKNOWN.
- 상세 ID·sequence와 원본 Noclip 근거, 평가 완료·근거 2개·활성 모듈 2개·reason code·null 점수 및 신뢰도.
- 플레이어별 별도 snapshot, status, GodMode 사건 이력 3건과 Launcher running 상태.
- 추가 Event 1건이 5초 polling으로 6→7건에 반영되고 NO_ACTIVE_EVIDENCE 대상이 SUSPICIOUS로 바뀜. 이후 polling에서 7건 유지.
- heartbeat-only 대상의 판정 데이터 없음·UNKNOWN·미제공 표시 및 빈 이벤트 목록.
- 잘못된 테스트 인증 오류, 임시 서버 종료 후 LIVE 지연·오류 표시와 마지막 7건 유지. 자동 DEMO 전환 없음.

시험은 `server.dashboard_backend.browser_smoke serve`로 생성한 임시 저장소에서 수행했다. 두 차례의 bounded fixture는 자연 종료 후 소유 서버와 임시 저장소를 정리했다. 중앙 운영 토큰·실제 데이터는 캡처와 저장소에 포함하지 않는다. 재현 방법은 [README](../../README.md)의 안전한 화면 시험 절을 참고한다.

최종 회귀: 프론트 166개, Dashboard backend 47개, Receiver 30개, Scoring 385개, Shared 48개가 통과했다. 일반 빌드와 공개 frontend 빌드도 통과했다. 기존 HTTP smoke의 합성 7건 검증을 별도로 유지한다. 공개 `/GZZ/`는 여전히 API 요청이 차단된 DEMO이며 이 캡처의 LIVE 서버와 별개다.

Noclip `blocked_path=1`이 파일 경로 필터에 잘못 걸리는 오류를 이 시험에서 확인했다. Noclip의 최상위 숫자 0/1·boolean만 남기도록 수정했고 문자열 경로·다른 민감 키의 제거와 JSON 복사 회귀를 검증했다.

## dashboard-native-dll-e2e.png / dashboard-native-dll-evidence.png

2026-10-06 main `2ac2bed` 기준, 테스트가 직접 만든 Windows Python 보조 프로세스에서 실제 `winhttp.dll`을 로드한 결과다. 합성 DLL 추가 Event를 직접 넣은 화면이 아니라 실제 LocalGuard 센서·detector·durable Shared sender → 실제 `server.main` Receiver·Scoring → React LIVE 연결을 사용했다. 게임이나 핵, Launcher를 실행한 시험은 아니다.

- 정상 기준선 0점 → 실제 DLL 추가 1점 → 변화 없는 반복 스캔 0점, 총 3건.
- 로컬 JSONL·Shared HTTP ACK·Receiver 목록/상세·B 이력이 동일함을 확인했다.
- 같은 DLL 추가는 1회만 기록됐고 이후 polling에서도 3건으로 유지됐다.
- 상세에서 DLL 이름·추가 변화·정상 서명·해시가 표시되고 전체 경로 1개는 숨겨졌다.
- 1점은 현재 사건 threshold 2 미만이며 외부 프로세스 접근은 관측하지 않았다. 따라서 INCONCLUSIVE·평가 미완료·근거 0개·점수/신뢰도 미제공이 올바른 결과다.
- Launcher 자료를 조작하지 않았으며 상태 확인 불가·클라이언트 미제공으로 표시됐다. 서버 자연 종료 후 마지막 LIVE 자료와 지연·조회 오류가 유지됐고 자동 DEMO 전환은 없었다.

재현은 [README](../../README.md)의 실제 Windows 센서 E2E 절을 참고한다. 저장한 캡처에는 운영 인증값·실제 사용자 로그가 없으며 Event ID·PID는 이번 시험이 만든 보조 프로세스의 값이다. 시험 후 소유 서버와 임시 저장소가 정리됐다.

기존 Jiwan 센서 E2E는 6개, 신규 서버 fixture·안전성 테스트는 7개 모두 통과했다. Windows 전용 테스트도 실제로 실행됐다. 별도 합성 unsigned DLL 회귀는 뒤 NORMAL 0점에도 임계 충족 이력과 SUSPICIOUS·근거 1개가 유지되는지 검증했다. 실게임 핵 ON/OFF와 추가 CHEAT Replay 수집은 이 결과에 포함하지 않는다.

## localguard-module-stable.png

실제 게임에서 `module_integrity`가 136개 DLL을 반복 관찰하는 동안 추가·변경·전송 Event가 0건으로 유지된 정상 관찰 화면이다. 첫 성공 스냅샷 이후 기준선이 안정적으로 유지되는지 확인한 자료이며, 전체 정상 플레이 판정을 대신하지 않는다.

## localguard-handle-detected.png

LocalGuard 외부 접근 통합 시험에서 관찰 대상이 4개에서 5개로 늘고 `emitted=1`이 된 화면이다. 이 캡처는 `external_process` 채널의 핸들 접근 감지 결과이며 `module_integrity`의 DLL 추가 탐지와는 구분한다.

`module_integrity` 자체의 양성 경로는 다음 명령으로 다시 검증했다.

```powershell
py -3 -m client.LocalGuard.external_access.module_integrity.smoke_test
```

검증 결과는 정상 서명된 `winhttp.dll`의 동적 로드를 `change_type=added`, `signature_status=trusted`, `raw_score=1`로 한 번 기록했다. 게임 파일이나 게임 프로세스는 수정하지 않는다.
