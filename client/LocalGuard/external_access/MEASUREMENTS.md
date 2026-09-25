# LocalGuard external process access v1 검증 결과

측정일: 2026-09-26

## 환경

- Windows, 논리 프로세서 16개
- Python 3.13
- 게임은 Steam에서 정상 실행
- 게임·에임봇·LocalGuard는 실제 핸들 관찰에 필요한 동일한 관리자 권한으로 실행
- CPU·메모리는 정상 게임 상태의 LocalGuard 프로세스를 1초 간격으로 20회 측정

## 기능 검증

### 정상 환경

```text
game_found=True observed=2 allowed=2 emitted=0 duration_ms=5833
```

검토된 정상 프로세스 두 개는 allowlist에 의해 제외됐고 탐지 결과는 생성되지 않았다.

### 직접 제작한 에임봇

첫 관리자 권한 회귀 검사에서 아래 결과를 확인했다.

- 접근 마스크: `0x001F3FFF`
- 접근 권한: `VM_READ`, `VM_WRITE`, `VM_OPERATION`, `CREATE_THREAD`
- 서명 상태: `trusted`
- 게시자: Python Software Foundation
- 경로 위험: `user_writable_location`
- `raw_score`: 8
- 해당 스캔 소요시간: 5,184ms

수동 실행·종료 테스트에서는 에임봇 핸들 생성 및 해제에 약 1~2초 내로 반응하는
것을 관찰했다. 이는 단일 PC의 실측 관찰값이며 최대 지연시간 보장은 아니다.

## 성능 측정

| 항목 | 결과 |
|---|---:|
| 측정 시간 | 20초 |
| 샘플 수 | 20 |
| 평균 CPU | 3.035% |
| 최고 CPU | 5.982% |
| 평균 Working Set | 42.40MB |
| 최고 Working Set | 62.23MB |
| 정상 환경 탐지 로그 | 생성되지 않음 |

CPU 비율은 전체 논리 프로세서 16개를 기준으로 정규화했다. 짧은 단일 환경 측정이므로
최종 성능 평가는 더 긴 플레이 세션과 다른 PC에서도 반복해야 한다.

## 자동 테스트

공통 파일 검사·JSONL 규격, 권한 점수, 서명·경로 보조 근거, allowlist,
핸들 대상 확인, 권한 부족 실패 처리를 포함한 단위 테스트 19개가 통과했다.

## 알려진 제한

- `NtQuerySystemInformation`은 Windows 내부 API이므로 OS 변경 시 재검증이 필요하다.
- 조회 전용 handle 복제를 우선 시도하지만 Windows가 거부하면 원래 접근 권한으로
  잠깐 복제해 대상 PID만 확인하고 즉시 닫는다.
- Windows·게임 업데이트로 allowlist 파일 해시가 바뀌면 재검토가 필요하다.
- 이 모듈은 탐지 근거와 `raw_score`만 기록하며 차단·종료·밴을 수행하지 않는다.
