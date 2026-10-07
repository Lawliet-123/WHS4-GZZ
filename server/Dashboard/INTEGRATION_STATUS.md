# Dashboard 통합 상태

이 문서는 8-A 화면이 종합 안티치트 관제 역할을 하기 위해 현재 연결할 수 있는 범위와, 8-B·Receiver·Scoring·Launcher 쪽에서 추가로 확정해야 하는 범위를 구분한다. 화면에 보인다는 이유만으로 아직 없는 서버 기능을 구현된 것처럼 표시하지 않는다.

## 2026-10-07 Dashboard 피드백 반영

기준 main `76c03dd`에서 작업했다. 기존 LocalGuard·ESP 변경은 유지하고 Dashboard 표시와 필요한 읽기용 메타데이터만 보강했다.

- 시간: 확인된 세션 기준에 T+를 표시하고 신규 최초 저장 UTC 시각을 별도 KST 수신 시각으로 연결했다. unknown 시간은 원본 ms로 남기며 과거 실제 시각을 만들지 않았다. 실제 관측 시각의 완전한 연결은 생산자 시간 계약 협의가 남아 있다.
- 이력 범위: 플레이어 상세의 일반적인 사건 이력 명칭을 `GodMode 이력`으로 변경했다.
- 이벤트 상태: 플레이어의 SUSPICIOUS를 raw 0 Event에 복사하던 표시를 제거했다. Event 직접 보고 상태와 현재 플레이어 판정을 분리했다.
- 판정 근거: 활성 모듈 이름·대표 reasons·최신 raw·실제 서버 임계값·보존 사건을 추가했다. DLL `[0, 2, 0]`은 최신 0과 보존 2를 나눠 표시하고, 일반 모듈의 과거 고점은 현재 활성으로 승격하지 않는다.
- 미사용 필드: 모두 미제공인 Severity 열·필터·정렬은 숨긴다. 플레이어 점수·신뢰도 대신 Final Verdict의 독립 위험 근거·활성 모듈·중복 보정·평가 상태를 표시한다.
- Launcher: 연결 요약에서 점검 대상 목록으로 이동하고 실패/WARN/SKIPPED·수신 지연·미보고·전송 장애를 세션/플레이어/client 기준으로 확인한다. 해당 대상의 실행 상태 상세로 이동할 수 있다.
- 모듈 신호: 최신 raw 높은 순 / 불러온 이력 최고 raw 높은 순 정렬과 원본 상세 연결을 추가했다.

검증: 프론트 단위·jsdom 상호작용 235개, Shared 52개, Receiver 30개, Scoring 403개, Dashboard backend 76개 통과. 일반 빌드와 `/GZZ/` 프론트 단독 빌드도 통과했다. backend HTTP 회귀에는 이벤트 목록·상세의 동일 시각, 중복 전송의 최초 시각 유지, Shared 7필드 보존을 포함한다.

자동화 회귀 후 추가 요청으로 아래 프론트 단독 공개 배포를 진행했다. 실제 게임·운영 backend 시험을 새로 진행한 것은 아니다. 과거 실게임 결과와 이번 자동화 회귀·DEMO 화면 확인을 구분한다. Scoring의 snapshot·이력·판정 조회는 원자적 watermark 계약이 아니므로 수신 중 시점 차이는 남아 있다. 정책 설명만으로 최종 판정을 대체하지 않는다.

### 프론트 단독 공개 배포 확인

- 공개 주소: [kkinomalo.com/GZZ/](https://kkinomalo.com/GZZ/). 피드백 반영 소스 `6fb1f15`의 정적 빌드를 기존 Vercel `gzz-dashboard-frontend`에 Production 배포했다.
- 업로드 범위는 `GZZ/index.html`, 해시 JS·CSS, 정적 배포 설정이다. 소스 전체·실제 로그·DB·인증 환경 파일은 올리지 않았다. 기존 홈페이지 프로젝트와 `/GZZ` 연결 규칙은 변경하지 않았다.
- 공개 주소의 HTML·JS·CSS가 모두 200이며 빌드 원본과 SHA-256이 일치한다. JS는 `index-CNePWaIw.js`, CSS는 `index-CwAw_pIf.css`다.
- 공개 브라우저에서 T+·KST 수신 시각, raw 0 이벤트의 직접 상태, 미제공 Severity 열 숨김, 판정 근거·독립 위험 근거·활성 모듈·중복 보정·평가 상태, `GodMode 이력` 명칭을 확인했다. Launcher 일부 저하 → 문제 세션/플레이어 → 해당 실행 상태 상세 이동도 확인했다. 점검 중 브라우저 warning/error는 없었다.
- 공개 화면은 계속 `DEMO`이며 HTML meta CSP의 `connect-src 'none'`으로 API 통신을 막는다. `/GZZ/` HTML 응답의 HTTP 보안 헤더는 CDN 경유 시 보이지 않으므로 헤더 적용을 검증됐다고 주장하지 않는다. 일반 LIVE 빌드·Receiver·Scoring·Launcher는 배포하지 않았다.
- 기존 홈페이지는 200과 `스쿨캠핑 | 신청` 제목을 유지한다. 화면 캡처는 [보고 근거](./docs/report-evidence/REPORT_EVIDENCE.md)에 추가했다.

## 2026-10-06 실제 게임 검증 추가 결과

승인된 비공개 방에서 이미 실행 중인 게임을 대상으로 확인했다. 이번에는 웹 화면 테스트를 하지 않았으며 아래 API 검증과 과거 React 검증을 하나의 실게임 화면 시험으로 묶지 않는다.

- 실제 Launcher로 DLL 무결성·외부 프로세스 접근·ESP·Hide Anywhere 수집기를 180초 실행했다. 네 수집기가 함께 RUNNING인 스냅샷 275개를 확인했고 모두 요청 후 STOPPED·종료 코드 0으로 끝났다. 강제 종료와 남은 자식 프로세스가 없으며 게임 인스턴스는 유지됐다. 프로세스가 실행됐다는 사실과 센서가 유효한 측정을 했다는 사실은 구분한다.
- 빈 임시 저장소의 실제 `server.main`에 Shared 전송을 연결했다. 합성 이벤트를 넣지 않았으며 수신한 506건의 목록·상세가 모두 일치했다. overview·snapshot·Final Verdict·발신자별 status·heartbeat 조회가 성공했고 snapshot과 별도 Verdict의 판정도 일치했다.
- 이 통합 실행의 최종 판정은 두 대상 모두 INCONCLUSIVE·평가 미완료였다. ESP 정책 미확정과 Hide 측정 불가를 정상으로 바꾸지 않았고 null 점수·신뢰도를 0으로 바꾸지 않았다. 506건에는 정상 상태와 ERROR 이벤트도 포함되므로 모두 핵 탐지라고 보고하지 않는다.
- 최신 ESP 수집기로 실제 NORMAL 1세션과 CHEAT 2세션을 추가했다. 공통 7필드·실제 점수·시각을 보존하고 개인정보 필터를 통과한 manifest와 events만 올린다. 원본 로그·로컬 DB·실패 기록은 업로드하지 않는다.

| Replay 세션 | 라벨 | Event | ON | OFF |
| --- | --- | ---: | ---: | ---: |
| `normal_realgame_20261006_capture02` | NORMAL | 63 | 없음 | 없음 |
| `esp_realgame_20261006_capture01` | CHEAT / ESP | 82 | 5706ms | 80409ms |
| `esp_realgame_20261006_capture02` | CHEAT / ESP | 67 | 5189ms | 77984ms |

ESP ON은 실제 게임 핸들을 연 시각이고 OFF는 해당 프로세스 종료 관측이다. 카메라·로컬 캐릭터의 읽기 준비 완료는 19208ms / 17655ms이며 이후 최소 60초간 새 읽기 성공을 확인했다. 해당 ESP에 직접 연결되는 탐지 Event는 12건 / 11건이다. 상대 플레이어 박스·스켈레톤 표시까지 확인한 시험은 아니다. NORMAL의 오탐 점수와 CHEAT의 다른 프로세스 관측도 그대로 남겼다. 앞서 보관 NORMAL 1세션을 추가한 것까지 합쳐 요청받은 추가 CHEAT 2개·NORMAL 2개 수는 충족했으며, 유효 ESP 자료는 총 CHEAT 3개·NORMAL 3개다. threshold는 ReplayAnalyzer 담당자의 재분석 후 확정해야 한다.

실행 중 발견한 준비·검증 도구 오류도 수정했다. 격리 환경에 Windows 사용자 프로필 환경 변수가 빠져 초기화가 실패하던 문제, 생성 시각 FILETIME 변환의 정수/실수 차이 때문에 같은 게임을 다른 대상으로 판단하던 문제, 캡처 준비 세션과 writer의 ID가 다르던 문제, 작업 객체 생성 실패 후 자식 프로세스 정리 경로를 고쳤다. 첫 실패 기록을 성공으로 바꾸지 않았으며 성공한 새 캡처만 Replay로 제출했다.

최신 테스트: ESP 단위·통합 202개, Launcher 검증 도구·임시 서버·Replay exporter 35개, DLL 무결성 37개, 새 Replay 계약 2개 통과. 이 수는 실게임 센서 종류나 탐지 성공 횟수가 아니다.

**전체 실게임 E2E는 아직 미완료다.** Hide는 현재 게임 빌드와 NamePool 바인딩이 맞지 않아 ERROR·measurement_valid=false였고 정상 측정으로 인정하지 않았다. UE4SS/관측 후크 DLL 예외와 등록되지 않은 읽기 프로세스 대조 시험은 이번 실행에 포함하지 않았다. Launcher 실행의 최초 Sysmon 조회 1회가 512건 제한으로 잘렸으므로 해당 통합 실행의 엄격한 전체 coverage도 INCOMPLETE로 보존했다. 이후 독립 Replay는 별도 준비 구간을 보존하고 측정 구간의 모든 필수 센서가 충분한 경우에만 내보냈다.

정상 Windows 프로세스의 VM_READ와 오버레이가 높은 점수로 남는 사례도 확인했다. 일부 카탈로그 서명 파일은 현재 파일 단위 WinVerifyTrust에서 unsigned로 해석되므로 서명·허용 정책 보강이 필요하다. 정상 자료를 핵으로 재분류하거나 OS PID 전체를 임의로 제외하지 않았다.

테스트용 Sysmon 기능은 원래 Disabled 상태로 복원했다. 이벤트 로그는 지우지 않았고 재부팅·게임 종료·게임 파일 수정·DLL 주입은 하지 않았다. 소유 수집기·임시 서버도 종료했다. 공개 `/GZZ/`는 여전히 프론트 단독 DEMO이며 운영 서버 연결과 이번 웹 UI 시험 완료를 의미하지 않는다. 평문 보고는 [실게임 검증 요약](./docs/report-evidence/REALGAME_VALIDATION_20261006.txt)에 남겼다.

## 2026-10-06 필수 체크리스트 기준

팀 Notion의 `진행도 → 필수 사항`에 적힌 기본 요건을 기준으로 노지완 항목을 확인했다. 노지완 행만 4/4로 체크하고, 본인 진행 요약에 아래 완료 범위와 별도 남은 작업을 함께 기록했다.

- [x] Logger 연동: 실제 `shared.logger.send_detection`과 HTTP ACK, 재시도·중복 억제 검증.
- [x] Detector 업로드: ESP와 DLL 무결성 코드가 팀 main에 반영됨.
- [x] Replay 세션 추가: 기존 정상 캡처 `normal_001`의 132개 Event 내보내기에 더해 위 실제 NORMAL 1개·CHEAT 2개를 새로 수집함. 유효 ESP 자료는 CHEAT 3개·NORMAL 3개이며 `esp_only_001`은 제외함. 추가 자료는 2026-10-06 팀 main에 반영됨.
- [x] E2E: 통제된 Windows 보조 프로세스에서 실제 DLL·VM_READ 핸들을 관측하고 Shared → Receiver → Scoring → 조회 API를 확인함. 실제 `server.main` → React LIVE 연결도 확인함. 보강 코드는 [팀 main에 반영된 변경](https://github.com/Lawliet-123/WHS4-GZZ/pull/126)에서 확인할 수 있음.

**4/4는 위 기본 요건의 완료이지 실게임·운영 검증 전체 완료가 아니다.** 다음 항목은 계속 미완료로 남긴다.

- [x] 최신 수집기로 추가 CHEAT 2개·NORMAL 1개를 독립된 캡처 세션에서 수집하고 ON/OFF 기록하기. 같은 실행 중인 게임에서 수집한 자료이지 서로 다른 게임 재실행 3회라는 뜻은 아님.
- [ ] 실제 Launcher·Sysmon·게임 종단 시험의 남은 범위: Hide 빌드 바인딩, 등록/미등록 읽기 프로세스 대조, UE4SS·후크 DLL 예외를 포함해 재확인하기. 실제 실행·정상 종료·API 경로는 위에서 확인함.
- [ ] 운영 인증을 확정하고 공개 `/GZZ/`와 실제 운영 API를 연결·검증하기.

추가한 `normal_001`은 보관 캡처의 내보내기이며 최신 수정의 실게임 탐지율 증명이 아니다. 합성 E2E 결과는 CHEAT Replay로 제출하지 않는다.

## 2026-10-06 Windows 센서부터 화면까지 E2E 재확인

GitHub 최신 main `2ac2bed`에는 기존 LocalGuard·ESP E2E와 React 프론트가 모두 머지되어 있었다. 이를 받은 뒤 다시 실행했고, 테스트용 앱만 쓰던 검증에 실제 `server.main`과 React 화면 확인을 추가했다.

- 기존 Jiwan E2E에 회귀 1개를 추가해 총 6개 통과. 실제 Windows DLL 로드·VM_READ 핸들 항목도 실행됐으며 skip은 없었다. Shared HTTP ACK, Receiver 저장, Scoring, Dashboard 조회가 이어지는지 확인했다.
- 같은 핸들이 유지되는 동안 이벤트가 반복되지 않고, 닫은 뒤 다시 열면 새 이벤트가 생성됐다. 전송 오류 후 재시도와 sender 재시작에서도 같은 Event ID로 중앙 기록이 한 번만 남았다.
- 합성 unsigned DLL 관측은 실제 detector가 `raw_score=2`로 계산했다. 이후 같은 `module_integrity`의 NORMAL 0점이 와도 이력 `[0, 2, 0]`, 최종 SUSPICIOUS, 근거 1개가 유지됐다. 합성 입력이며 실제 핵 DLL을 사용한 테스트는 아니다.
- 새 `server.dashboard_backend.module_integrity_e2e`는 정상 시스템 DLL을 실제로 로드해 운영 진입점 `server.main`의 loopback HTTP에 전송한다. 이번 실행은 `winhttp.dll` 추가 1건과 NORMAL 2건으로, 로컬 JSONL·Shared ACK·Receiver·B 이력이 모두 3건으로 일치했다. 새 fixture·안전성 테스트 7개도 skip 없이 통과했다.
- 일반 React LIVE에서 실제 DLL 이벤트 목록·상세·별도 snapshot·타임라인을 확인했다. `change_type=added`, 정상 서명, 원점수 1과 파일 해시가 표시됐고 경로 필드는 숨겨졌다. 반복 polling에서도 목록은 3건으로 유지됐다.
- 서명된 DLL의 점수 1은 사건 threshold 2 미만이다. DLL만 검사하고 `external_process`는 관측하지 않은 시험이므로 최종 INCONCLUSIVE·평가 미완료·근거 0개·null 점수/신뢰도 미제공이 맞았다. Launcher를 실행하거나 가짜 heartbeat를 넣지 않았고, 연결 상태는 확인 불가·클라이언트 미제공으로 구분했다.
- 임시 서버 자연 종료 후에는 마지막 LIVE 자료를 유지하고 지연·조회 오류를 표시했다. DEMO로 자동 전환하지 않았다. 소유 서버의 listener 종료와 임시 저장소 정리도 테스트했다.

최신 회귀 결과: 프론트 166개 및 일반 build, Dashboard backend 54개, Scoring 402개, Receiver 30개, Shared 48개, LocalGuard module integrity 37개 통과. Jiwan 6개는 Scoring 402개에, fixture 7개는 Dashboard 54개에 포함된 수다. ESP 182개도 기본 외부 수집 경계를 빈 fixture로 격리한 단위 테스트에서 통과했으며, 해당 클래스의 명시적 fixture 검증은 유지했다. 이는 실센서 182종 검증이라는 뜻이 아니다.

**완료 범위는 통제된 Windows 보조 프로세스와 실제 로컬 HTTP·React 화면이다.** 실게임 핵 ON/OFF, Sysmon, 실제 Launcher 전체 실행·등록 PID/생성 시각 종단 연동, 운영 배포 인증, 추가 CHEAT Replay 수집은 아직 완료로 표시하지 않는다. 공개 `/GZZ/`도 계속 프론트 단독 DEMO다. 캡처와 명령은 [보고 근거](./docs/report-evidence/REPORT_EVIDENCE.md)와 [README](./README.md)에 남겼다.

## 2026-10-06 실제 HTTP·브라우저 확인

최신 main `31dc833`을 기준으로 backend v2와 이후 Launcher 수정을 유지한 상태에서 확인했다. 전달받은 이전 v2 ZIP으로 현재 서버·Scoring을 덮어쓰지 않았다.

- 프론트: 166개 테스트, 일반 build와 공개용 `build:gzz` 통과.
- 서버 회귀: Dashboard backend 47, Receiver 30, Scoring 385, Shared 48, 총 510개 테스트 통과.
- `server.dashboard_backend.smoke`: 실제 `server.main`을 임시 저장소·loopback HTTP로 실행해 합성 7건의 저장·Scoring·기존 C API·신규 조회·Launcher 상태 확인.
- 실제 React LIVE 화면: overview, 별도 이벤트 목록·상세, snapshot, GodMode 이력, heartbeat status 조회 확인. 합성 입력으로 서버가 반환한 SUSPICIOUS / INCONCLUSIVE / NO_ACTIVE_EVIDENCE / UNKNOWN을 확인했다.
- `assessment_complete`, 근거 2개·활성 모듈 2개, reason codes와 null 점수·신뢰도의 미제공 표시 확인. heartbeat-only 대상은 판정 데이터 없음·UNKNOWN이며 정상으로 변환하지 않았다.
- 새 Event를 Receiver에 추가한 뒤 새로고침 버튼 없이 5초 polling으로 목록 6→7건과 판정 NO_ACTIVE_EVIDENCE→SUSPICIOUS 반영 확인. 이후 polling에도 중복 증가하지 않았다.
- 잘못된 테스트 인증은 오류로 표시하고, 이미 연결된 상태의 인증 실패에서는 마지막 LIVE 자료와 지연 표시를 유지했다. 빈 이벤트 목록을 장애 메시지로 표시하지 않았다. 임시 서버가 자연 종료된 뒤에도 마지막 7건을 유지하고 LIVE 지연·갱신 실패·API 오류를 표시했으며 DEMO로 자동 전환하지 않았다.
- Noclip의 숫자 `blocked_path`가 경로 개인정보 필터에 걸려 사라지는 오류 수정. Noclip의 최상위 0/1·boolean 근거만 보존하며 문자열 경로와 다른 민감 키는 계속 숨긴다.

이 기록은 **합성 입력을 실제 서버·브라우저로 연결한 검증**이다. 실제 게임·치트·운영 서버를 사용한 종단 시험은 아니며 공개 `/GZZ/`의 운영 API 연결을 의미하지 않는다. 캡처와 재현 방법은 [보고 근거](./docs/report-evidence/REPORT_EVIDENCE.md)를 참고한다.

## 현재 프론트에서 연결된 범위

- Receiver, Scoring, Launcher 연결 상태와 Event 인덱스 상태
- 전체 13개 보호 모듈 카탈로그와 Launcher component 상태
- 공통 Event의 세션, 플레이어, 모듈, submodule, 종류, 판정, 검색 필터
- 세션별 플레이어 판정 요약과 최대 관측 시각
- 플레이어별 Scoring Final Verdict, 정책 상태, 최신 근거, Launcher 실행 상태
- 서버 `sequence` 기준 플레이어 Event 타임라인과 Event 상세
- Snapshot과 Launcher status를 각각 조회하고, 한쪽 갱신 실패 시 마지막 성공 자료 유지
- 자동 갱신 실패 표시, Event watermark 초기화 시 전체 재동기화
- 연결·상세 조회 오류를 해당 dialog·drawer 안에서 표시
- Dashboard v2 응답의 핵심 중첩 구조를 런타임에 검증하고 계약 불일치 시 안전하게 중단
- Evidence 화면·JSON 복사 전 직접 식별 필드와 자유 문자열의 절대 경로 제거
- 독립 페이지 탐색, 주소 직접 접속·새로고침·뒤로/앞으로 이동
- 플레이어 상세 탭, 키보드 방향키·Home·End 탐색
- 개요·이벤트의 공통 SOC 표, 수신 순서·원점수·명시적 severity 정렬/필터
- 조사 패널의 현재 대상 판정·평가 완료 여부·근거·활성 모듈·reason codes와 플레이어 화면 이동
- 로드된 세션·플레이어 판정 분포와 탐지기별 관측 건수, 그래프에서 필터 목록으로 이동
- 선택 세션의 서버 수신 순번 구간별 관측 건수, 0점 이하·양수 관측 구분

필터는 세션·플레이어·타임라인·Event 표·Launcher 대상 목록에 같은 범위로 적용하며 페이지 이동 시 유지한다. 종합 현황은 필터와 관계없이 전체 조회 범위를 보여주고, 요약에서 목록으로 들어갈 때 필터를 초기화한다. 모듈 표는 다른 모듈로 이동할 수 있도록 `보호 모듈` 차원만 제외한 나머지 범위에서 비교한다. 모듈의 실행 상태는 현재 조회 대상의 heartbeat component를 사용한다.

## 프론트에서 임의로 만들지 않는 값

- Scoring이 주지 않는 모듈별 점수와 확률
- 세션 전체를 대표하는 최종 판정
- `UNKNOWN` 또는 `INCONCLUSIVE`를 정상으로 바꾼 값
- `raw_score`를 모듈끼리 더한 종합 점수
- `timestamp_ms`의 시간 기준이 불명확한 경우의 실제 시각
- stale heartbeat만으로 추정한 종료 원인
- 원점수로 환산한 Critical/High/Medium/Low severity
- 서버에 없는 사건 처리 상태·담당자·제재 이력
- 모든 PC를 대표하는 전역 활성 클라이언트 수

현재 세션 표의 판정은 서버의 세션 판정이 아니라 **그 세션에서 가장 우선 검토가 필요한 플레이어 판정 요약**이다. `max_observed_timestamp_ms`는 세션 길이가 아니라 **최대 관측 시각**으로 표시한다.

## 8-B·백엔드와 확정해야 하는 항목

1. 운영 인증
   - 현재 Bearer token 직접 입력과 Vite `/dashboard-api` proxy는 로컬 통합 시험용이다.
   - 운영에서는 same-origin BFF·reverse proxy 또는 Dashboard 로그인 세션이 필요하다.

2. 대용량 조회
   - 현재 API는 cursor를 제공하지만 화면은 안전 한도까지 여러 페이지를 합쳐 조회한다.
   - 실운영에서는 Event 종류·판정·날짜 필터와 서버 측 페이지 이동을 API에 추가해야 한다.

3. 판정 집계
   - 현재 Scoring은 플레이어 Final Verdict를 제공하지만 세션 Final Verdict와 비교 가능한 모듈 점수 목록은 제공하지 않는다.
   - 이 값이 확정되기 전까지 화면은 플레이어 판정 요약만 사용한다.

4. 증거 자료
   - 현재 `capabilities.evidence_images=false`이고 이미지·추가 로그 조회 API가 없다.
   - 지원 시 인증된 same-origin URL, 접근 권한, 보존 기간, 404 처리 규격이 필요하다.

5. 활성 세션
   - 현재 응답에는 현재 실행 중인 세션과 과거 종료 세션을 구분하는 공통 필드가 없다.
   - 오래된 `stopped`·`stale` heartbeat가 전체 상태를 오염시키지 않도록 session lifecycle 또는 active flag가 필요하다.

6. 개인정보 제거
   - 프론트도 알려진 직접 식별 Evidence 키와 reason·log의 절대 경로를 숨기지만, 임의 형식 문자열의 모든 개인정보를 판별할 수는 없다.
   - Receiver 또는 Dashboard backend 응답 단계에서 allowlist 기반 제거를 한 번 더 적용해야 한다.

7. SOC 조회 필드
   - 현재 Shared 7필드와 Dashboard v2에는 공통 severity와 workflow 상태가 없다.
   - 선택적 evidence.severity는 4개 표준값만 표시하되 필수 계약으로 강제하지 않는다. 미제공을 0 또는 Low로 바꾸지 않는다.
   - Event 표의 대상 판정은 해당 세션·플레이어의 현재 Scoring 상태이며 Event별 처리 상태가 아니다.
   - 클라이언트는 최신 Launcher source.client_id로 표시하며 Event 발신자와의 상관관계는 추가 계약이 필요하다.

## Launcher·모듈 등록 확인 사항

현재 main 기준 화면은 등록 계약을 그대로 표현한다. 다음 기능은 이름만 보고 구현됐다고 확대 해석하지 않는다.

- Self Defense: 현재 확인 가능한 범위는 watchdog과 프로세스 생존 상태다.
- Input Signature: 현재 확인 가능한 범위는 YARA와 알려진 실행 파일 해시다.
- Kernel Watcher: 실제 실행 경로와 heartbeat 등록 여부를 Launcher 담당과 최종 확인해야 한다.

## 종단 통합 확인 순서

1. Launcher가 같은 `session_id`와 `player_id`로 각 component heartbeat를 전송한다.
2. 탐지기가 Shared 7필드 Event를 Receiver에 전송한다.
3. Dashboard overview에서 세션·플레이어·component 상태가 나타나는지 확인한다.
4. Event 상세의 ID, sequence, module, submodule이 Receiver 저장 내용과 같은지 확인한다.
5. Scoring snapshot의 Final Verdict와 Dashboard 판정이 같은지 확인한다.
6. 프로세스 중지·전송 장애·복구 순서가 시스템 상태와 플레이어 실행 상태에 반영되는지 확인한다.
7. API 일시 실패 후 마지막 성공 자료가 남고 `LIVE · 지연`으로 바뀌는지 확인한다.
8. 새 Event가 들어온 뒤 자동 갱신으로 중복 없이 추가되는지 확인한다.
