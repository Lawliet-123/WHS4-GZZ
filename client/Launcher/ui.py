"""상태 화면.

> **이 파일은 동효님 담당입니다.** 지금은 콘솔에 표를 그리는 최소 버전이고,
> GUI 로 바꾸셔도 main.py 는 손댈 필요가 없습니다. 아래 두 함수만 지켜 주세요.
>
>     render(rows, ctx)   rows 는 아래 모양의 dict 목록. 화면을 새로 그린다
>     line(text)          런처가 한 줄 알리고 싶을 때 (진행 상황·경고)
>
> rows 한 줄의 모양 (process_manager.ModuleState.snapshot):
>
>     {"name": "memory_integrity", "owner": "2번 (재민·랑언)",
>      "status": "RUNNING", "mode": "oneshot", "runs": 3,
>      "restarts": 0, "started_by": "launcher",
>      "last_code": 1, "uptime_s": 12.4,
>      "detail": "의심 발견", "log": "...logs/memory_integrity.log"}
>
> status 값: MISSING / SKIPPED / PENDING / RUNNING / DONE / WARN / RESTART / FAILED / STOPPED
>
> RESTART 는 상주 모듈이 죽어서 되살리는 중이라는 뜻이다(간격 대기 포함).
> started_by 는 지금 떠 있는 프로세스를 누가 띄웠는지("launcher" / "watchdog").
>
> 서버 연결 상태는 ctx["server"] 로 들어옵니다. 하트비트를 붙이시면
> 그 값만 채워 주시면 됩니다.

## 출력은 stderr 로 보낸다

런처가 나중에 `--json` 같은 걸로 상태를 내보낼 수 있어야 해서, 사람이 보는
화면과 기계가 읽는 출력이 섞이면 안 된다. 같은 이유로 run_session.py 의
진행 표시도 stderr 로 보낸다.
"""

import sys  # 사람에게 보여주는 상태 표를 표준 오류 스트림으로 보낸다.
from datetime import datetime  # 상태를 마지막으로 그린 시각을 표시한다.
import unicodedata  # 한글·전각 문자의 실제 콘솔 표시 너비를 계산한다.

# 상태를 한눈에 구분하려고 짧은 표식을 붙인다. 한글 콘솔(cp949)에서 못 찍는
# 글자를 쓰면 출력하다 죽기 때문에 아스키만 쓴다.
MARK = {
    "RUNNING": "  ", "DONE": "  ", "WARN": "! ", "FAILED": "!!",  # 정상·주의·실패를 ASCII로 구분한다.
    "MISSING": "- ", "SKIPPED": "- ", "PENDING": ".. ", "STOPPED": "  ",  # 미시작·종료도 구분한다.
    "RESTART": "~ ",  # 런처 또는 워치독이 되살리는 동안의 표시다.
}


def line(text: str) -> None:
    """진행 메시지를 즉시 보이게 하되 나중의 기계용 stdout과 섞지 않는다."""
    print(text, file=sys.stderr, flush=True)  # 버퍼가 쌓이지 않게 매 줄 바로 내보낸다.


def _width(text: str) -> int:
    """한글과 전각 문자가 들어가도 상태 표 열이 크게 어긋나지 않게 한다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1 for ch in text)  # 전각은 두 칸이다.


def _fit(value, columns: int) -> str:
    """값을 콘솔 표시 칸 수에 맞게 잘라내거나 공백을 채운다."""
    text = "" if value is None else str(value)  # None만 빈 값으로 취급하고 0은 그대로 표시한다.
    if _width(text) > columns:  # 긴 메시지가 다음 표 열을 밀어내지 않게 한다.
        out = ""  # 허용된 너비에 들어가는 앞부분만 모은다.
        for ch in text:  # 한글을 중간 바이트에서 자르지 않으려고 글자별로 본다.
            if _width(out + ch) > columns - 1:  # 마지막 한 칸은 생략 표시용이다.
                break  # 여기부터 뒤의 글자는 표에 넣지 않는다.
            out += ch  # 아직 범위 안이므로 글자를 추가한다.
        text = out + "~"  # ASCII 생략 표식으로 내용이 잘렸음을 알린다.
    return text + " " * (columns - _width(text))  # 짧은 값은 오른쪽 공백으로 정렬한다.


def render(rows, ctx=None) -> None:
    """런처 문맥과 각 모듈 스냅샷을 사람이 읽는 콘솔 표로 출력한다."""
    ctx = ctx or {}  # 단독 호출·테스트에서 문맥이 없어도 동작하게 한다.
    game = ctx.get("game_pid")  # 게임 프로세스가 실제로 떴는지 표시할 PID다.
    server = ctx.get("server", "미연결")  # 중앙 서버 통신 상태가 들어올 자리다.
    line("")  # 이전 주기 표와 시각적으로 구분한다.
    line(f"  [{datetime.now():%H:%M:%S}] 세션 {ctx.get('session', '-')}   게임 "
         + (f"PID {game}" if game else "미실행")  # PID가 없으면 게임 미실행이라고 적는다.
         + f"   서버 {server}")  # 세션·게임·서버를 한 줄에서 확인한다.
    ue4ss = ctx.get("ue4ss")  # 설치·로드 검증 결과가 실제로 전달됐는지 확인한다.
    line(f"  UE4SS {ue4ss or '검증 정보 없음'}")  # 정보가 없어도 정상처럼 조용히 숨기지 않는다.
    running = sum(r.get("status") == "RUNNING" for r in rows)  # 살아 있는 모듈 프로세스 수다.
    attention = sum(r.get("status") in ("MISSING", "SKIPPED", "WARN", "RESTART", "FAILED")
                    for r in rows)  # 시작 실패·검사 불가·재시작 중인 항목 수다.
    line(f"  모듈 실행 중 {running}/{len(rows)}   확인 필요 {attention}"
         "   (RUNNING은 탐지 성공·서버 전송 성공 판정이 아닙니다)")  # 상태의 의미를 과장하지 않는다.
    line("  " + _fit("모듈", 20) + " " + _fit("상태", 10)
         + " 실행 재시작 시작주체  비고")  # 시작 횟수와 재시작 횟수를 따로 보여준다.
    line("  " + "-" * 92)  # 표의 제목과 데이터 행을 구분한다.
    for r in rows:  # process_manager가 준 순서대로 모듈을 한 줄씩 그린다.
        extra = r.get("detail") or ""  # 실패·건너뜀 등의 원인을 우선 표시한다.
        if r.get("status") == "RUNNING" and r.get("mode") == "continuous":  # 상주 모듈에만 가동 시간을 쓴다.
            extra = extra or f"{r.get('uptime_s', 0):.0f}초째"  # 별도 메시지가 없으면 생존 시간을 대신 보여준다.
        source = r.get("started_by") or "-"  # 런처/워치독 중 누가 시작했는지 모를 때는 '-'다.
        line("  " + MARK.get(r.get("status"), "  ")
             + _fit(r.get("name", "-"), 18) + " "  # 상태 표식 뒤의 모듈 이름 열이다.
             + _fit(r.get("status", "-"), 10) + " "  # RUNNING·RESTART 등의 상태 열이다.
             + f"{r.get('runs', 0):>4} {r.get('restarts', 0):>6} "  # 시작·재시작 수를 정렬한다.
             + _fit(source, 9) + " " + _fit(extra, 30).rstrip())  # 시작 주체와 짧은 이유를 붙인다.
    bad = [r for r in rows if r.get("status") in ("MISSING", "SKIPPED", "WARN", "FAILED", "RESTART")]  # 원인이 필요한 행이다.
    if bad:  # 비정상·미검사 모듈의 전체 이유를 잘린 표 밖에 다시 보여준다.
        line("")  # 표 본문과 문제 목록을 띄운다.
        for r in bad:  # 문제가 있는 각 모듈에 대해 원인과 로그 위치를 적는다.
            detail = r.get("detail") or "상세 원인 없음"  # 표의 30칸에서 잘린 내용을 복원한다.
            line(f"  ! {r.get('name', '-')} ({r.get('status', '-')}) - {detail}")  # cp949 콘솔에서도 안전한 구분자다.
            if r.get("log"):  # 로그가 있으면 원문 확인 경로를 함께 준다.
                line(f"    로그: {r['log']}")  # 미검사·주의 상태의 로그도 숨기지 않는다.
