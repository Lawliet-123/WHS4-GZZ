# 보고용 캡처 근거

이 폴더의 이미지는 종합 Dashboard와 LocalGuard 연동 결과를 설명하기 위한 캡처다. 실제 구현 범위를 넘어선 탐지 성공으로 해석하지 않는다.

## dashboard-overview.png

React Dashboard의 데모 화면이다. Receiver·Scoring·Launcher 연결 상태, 공통 필터, 세션·플레이어·Event 요약, 13개 보호 모듈 상태를 한 화면에 표시한다.

## localguard-module-stable.png

실제 게임에서 `module_integrity`가 136개 DLL을 반복 관찰하는 동안 추가·변경·전송 Event가 0건으로 유지된 정상 관찰 화면이다. 첫 성공 스냅샷 이후 기준선이 안정적으로 유지되는지 확인한 자료이며, 전체 정상 플레이 판정을 대신하지 않는다.

## localguard-handle-detected.png

LocalGuard 외부 접근 통합 시험에서 관찰 대상이 4개에서 5개로 늘고 `emitted=1`이 된 화면이다. 이 캡처는 `external_process` 채널의 핸들 접근 감지 결과이며 `module_integrity`의 DLL 추가 탐지와는 구분한다.

`module_integrity` 자체의 양성 경로는 다음 명령으로 다시 검증했다.

```powershell
py -3 -m client.LocalGuard.external_access.module_integrity.smoke_test
```

검증 결과는 정상 서명된 `winhttp.dll`의 동적 로드를 `change_type=added`, `signature_status=trusted`, `raw_score=1`로 한 번 기록했다. 게임 파일이나 게임 프로세스는 수정하지 않는다.
