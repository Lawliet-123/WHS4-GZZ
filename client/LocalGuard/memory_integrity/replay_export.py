"""탐지 세션을 팀 replay-data 양식으로 내보낸다 (7번 ReplayAnalyzer 제출용).

허송희가 2026-09-17 정한 양식이고 `replay-data/noclip/noclip_001/` 이 기준 예시다.

    replay-data/<핵>/<session_id>/
        manifest.json          라벨·구간 (정답지)
        events.jsonl           시계열 이벤트
        raw/<module>.jsonl     원본 로그 (형식 자유)

## 우리 로그와의 차이

`main.py` 는 한 세션에 모듈당 **한 건**을 낸다. 외부 스캔이라 스냅샷 하나가
한 번의 검사이기 때문이다. noclip 예시처럼 초당 여러 줄이 나오지 않는다.

그래서 시계열을 지어내지 않는다. **관측한 만큼만 쓴다.**
`manifest.json` 의 `samples` 로 몇 건인지 밝히고, 없는 구간을 채우지 않는다.

## 라벨은 추론하지 않는다

`label` 은 정답지다. 탐지 결과에서 역산하면 채점이 순환 논리가 된다
(탐지기가 잡았으니 CHEAT 이다 → 탐지기가 맞았다). 그래서 **인자로 받는다.**

사용:
    python replay_export.py <session_id> --cheat whistle-spoofing
    python replay_export.py <session_id> --clean
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _repo_root():
    """replay-data/ 를 가진 폴더를 찾는다. **상위 개수를 세지 않는다.**

    이 파일은 팀 구조가 바뀔 때마다 깊이가 달라졌다(anti-cheat/ ->
    LocalGuard/memory_integrity/ -> client/LocalGuard/memory_integrity/).
    dirname 을 몇 번 부를지 박아두면 그때마다 조용히 엉뚱한 자리를 가리킨다.
    실제로 예전 배치에서는 레포 **바깥**을 가리키고 있었다.
    """
    d = HERE
    while True:
        if os.path.isdir(os.path.join(d, "replay-data")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            # 아직 안 만들어졌을 수 있다. 그때는 .git 이 있는 자리를 레포로 본다.
            d2 = HERE
            while True:
                if os.path.isdir(os.path.join(d2, ".git")):
                    return d2
                p2 = os.path.dirname(d2)
                if p2 == d2:
                    raise SystemExit(
                        "레포 루트를 찾지 못했습니다. --out 으로 직접 지정해 주세요.")
                d2 = p2
        d = parent
DEFAULT_LOGS = os.path.join(HERE, "logs", "detection")

# 우리 모듈 -> 팀 replay-data 폴더 (modules/ 이름과 맞춘다)
CHEAT_DIRS = {
    "whistle-spoofing", "hide-anywhere", "auto-paint", "auto-paint-ver2",
    "esp", "godmode", "noclip", "aimbot",
}


def load_session(log_path):
    events = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    if not events:
        raise SystemExit(f"세션 로그가 비어 있습니다: {log_path}")
    return events


def to_replay_event(ev, player_id):
    """팀 events.jsonl 한 줄로 바꾼다.

    noclip 예시의 키를 그대로 쓴다 — session_id / player_id / module /
    timestamp_ms / evidence / reasons / raw_score.

    우리 `evidence` 는 문자열 dict 이고 예시는 숫자 dict 이지만, 예시에도
    스키마 강제는 없다. 숫자로 바꾸려고 정보를 버리지 않는다.
    """
    return {
        "session_id": ev["session_id"],
        # 이벤트에 박힌 값이 우선이다. 인자는 예전 로그를 위한 대비책이다.
        "player_id": ev.get("player_id") or player_id,
        "module": ev["module"],
        "timestamp_ms": ev["timestamp_ms"],
        "window_id": ev.get("window_id", 0),
        "sample_id": ev.get("sample_id", 0),
        "evidence": ev.get("evidence", {}),
        "reasons": ev.get("reasons", []),
        # `score` 는 2026-09-21 이전 로그의 옛 키다. 그때 찍은 세션 4개를
        # 다시 못 읽으면 지금까지의 측정이 통째로 날아간다.
        "raw_score": ev.get("raw_score", ev.get("score", 0)),
        # 우리 계약의 상태를 같이 싣는다. ERROR/OFFLINE 은 CLEAN 이 아니고
        # 집계에서 빼야 하는데, raw_score 만 보면 0 이라 구분이 안 된다.
        "status": ev.get("status"),
        "severity": ev.get("severity"),
    }


def load_markers(log_dir, stem, run_id=None):
    """run_session.py --watch 가 남긴 ON/OFF 표시를 읽는다.

    돌려주는 값: (시작 상태, 토글 목록, 버린 줄 수). 파일이 없으면 None.

    run_id 가 주어지면 **그 실행의 줄만** 쓴다. 같은 이름으로 두 번 돌린
    기록이 섞였을 때 다른 실행의 ON/OFF 가 끼어들지 않게 한다. 버린 줄은
    조용히 버리지 않고 개수를 돌려준다.
    """
    p = os.path.join(log_dir, f"{stem}.markers.jsonl")
    if not os.path.exists(p):
        return None
    initial, toggles, dropped = "OFF", [], 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = json.loads(line)
            if run_id and m.get("run_id") and m["run_id"] != run_id:
                dropped += 1
                continue
            if m.get("initial"):
                initial = m["state"]
            else:
                toggles.append(m)
    return initial, toggles, dropped


def load_meta(log_dir, stem):
    p = os.path.join(log_dir, f"{stem}.meta.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cheat_windows(toggles, session_end_ms, initial="OFF"):
    """ON/OFF 표시를 [켠 시각, 끈 시각] 구간 목록으로 바꾼다.

    시작 상태는 마커 파일 첫 줄에 적혀 있다(run_session.Markers).
    마지막이 ON 으로 끝났으면 세션 끝으로 닫고 open_ended 를 켠다 —
    끈 시각을 **모르는** 것이지 안 끈 게 확실한 건 아니다. 분석하는 쪽이
    Post-OFF 계산에서 뺄 수 있게 표시를 같이 넘긴다.
    """
    wins, start, open_ended = [], (0 if initial == "ON" else None), False
    for m in sorted(toggles, key=lambda m: m["t_ms"]):
        if m["state"] == "ON" and start is None:
            start = m["t_ms"]
        elif m["state"] == "OFF" and start is not None:
            wins.append([start, m["t_ms"]])
            start = None
    if start is not None:
        # 창을 닫아 끝냈으면 마지막 기록 시각이 ON 보다 앞일 수 있다.
        # 길이가 음수인 구간을 내보내지 않는다.
        wins.append([start, max(session_end_ms, start)])
        open_ended = True
    return wins, open_ended


# 모듈마다 "핵 구간"이 뜻하는 게 다르다.
#
# 반복 관측의 ON/OFF 는 **토글**을 누른 시각이다. 그런데 injection·overlay_hook·
# whistle 은 토글이 아니라 **DLL 이 프로세스에 들어와 있는지**를 본다. DLL 은
# 토글 전에 주입되고 끈 뒤에도 남아 있으므로, 토글 구간으로 재면 지연은
# 음수, ON 전은 오탐, Post-OFF 는 무한대로 나온다(2026-09-23 검토).
# 탐지기가 틀린 게 아니라 기준이 다른 것이라, 모듈마다 무엇을 따라가는지
# manifest 에 적어 분석 쪽이 기준을 고를 수 있게 한다.
MODULE_TIMING = {
    "value_tamper": "toggle",        # 핵이 값을 쓰는 동안. 단 끈 뒤에도 값이 남을 수 있다
    "whistle_rpc":  "toggle",        # 도발을 연타하는 동안 (구간 모드)
    "injection":    "dll_resident",  # DLL 이 들어와 있는 동안 — 토글과 무관
    "overlay_hook": "dll_resident",
    "whistle":      "dll_resident",  # 핵 DLL 이 건 후크의 흔적
    "filesystem":   "advisory",      # 디스크의 파일 흔적 — 게임·토글과 무관
}


def _phase(ev, wins):
    """이 이벤트가 핵 구간 안에서 본 것인가.

    스캔은 한 번에 수 초~10초 넘게 걸린다. timestamp_ms(스캔 끝)만 보면,
    끄기 직전에 읽고 끈 뒤에 끝난 스캔이 Post-OFF 로 집계된다. 스캔 구간
    [시작, 끝] 이 있으면 그걸로 가른다.

      ON        스캔 전체가 한 핵 구간 안
      OFF       스캔 전체가 모든 핵 구간 밖
      BOUNDARY  스캔 도중에 켜거나 껐다 — 어느 쪽 상태를 봤는지 모른다
    """
    s = ev.get("scan_start_ms", ev["timestamp_ms"])
    e = ev.get("scan_end_ms", ev["timestamp_ms"])
    if any(a <= s and e <= b for a, b in wins):
        return "ON"
    if all(e < a or b < s for a, b in wins):
        return "OFF"
    return "BOUNDARY"


def _check_integrity(events, session_id, log_path):
    """한 파일에 두 실행이 섞였는지 본다. 섞였으면 내보내지 않는다.

    같은 이름으로 다시 돌리면 이어 쓰기라 예전 실행 뒤에 새 실행이 붙었다.
    그러면 시각이 한 번 되돌아간다. 지금은 run_session 이 같은 이름을 막지만,
    이미 섞인 옛 파일을 내보내면 핵 구간과 타임라인이 틀린 채로 7번에 간다.
    """
    bad = [e.get("session_id") for e in events if e.get("session_id") != session_id]
    if bad:
        raise SystemExit(f"다른 세션의 이벤트가 섞여 있습니다({bad[0]}): {log_path}")
    ts = [e["timestamp_ms"] for e in events]
    for i in range(1, len(ts)):
        if ts[i] < ts[i - 1]:
            raise SystemExit(
                f"시각이 {ts[i - 1]}ms -> {ts[i]}ms 로 되돌아갑니다. 같은 세션 이름으로 "
                f"두 번 실행된 기록입니다: {log_path}\n"
                f"    실행마다 다른 --session 이름으로 다시 측정해 주세요.")
    # (window_id, sample_id) 중복은 **두 키가 실제로 있는 이벤트끼리만** 본다.
    # 9/21 이전 로그에는 이 키가 없는데, 기본값 0 으로 채운 뒤 비교하면
    # 옛 세션이 전부 중복으로 거부된다.
    seen = set()
    for e in events:
        if "window_id" in e and "sample_id" in e:
            k = (e["window_id"], e["sample_id"])
            if k in seen:
                raise SystemExit(f"같은 (window_id, sample_id)={k} 가 두 번 나옵니다. "
                                 f"두 실행이 섞인 기록입니다: {log_path}")
            seen.add(k)


def _find_logs(session_id):
    """세션 로그가 있는 폴더를 찾는다.

    러너마다 자기 폴더에 쓴다(2번은 memory_integrity/logs/detection,
    휘파람은 detectors/whistle-spoofing/logs/detection). 예전에는 2번 폴더만
    봐서, 휘파람 세션은 --logs 를 모르면 못 찾았고 **같은 이름이 2번 폴더에
    있으면 엉뚱한 옛 세션을 조용히 내보냈다**(검토에서 재현).
    """
    cands = [DEFAULT_LOGS]
    det = os.path.join(_repo_root(), "client", "detectors")
    if os.path.isdir(det):
        for name in sorted(os.listdir(det)):
            cands.append(os.path.join(det, name, "logs", "detection"))
    hits = [d for d in cands if os.path.exists(os.path.join(d, f"{session_id}.jsonl"))]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"세션 '{session_id}' 로그를 찾지 못했습니다. 찾아본 자리:\n    "
                         + "\n    ".join(cands))
    raise SystemExit(f"세션 '{session_id}' 가 여러 폴더에 있습니다:\n    "
                     + "\n    ".join(hits) + "\n    --logs 로 하나를 골라 주세요.")


def export(session_id, cheat, out_root, log_dir=None, player_id="player_001"):
    log_dir = log_dir or _find_logs(session_id)
    log_path = os.path.join(log_dir, f"{session_id}.jsonl")
    if not os.path.exists(log_path):
        raise SystemExit(f"세션 로그가 없습니다: {log_path}")

    events = load_session(log_path)
    _check_integrity(events, session_id, log_path)
    meta = load_meta(log_dir, session_id) or {}
    times = [e["timestamp_ms"] for e in events]

    mk = load_markers(log_dir, session_id, meta.get("run_id"))
    initial, toggles, dropped = mk if mk else ("OFF", [], 0)
    if dropped:
        print(f"! 다른 실행의 마커 {dropped}줄을 뺐습니다.", file=sys.stderr)
    # 창을 닫아 끝내면 meta 의 ended_ms 가 마지막 바퀴보다 앞일 수 있다.
    # 가장 늦은 기록을 세션 끝으로 본다.
    session_end = max([meta.get("ended_ms") or 0, max(times)]
                      + [m["t_ms"] for m in toggles])
    wins, open_ended = cheat_windows(toggles, session_end, initial)

    if cheat is None and wins:
        # --clean 이라고 해놓고 핵 ON 표시가 있다. 둘 중 하나가 틀렸다.
        # 조용히 NORMAL 로 내면 핵 세션이 정상 표본에 섞인다.
        raise SystemExit(
            f"--clean 인데 핵 ON 구간이 {len(wins)}개 있습니다. "
            f"핵 세션이면 --cheat <핵이름> 으로 내보내 주세요.")

    folder = cheat or "normal"
    dest = os.path.join(out_root, folder, session_id)
    os.makedirs(os.path.join(dest, "raw"), exist_ok=True)

    marked = mk is not None
    rows = []
    for e in events:
        r = to_replay_event(e, player_id)
        # 우리 확장 키. 팀 스키마에는 없다.
        for k in ("scan_start_ms", "scan_end_ms"):
            if k in e:
                r[k] = e[k]
        if cheat and marked:
            r["cheat_phase"] = _phase(e, wins)
        elif cheat:
            r["cheat_phase"] = "UNKNOWN"     # 핵 세션인데 언제 켰는지 모른다
        else:
            r["cheat_phase"] = "OFF"
        rows.append(r)
    with open(os.path.join(dest, "events.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 원본은 그대로 같이 넣는다. 가공본만 남기면 재검증을 못 한다.
    with open(os.path.join(dest, "raw", "detection.jsonl"), "w",
              encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    for suffix in (".markers.jsonl", ".meta.json"):
        src = os.path.join(log_dir, f"{session_id}{suffix}")
        if os.path.exists(src):
            with open(src, encoding="utf-8") as a, \
                 open(os.path.join(dest, "raw", f"detection{suffix}"), "w",
                      encoding="utf-8") as b:
                b.write(a.read())

    modules = sorted({e["module"] for e in events})
    by_mod = {m: [e for e in events if e["module"] == m] for m in modules}
    failed = ("ERROR", "OFFLINE")

    # Post-OFF 우측 절단. 마지막으로 끈 뒤 **세션 끝까지 계속 걸려 있었으면**
    # Post-OFF 는 "그만큼 지속"이 아니라 "적어도 그만큼, 끝은 모름"이다.
    # Hide Anywhere 은 끄면 쓰기만 멈추고 값을 되돌리지 않으므로 value_tamper 가
    # 이렇게 된다. 탐지기가 틀린 게 아니라 메모리에 변조된 값이 실제로 남아 있다.
    censored = []
    if wins and not open_ended:
        last_off = wins[-1][1]
        for m, evs in by_mod.items():
            after = [e for e in evs if e.get("scan_start_ms", e["timestamp_ms"]) > last_off]
            if after and after[-1].get("status") in ("SUSPICIOUS", "DETECTED"):
                censored.append(m)

    manifest = {
        "label": "CHEAT" if cheat else "NORMAL",
        "player_id": player_id,
        "session_id": session_id,
        "cheat_type": cheat.upper().replace("-", "_") if cheat else None,
        # 반복 관측(--watch)에서 사람이 Enter 로 표시한 시각이다.
        # 표시가 없으면 **모르는 것**이다. 지어내지 않고 비운다.
        "cheat_start_ms": wins[0][0] if wins else None,
        "cheat_end_ms": wins[0][1] if wins else None,
        # 여러 번 켰다 껐다 했으면 전부. 송희님 ReplayAnalyzer 가 이 형식도 읽는다.
        "cheat_windows_ms": wins or None,
        # ── 아래는 우리가 덧붙이는 것 ──
        "cheat_timing": ("marked" if marked and cheat else
                         "none (normal session)" if not cheat else
                         "unknown (single snapshot, no markers)"),
        # 마지막 구간의 끝은 세션 끝으로 채운 값이다. 실제로 끈 시각이 아니다.
        "cheat_open_ended": open_ended,
        "initial_state": initial if marked else None,
        # 모듈마다 무엇을 따라가는가. "toggle" 인 모듈만 위 핵 구간으로
        # 지연·Post-OFF 를 재야 한다. 나머지는 DLL 주입 시각이 기준이다.
        "module_timing": {m: MODULE_TIMING.get(m, "unknown") for m in modules},
        # 끈 뒤 세션 끝까지 계속 걸린 모듈. Post-OFF 는 하한값이다.
        "post_off_censored": censored,
        # 이벤트마다 cheat_phase(ON/OFF/BOUNDARY) 를 달았다. BOUNDARY 는 스캔 도중에
        # 켜거나 끈 것이라 어느 상태를 봤는지 모른다 — 집계에서 빼는 게 안전하다.
        "phase_counts": ({ph: sum(1 for r in rows if r["cheat_phase"] == ph)
                          for ph in ("ON", "OFF", "BOUNDARY")}
                         if cheat and marked else None),
        "source": ("external scan, repeated (run_session.py --watch)"
                   if meta.get("mode") == "watch"
                   else "external scan, single snapshot (run_session.py)"),
        "rounds": meta.get("rounds"),
        "interval_s": meta.get("interval_s"),
        # 표본 수를 밝혀야 집계가 정직해진다.
        "samples": len(events),
        "span_ms": [min(times), max(times)],
        "modules": modules,
        # **모든** 이벤트가 검사 실패인 모듈만 통째로 뺀다. 반복 관측에서 한 바퀴만
        # ERROR 였던 모듈까지 빼면 나머지 정상 관측이 다 버려진다.
        "excluded_from_scoring": sorted(
            m for m, evs in by_mod.items()
            if all(e.get("status") in failed for e in evs)),
        # 바퀴 단위 실패 수. 이 이벤트들은 집계에서 빼야 한다.
        "error_events": {m: n for m, n in
                         ((m, sum(1 for e in evs if e.get("status") in failed))
                          for m, evs in by_mod.items()) if n},
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return dest, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="탐지 세션 -> replay-data 양식")
    ap.add_argument("session")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cheat", choices=sorted(CHEAT_DIRS),
                   help="핵이 켜져 있던 세션. 폴더 이름이 된다")
    g.add_argument("--clean", action="store_true", help="핵 없는 정상 세션")
    ap.add_argument("--out", default=None, help="replay-data 루트")
    ap.add_argument("--logs", default=None,
                    help="세션 로그 폴더. 안 주면 러너 폴더들을 찾아본다 "
                         "(같은 이름이 여러 곳에 있으면 지정해야 한다)")
    ap.add_argument("--player", default="player_001")
    a = ap.parse_args(argv)

    out = a.out or os.path.join(_repo_root(), "replay-data")
    dest, manifest = export(a.session, None if a.clean else a.cheat,
                            out, a.logs, a.player)
    print(f"{dest}")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
