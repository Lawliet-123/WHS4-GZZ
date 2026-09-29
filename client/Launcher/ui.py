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
>      "last_code": 1, "uptime_s": 12.4,
>      "detail": "의심 발견", "log": "...logs/memory_integrity.log"}
>
> status 값: MISSING / SKIPPED / PENDING / RUNNING / DONE / WARN / FAILED / STOPPED
>
> 서버 연결 상태는 ctx["server"] 로 들어옵니다. 하트비트를 붙이시면
> 그 값만 채워 주시면 됩니다.

## 출력은 stderr 로 보낸다

런처가 나중에 `--json` 같은 걸로 상태를 내보낼 수 있어야 해서, 사람이 보는
화면과 기계가 읽는 출력이 섞이면 안 된다. 같은 이유로 run_session.py 의
진행 표시도 stderr 로 보낸다.
"""

import sys

# 상태를 한눈에 구분하려고 짧은 표식을 붙인다. 한글 콘솔(cp949)에서 못 찍는
# 글자를 쓰면 출력하다 죽기 때문에 아스키만 쓴다.
MARK = {
    "RUNNING": "  ", "DONE": "  ", "WARN": "! ", "FAILED": "!!",
    "MISSING": "- ", "SKIPPED": "- ", "PENDING": ".. ", "STOPPED": "  ",
}


def line(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def render(rows, ctx=None) -> None:
    ctx = ctx or {}
    game = ctx.get("game_pid")
    server = ctx.get("server", "미연결")
    line("")
    line(f"  세션 {ctx.get('session', '-')}   게임 "
         + (f"PID {game}" if game else "미실행")
         + f"   서버 {server}")
    line(f"  {'모듈':<20} {'상태':<9} {'횟수':>4}  비고")
    line("  " + "-" * 72)
    for r in rows:
        extra = r.get("detail") or ""
        if r["status"] == "RUNNING" and r["mode"] == "continuous":
            extra = extra or f"{r['uptime_s']:.0f}초째"
        if len(extra) > 38:
            extra = extra[:37] + "…"
        line(f"  {MARK.get(r['status'], '  ')}{r['name']:<18} "
             f"{r['status']:<9} {r['runs']:>4}  {extra}")
    bad = [r for r in rows if r["status"] == "FAILED"]
    if bad:
        line("")
        for r in bad:
            line(f"  ! {r['name']} — 로그: {r['log']}")
