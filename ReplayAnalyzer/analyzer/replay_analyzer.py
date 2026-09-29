from pathlib import Path
import csv
import json
from collections import defaultdict

# =========================================================
# ReplayAnalyzer - Auto Module / Auto Threshold Version
#
# 기능
# 1. replay-data 아래 모든 세션 자동 탐색
# 2. 한 세션에 여러 module이 있어도 module별로 자동 분리
# 3. 기존처럼 한 세션에 module 하나만 있는 데이터도 그대로 지원
# 4. 각 module의 raw_score 범위를 자동 확인
# 5. module별 threshold 후보를 자동 생성
# 6. threshold마다 TP / FP / FN / TN 계산
# 7. 탐지율 / 오탐률 / 미탐률 / 평균 탐지시간 계산
# 8. CHEAT OFF 이후 탐지 지속 여부/시간 계산
# 9. cheat_start_ms / cheat_end_ms가 null이어도 세션 단위 TP/FN 계산
# 10. ERROR / OFFLINE 이벤트는 scoring에서 제외
# 11. manifest.excluded_from_scoring에 적힌 module은 분석에서 제외
# 12. detector별 target cheat_type 범위를 반영해 관계없는 CHEAT를 FN에서 제외
# 13. filesystem 같은 advisory 모듈은 TP/FP/FN/TN에서 분리
# 14. legacy manifest(session_id/start/end)와 window manifest(test_id/cheat_windows_ms) 모두 지원
# 15. 결과를 콘솔 + CSV로 저장
#
# 공통 Event 권장 필드:
# session_id, player_id, module, timestamp_ms,
# evidence, reasons, raw_score
#
# status / severity 같은 추가 필드는 있어도 됨.
# =========================================================

OUTPUT_DIR_NAME = "analysis-output"
EXCLUDED_EVENT_STATUSES = {"ERROR", "OFFLINE"}

# =========================================================
# 평가 범위 설정
#
# 핵심 원칙:
# - NORMAL 세션은 해당 detector의 오탐(FP) 검증용으로 사용
# - CHEAT 세션은 그 detector가 원래 잡도록 설계된 cheat_type일 때만
#   TP/FN 계산에 포함
# - 다른 종류의 핵은 OUT_OF_SCOPE로 빼서 FN으로 세지 않음
# - filesystem처럼 "현재 핵 활성 여부"가 아니라 환경/흔적을 보는 모듈은
#   현재 NORMAL/CHEAT manifest만으로 TP/FP를 정의하기 어려워 ADVISORY로 분리
#
# 새 detector가 생기면 필요할 때 이 표에 target cheat_type만 추가하면 됨.
# 표에 없는 module도 module 이름과 cheat_type이 직접 일치하면 자동 평가됨.
# =========================================================
MODULE_TARGETS = {
    "whistle": {"WHISTLE_SPOOFING"},
    "whistle_rpc": {"WHISTLE_SPOOFING"},
    "noclip": {"NOCLIP"},
    "godmode": {"GODMODE"},
    "esp": {"ESP", "ESP_ONLY"},
    "aimbot": {"AIMBOT"},
    "auto-paint": {"AUTO_PAINT", "AUTOPAINT"},
    "auto_paint": {"AUTO_PAINT", "AUTOPAINT"},
    "auto-paint-ver2": {"AUTO_PAINT", "AUTO_PAINT_VER2", "AUTOPAINT"},
    "auto_paint_ver2": {"AUTO_PAINT", "AUTO_PAINT_VER2", "AUTOPAINT"},
    "hide-anywhere": {"HIDE_ANYWHERE"},
    "hide_anywhere": {"HIDE_ANYWHERE"},

    # 범용 detector: 현재 팀 설계상 잡도록 의도된 구현 축만 포함
    # ESP처럼 외부 RPM 읽기 방식은 injection detector의 FN으로 세지 않음.
    "injection": {"WHISTLE_SPOOFING", "AUTO_PAINT", "AUTOPAINT", "GODMODE", "HIDE_ANYWHERE"},
    "value_tamper": {"GODMODE", "NOCLIP", "HIDE_ANYWHERE", "AIMBOT"},
}

ADVISORY_MODULES = {
    "filesystem",
}


def find_repo_root_and_replay_data():
    """replay_analyzer_auto.py 위치에서 위쪽으로 올라가며 replay-data 폴더를 찾음."""
    here = Path(__file__).resolve().parent

    for folder in [here, *here.parents]:
        replay_data = folder / "replay-data"
        if replay_data.exists():
            return folder, replay_data

    replay_data = Path.cwd() / "replay-data"
    if replay_data.exists():
        return Path.cwd(), replay_data

    raise FileNotFoundError("replay-data 폴더를 찾을 수 없습니다.")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def load_jsonl(path):
    """JSONL을 읽음. 잘못된 줄은 경고만 출력하고 계속 진행."""
    events = []

    with open(path, "r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as error:
                print(f"[WARNING] JSON 오류: {path} / line {line_number}")
                print(error)

    return events


def normalize_manifest(manifest, session_dir, events):
    """
    서로 다른 manifest 형식을 ReplayAnalyzer 내부 표준 형식으로 정규화한다.

    지원 형식 1 (기존):
      session_id / label / cheat_type / cheat_start_ms / cheat_end_ms

    지원 형식 2 (Aimbot 등):
      test_id / label / cheat_type / cheat_windows_ms

    cheat_windows_ms가 여러 개면 원본 windows를 유지하고,
    legacy 호환용 cheat_start_ms/cheat_end_ms에는 첫 시작/마지막 종료를 넣는다.
    """
    normalized = dict(manifest)

    session_id = (
        normalized.get("session_id")
        or normalized.get("test_id")
        or session_dir.name
    )
    normalized["session_id"] = str(session_id)

    label = normalized.get("label")
    if label is not None:
        normalized["label"] = str(label).upper()

    # manifest에 player_id가 없으면 event에서 첫 player_id를 가져온다.
    if not normalized.get("player_id"):
        for event in events:
            player_id = event.get("player_id")
            if player_id:
                normalized["player_id"] = player_id
                break

    windows = normalized.get("cheat_windows_ms")
    normalized_windows = []

    if isinstance(windows, list):
        for window in windows:
            if not isinstance(window, (list, tuple)) or len(window) < 2:
                continue

            start, end = window[0], window[1]
            if not isinstance(start, (int, float)):
                continue
            if end is not None and not isinstance(end, (int, float)):
                continue

            normalized_windows.append([start, end])

    # 기존 start/end 형식도 내부적으로 windows 배열로 통일한다.
    if not normalized_windows:
        start = normalized.get("cheat_start_ms")
        end = normalized.get("cheat_end_ms")
        if isinstance(start, (int, float)):
            normalized_windows = [[start, end]]

    normalized["cheat_windows_ms"] = normalized_windows

    if normalized_windows:
        normalized["cheat_start_ms"] = normalized_windows[0][0]

        finite_ends = [
            window[1] for window in normalized_windows
            if isinstance(window[1], (int, float))
        ]
        normalized["cheat_end_ms"] = finite_ends[-1] if finite_ends else None
    else:
        normalized.setdefault("cheat_start_ms", None)
        normalized.setdefault("cheat_end_ms", None)

    return normalized


def event_in_cheat_windows(timestamp, windows):
    """timestamp가 하나 이상의 핵 활성 구간 안에 있는지 확인."""
    for start, end in windows:
        if end is None:
            if timestamp >= start:
                return True
        elif start <= timestamp <= end:
            return True
    return False


def window_start_for_timestamp(timestamp, windows):
    """timestamp가 속한 핵 구간의 시작 시각을 반환."""
    for start, end in windows:
        if end is None:
            if timestamp >= start:
                return start
        elif start <= timestamp <= end:
            return start
    return None


def module_post_off_is_censored(module, manifest):
    """manifest의 post_off_censored에 현재 module이 포함되는지 확인."""
    configured = manifest.get("post_off_censored", [])

    if configured is True:
        return True
    if not isinstance(configured, (list, tuple, set)):
        return False

    normalized_module = normalize_module_name(module)
    return any(
        normalize_module_name(value) == normalized_module
        for value in configured
    )


def fallback_module_name(session_dir, manifest):
    """event에 module이 없을 때만 사용하는 fallback."""
    cheat_type = manifest.get("cheat_type")
    if cheat_type:
        return str(cheat_type).lower()

    return session_dir.parent.name.lower()


def normalize_module_name(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def normalize_cheat_type(value):
    """cheat_type 비교용 정규화. 공백/하이픈을 언더스코어로 통일."""
    if value is None:
        return None
    text = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text or None


def module_target_types(module):
    module = normalize_module_name(module)
    configured = MODULE_TARGETS.get(module)
    if configured is None:
        return None
    return {normalize_cheat_type(value) for value in configured}


def module_scope_mode(module):
    module = normalize_module_name(module)
    if module in ADVISORY_MODULES:
        return "ADVISORY"
    if module in MODULE_TARGETS:
        return "TARGETED"
    return "AUTO_MATCH"


def cheat_session_in_scope(module, manifest):
    """
    CHEAT 세션이 이 detector의 성능 평가 대상인지 판단.

    우선순위:
    1) MODULE_TARGETS에 명시된 module -> 해당 cheat_type만 대상
    2) 미등록 module -> module 이름과 cheat_type이 직접 일치하면 대상
       (예: noclip <-> NOCLIP)
    3) cheat_type이 없으면 범위를 확정할 수 없으므로 제외
    """
    cheat_type = normalize_cheat_type(manifest.get("cheat_type"))
    if cheat_type is None:
        return False

    targets = module_target_types(module)
    if targets is not None:
        return cheat_type in targets

    module_as_type = normalize_cheat_type(module)
    if module_as_type == cheat_type:
        return True

    # 흔한 이름 차이도 최소한으로 허용
    # 예: whistle <-> WHISTLE_SPOOFING
    if module_as_type and (
        cheat_type.startswith(module_as_type + "_")
        or module_as_type.startswith(cheat_type + "_")
    ):
        return True

    return False


def split_session_by_module(session_dir, manifest, events):
    """
    세션 하나의 events.jsonl을 module별 pseudo-session으로 분리한다.

    기존 데이터:
      noclip 세션 안의 모든 event.module == "noclip"
      -> pseudo-session 1개 생성 (기존과 동일)

    다중 모듈 데이터:
      filesystem / injection / whistle ...
      -> module별 pseudo-session 여러 개 생성

    manifest.excluded_from_scoring에 적힌 module은 제외한다.
    """
    fallback = fallback_module_name(session_dir, manifest)
    excluded_modules = {
        normalize_module_name(name)
        for name in manifest.get("excluded_from_scoring", [])
        if normalize_module_name(name)
    }

    grouped = defaultdict(list)

    for event in events:
        module = normalize_module_name(event.get("module")) or fallback
        grouped[module].append(event)

    pseudo_sessions = []

    # events가 비어 있어도 fallback module로 빈 세션 하나는 남긴다.
    if not grouped:
        grouped[fallback] = []

    for module, module_events in grouped.items():
        if module in excluded_modules:
            print(
                f"[SKIP] excluded_from_scoring: "
                f"session={manifest.get('session_id', session_dir.name)} / module={module}"
            )
            continue

        pseudo_sessions.append({
            "session_dir": session_dir,
            "manifest": manifest,
            "events": module_events,
            "module": module,
        })

    return pseudo_sessions


def find_sessions(replay_data):
    """replay-data 안의 모든 manifest.json을 찾아 module별 pseudo-session 목록 생성."""
    sessions = []

    for manifest_path in sorted(replay_data.rglob("manifest.json")):
        session_dir = manifest_path.parent
        events_path = session_dir / "events.jsonl"

        if not events_path.exists():
            print(f"[SKIP] events.jsonl 없음: {session_dir}")
            continue

        try:
            manifest = load_json(manifest_path)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"[SKIP] 잘못된 manifest.json: {manifest_path}")
            print(f"       {error}")
            continue

        events = load_jsonl(events_path)
        manifest = normalize_manifest(manifest, session_dir, events)

        sessions.extend(
            split_session_by_module(
                session_dir,
                manifest,
                events,
            )
        )

    return sessions


def event_is_scoring_eligible(event):
    """ERROR/OFFLINE 결과는 CLEAN/NORMAL로 간주하지 않고 scoring에서 제외.

    legacy 데이터의 최상위 status와 shared 7필드 형식의 evidence.status를
    둘 다 확인한다. 둘 중 하나라도 제외 상태면 scoring에서 제외한다.
    """
    statuses = []

    top_level_status = event.get("status")
    if top_level_status is not None:
        statuses.append(str(top_level_status).upper())

    evidence = event.get("evidence")
    if isinstance(evidence, dict):
        evidence_status = evidence.get("status")
        if evidence_status is not None:
            statuses.append(str(evidence_status).upper())

    return not any(status in EXCLUDED_EVENT_STATUSES for status in statuses)


def numeric_scoring_events(events):
    return [
        event for event in events
        if event_is_scoring_eligible(event)
        and isinstance(event.get("raw_score"), (int, float))
        and isinstance(event.get("timestamp_ms"), (int, float))
    ]


def numeric_scores(sessions):
    """해당 module에서 scoring 가능한 모든 raw_score 수집."""
    scores = []

    for session in sessions:
        for event in numeric_scoring_events(session["events"]):
            scores.append(float(event["raw_score"]))

    return scores


def build_threshold_candidates(scores):
    """
    raw_score를 보고 threshold 후보 자동 생성.

    - 정수 점수이며 최대값이 50 이하: 1 ~ 최대점수+1
    - 실수이거나 범위가 큰 경우: 실제 관측된 양수 점수 + 최대값보다 큰 값 하나
      예: 0/60/100 -> 60, 100, 101
    """
    if not scores:
        return [1]

    positive = sorted(set(score for score in scores if score > 0))
    if not positive:
        return [1]

    all_integer = all(score.is_integer() for score in positive)
    max_score = max(positive)

    if all_integer and max_score <= 50:
        return list(range(1, int(max_score) + 2))

    candidates = positive.copy()
    candidates.append(max_score + 1)
    return candidates


def analyze_session(module, manifest, events, threshold):
    """
    module별 세션 하나를 특정 threshold로 평가.

    CHEAT + cheat_windows_ms 또는 cheat_start_ms/end_ms 존재:
      핵 활성화 구간 안에서 raw_score >= threshold가 있으면 TP, 없으면 FN.
      여러 활성화 구간도 지원한다.

    CHEAT + 활성화 구간 정보 없음:
      전체 세션에서 threshold 이상이 있으면 TP.
      탐지 지연시간은 계산하지 않음(N/A).

    NORMAL:
      threshold 이상이 한 번이라도 있으면 FP, 없으면 TN.

    ERROR/OFFLINE 이벤트:
      scoring 이벤트에서 제외.
      해당 module의 이벤트가 전부 ERROR/OFFLINE이면 EXCLUDED 세션으로 처리.

    Post-OFF:
      cheat_end_ms가 있을 때만 계산.
    """
    label = str(manifest.get("label", "")).upper()
    cheat_start = manifest.get("cheat_start_ms")
    cheat_end = manifest.get("cheat_end_ms")
    cheat_windows = manifest.get("cheat_windows_ms") or []
    cheat_open_ended = bool(manifest.get("cheat_open_ended", False))
    post_off_censored = module_post_off_is_censored(module, manifest)
    scope_mode = module_scope_mode(module)

    all_numeric_events = [
        event for event in events
        if isinstance(event.get("raw_score"), (int, float))
        and isinstance(event.get("timestamp_ms"), (int, float))
    ]
    numeric_events = numeric_scoring_events(events)

    excluded_event_count = len(all_numeric_events) - len(numeric_events)

    # filesystem처럼 현재 NORMAL/CHEAT 라벨로 성능을 정의하기 어려운 모듈
    if scope_mode == "ADVISORY":
        return {
            "session_id": manifest.get("session_id", "unknown"),
            "player_id": manifest.get("player_id", "unknown"),
            "label": label,
            "cheat_type": manifest.get("cheat_type"),
            "threshold": threshold,
            "max_score": max((event.get("raw_score", 0) for event in numeric_events), default=0),
            "result": "ADVISORY",
            "cheat_start_ms": cheat_start,
            "cheat_end_ms": cheat_end,
            "cheat_windows_ms": cheat_windows,
            "first_detection_ms": None,
            "last_detection_ms": None,
            "detection_latency_ms": None,
            "post_off_detection_count": 0,
            "first_post_off_detection_ms": None,
            "last_post_off_detection_ms": None,
            "post_off_duration_ms": None,
            "excluded_event_count": excluded_event_count,
            "window_mode": "advisory",
            "scope_mode": scope_mode,
            "cheat_open_ended": cheat_open_ended,
            "post_off_censored": post_off_censored,
        }

    # 이 detector와 관계없는 다른 종류의 CHEAT 세션은 FN으로 세지 않는다.
    if label == "CHEAT" and not cheat_session_in_scope(module, manifest):
        return {
            "session_id": manifest.get("session_id", "unknown"),
            "player_id": manifest.get("player_id", "unknown"),
            "label": label,
            "cheat_type": manifest.get("cheat_type"),
            "threshold": threshold,
            "max_score": max((event.get("raw_score", 0) for event in numeric_events), default=0),
            "result": "OUT_OF_SCOPE",
            "cheat_start_ms": cheat_start,
            "cheat_end_ms": cheat_end,
            "cheat_windows_ms": cheat_windows,
            "first_detection_ms": None,
            "last_detection_ms": None,
            "detection_latency_ms": None,
            "post_off_detection_count": 0,
            "first_post_off_detection_ms": None,
            "last_post_off_detection_ms": None,
            "post_off_duration_ms": None,
            "excluded_event_count": excluded_event_count,
            "window_mode": "out_of_scope",
            "scope_mode": scope_mode,
            "cheat_open_ended": cheat_open_ended,
            "post_off_censored": post_off_censored,
        }

    # 이벤트는 있었는데 ERROR/OFFLINE 때문에 scoring 가능한 이벤트가 하나도 없는 경우
    if events and not numeric_events:
        return {
            "session_id": manifest.get("session_id", "unknown"),
            "player_id": manifest.get("player_id", "unknown"),
            "label": label,
            "cheat_type": manifest.get("cheat_type"),
            "threshold": threshold,
            "max_score": None,
            "result": "EXCLUDED",
            "cheat_start_ms": cheat_start,
            "cheat_end_ms": cheat_end,
            "cheat_windows_ms": cheat_windows,
            "first_detection_ms": None,
            "last_detection_ms": None,
            "detection_latency_ms": None,
            "post_off_detection_count": 0,
            "first_post_off_detection_ms": None,
            "last_post_off_detection_ms": None,
            "post_off_duration_ms": None,
            "excluded_event_count": excluded_event_count,
            "window_mode": "excluded",
            "scope_mode": scope_mode,
            "cheat_open_ended": cheat_open_ended,
            "post_off_censored": post_off_censored,
        }

    max_score = max(
        (event.get("raw_score", 0) for event in numeric_events),
        default=0,
    )

    detected_events = [
        event for event in numeric_events
        if event.get("raw_score", 0) >= threshold
    ]

    last_detection = (
        max(event["timestamp_ms"] for event in detected_events)
        if detected_events else None
    )

    post_off_detection_count = 0
    first_post_off_detection = None
    last_post_off_detection = None
    post_off_duration = None

    if label == "CHEAT" and cheat_end is not None and not cheat_open_ended:
        post_off_detections = [
            event for event in detected_events
            if event["timestamp_ms"] > cheat_end
        ]

        post_off_detection_count = len(post_off_detections)

        if post_off_detections:
            first_post_off_detection = min(
                event["timestamp_ms"] for event in post_off_detections
            )
            last_post_off_detection = max(
                event["timestamp_ms"] for event in post_off_detections
            )
            post_off_duration = last_post_off_detection - cheat_end
        else:
            post_off_duration = 0

    if label == "CHEAT":
        # 활성화 구간을 모르면 전체 세션을 평가 대상으로 사용한다.
        if not cheat_windows:
            valid_detections = detected_events
            window_mode = "whole_session"
        else:
            window_mode = (
                "labeled_window" if len(cheat_windows) == 1
                else "labeled_windows"
            )
            valid_detections = [
                event for event in detected_events
                if event_in_cheat_windows(event["timestamp_ms"], cheat_windows)
            ]

        if valid_detections:
            first_detection = min(
                event["timestamp_ms"] for event in valid_detections
            )

            if cheat_windows:
                matching_window_start = window_start_for_timestamp(
                    first_detection,
                    cheat_windows,
                )
                latency = (
                    first_detection - matching_window_start
                    if matching_window_start is not None
                    else None
                )
            else:
                latency = None

            result = "TP"
        else:
            first_detection = None
            latency = None
            result = "FN"

    elif label == "NORMAL":
        window_mode = "normal_session"
        if detected_events:
            first_detection = min(
                event["timestamp_ms"] for event in detected_events
            )
            latency = None
            result = "FP"
        else:
            first_detection = None
            latency = None
            result = "TN"

    else:
        window_mode = "unknown_label"
        first_detection = None
        latency = None
        result = "UNKNOWN"

    return {
        "session_id": manifest.get("session_id", "unknown"),
        "player_id": manifest.get("player_id", "unknown"),
        "label": label,
        "cheat_type": manifest.get("cheat_type"),
        "threshold": threshold,
        "max_score": max_score,
        "result": result,
        "cheat_start_ms": cheat_start,
        "cheat_end_ms": cheat_end,
        "first_detection_ms": first_detection,
        "last_detection_ms": last_detection,
        "detection_latency_ms": latency,
        "post_off_detection_count": post_off_detection_count,
        "first_post_off_detection_ms": first_post_off_detection,
        "last_post_off_detection_ms": last_post_off_detection,
        "post_off_duration_ms": post_off_duration,
        "excluded_event_count": excluded_event_count,
        "window_mode": window_mode,
        "scope_mode": scope_mode,
        "cheat_open_ended": cheat_open_ended,
        "post_off_censored": post_off_censored,
    }


def summarize(results):
    """여러 세션 결과를 하나의 threshold 결과로 요약."""
    counts = defaultdict(int)
    latencies = []
    post_off_durations = []
    post_off_sessions = 0
    post_off_detection_count = 0
    censored_post_off_sessions = 0
    open_ended_sessions = 0
    excluded_event_count = 0

    for result in results:
        counts[result["result"]] += 1
        excluded_event_count += result.get("excluded_event_count", 0)

        latency = result.get("detection_latency_ms")
        if latency is not None:
            latencies.append(latency)

        if result.get("cheat_open_ended"):
            open_ended_sessions += 1

        count = result.get("post_off_detection_count", 0)
        if count > 0:
            post_off_sessions += 1
            post_off_detection_count += count

            duration = result.get("post_off_duration_ms")
            if result.get("post_off_censored"):
                # 실제 종료 시점을 관측하지 못한 하한값이므로 일반 평균에서 제외한다.
                censored_post_off_sessions += 1
            elif duration is not None:
                post_off_durations.append(duration)

    tp = counts["TP"]
    fp = counts["FP"]
    fn = counts["FN"]
    tn = counts["TN"]
    excluded_sessions = counts["EXCLUDED"]
    out_of_scope_sessions = counts["OUT_OF_SCOPE"]
    advisory_sessions = counts["ADVISORY"]
    unknown_sessions = counts["UNKNOWN"]

    cheat_sessions = tp + fn
    normal_sessions = fp + tn

    detection_rate = None
    false_positive_rate = None
    false_negative_rate = None
    avg_latency = None
    avg_post_off_duration = None

    if cheat_sessions > 0:
        detection_rate = tp / cheat_sessions * 100
        false_negative_rate = fn / cheat_sessions * 100

    if normal_sessions > 0:
        false_positive_rate = fp / normal_sessions * 100

    if latencies:
        avg_latency = sum(latencies) / len(latencies)

    if post_off_durations:
        avg_post_off_duration = sum(post_off_durations) / len(post_off_durations)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "excluded_sessions": excluded_sessions,
        "out_of_scope_sessions": out_of_scope_sessions,
        "advisory_sessions": advisory_sessions,
        "unknown_sessions": unknown_sessions,
        "excluded_event_count": excluded_event_count,
        "detection_rate": detection_rate,
        "false_positive_rate": false_positive_rate,
        "false_negative_rate": false_negative_rate,
        "avg_latency_ms": avg_latency,
        "post_off_sessions": post_off_sessions,
        "post_off_detection_count": post_off_detection_count,
        "censored_post_off_sessions": censored_post_off_sessions,
        "open_ended_sessions": open_ended_sessions,
        "avg_post_off_duration_ms": avg_post_off_duration,
    }


def format_percent(value):
    return "N/A" if value is None else f"{value:.1f}%"


def format_latency(value):
    return "-" if value is None else f"{value / 1000:.2f}s"


def format_threshold(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def analyze_module(module, sessions):
    """module 하나에 대해 threshold 성능과 Post-OFF를 계산."""
    scores = numeric_scores(sessions)
    thresholds = build_threshold_candidates(scores)

    if scores:
        min_score = min(scores)
        max_score = max(scores)
    else:
        min_score = 0
        max_score = 0

    cheat_count = sum(
        1 for s in sessions
        if str(s["manifest"].get("label", "")).upper() == "CHEAT"
    )
    normal_count = sum(
        1 for s in sessions
        if str(s["manifest"].get("label", "")).upper() == "NORMAL"
    )
    in_scope_cheat_count = sum(
        1 for s in sessions
        if str(s["manifest"].get("label", "")).upper() == "CHEAT"
        and cheat_session_in_scope(module, s["manifest"])
    )
    out_of_scope_cheat_count = cheat_count - in_scope_cheat_count
    scope_mode = module_scope_mode(module)

    print()
    print("=" * 92)
    print(f"MODULE: {module}")
    print(
        f"Sessions: {len(sessions)} "
        f"(CHEAT={cheat_count}, NORMAL={normal_count}, "
        f"IN-SCOPE CHEAT={in_scope_cheat_count}, OOS CHEAT={out_of_scope_cheat_count})"
    )
    print(f"Evaluation scope: {scope_mode}")
    if scope_mode == "TARGETED":
        targets = sorted(module_target_types(module) or [])
        print("Target cheat types: " + ", ".join(targets))
    elif scope_mode == "ADVISORY":
        print("TP/FP/FN/TN 제외: 현재 NORMAL/CHEAT 라벨만으로 이 모듈의 정답을 정의하기 어려움")
    print(
        f"Observed raw_score range: "
        f"{format_threshold(min_score)} ~ {format_threshold(max_score)}"
    )
    print(
        "Threshold candidates: "
        + ", ".join(format_threshold(threshold) for threshold in thresholds)
    )
    print("=" * 78)
    print()

    header = (
        f"{'Threshold':<12}"
        f"{'TP':<5}"
        f"{'FP':<5}"
        f"{'FN':<5}"
        f"{'TN':<5}"
        f"{'EXCL':<6}"
        f"{'OOS':<6}"
        f"{'ADV':<6}"
        f"{'Detect Rate':<14}"
        f"{'FP Rate':<12}"
        f"{'FN Rate':<12}"
        f"{'Avg Latency':<14}"
        f"{'PostOFF Sess':<14}"
        f"{'Avg PostOFF'}"
    )

    print(header)
    print("-" * len(header))

    comparison_rows = []
    detail_rows = []

    for threshold in thresholds:
        results = [
            analyze_session(
                module,
                session["manifest"],
                session["events"],
                threshold,
            )
            for session in sessions
        ]

        summary = summarize(results)

        comparison_rows.append({
            "module": module,
            "threshold": threshold,
            "tp": summary["tp"],
            "fp": summary["fp"],
            "fn": summary["fn"],
            "tn": summary["tn"],
            "excluded_sessions": summary["excluded_sessions"],
            "out_of_scope_sessions": summary["out_of_scope_sessions"],
            "advisory_sessions": summary["advisory_sessions"],
            "unknown_sessions": summary["unknown_sessions"],
            "scope_mode": scope_mode,
            "excluded_event_count": summary["excluded_event_count"],
            "detection_rate": summary["detection_rate"],
            "false_positive_rate": summary["false_positive_rate"],
            "false_negative_rate": summary["false_negative_rate"],
            "avg_latency_ms": summary["avg_latency_ms"],
            "post_off_sessions": summary["post_off_sessions"],
            "post_off_detection_count": summary["post_off_detection_count"],
            "censored_post_off_sessions": summary["censored_post_off_sessions"],
            "open_ended_sessions": summary["open_ended_sessions"],
            "avg_post_off_duration_ms": summary["avg_post_off_duration_ms"],
        })

        for result in results:
            if result["label"] == "CHEAT":
                detail_rows.append({
                    "module": module,
                    "session_id": result["session_id"],
                    "player_id": result["player_id"],
                    "cheat_type": result["cheat_type"],
                    "scope_mode": result.get("scope_mode"),
                    "threshold": threshold,
                    "result": result["result"],
                    "window_mode": result["window_mode"],
                    "cheat_start_ms": result["cheat_start_ms"],
                    "cheat_end_ms": result["cheat_end_ms"],
                    "cheat_windows_ms": json.dumps(
                        result.get("cheat_windows_ms", []),
                        ensure_ascii=False,
                    ),
                    "first_detection_ms": result["first_detection_ms"],
                    "last_detection_ms": result["last_detection_ms"],
                    "detection_latency_ms": result["detection_latency_ms"],
                    "post_off_detection_count": result["post_off_detection_count"],
                    "first_post_off_detection_ms": result["first_post_off_detection_ms"],
                    "last_post_off_detection_ms": result["last_post_off_detection_ms"],
                    "post_off_duration_ms": result["post_off_duration_ms"],
                    "post_off_censored": result.get("post_off_censored", False),
                    "cheat_open_ended": result.get("cheat_open_ended", False),
                    "excluded_event_count": result["excluded_event_count"],
                })

        print(
            f"{format_threshold(threshold):<12}"
            f"{summary['tp']:<5}"
            f"{summary['fp']:<5}"
            f"{summary['fn']:<5}"
            f"{summary['tn']:<5}"
            f"{summary['excluded_sessions']:<6}"
            f"{summary['out_of_scope_sessions']:<6}"
            f"{summary['advisory_sessions']:<6}"
            f"{format_percent(summary['detection_rate']):<14}"
            f"{format_percent(summary['false_positive_rate']):<12}"
            f"{format_percent(summary['false_negative_rate']):<12}"
            f"{format_latency(summary['avg_latency_ms']):<14}"
            f"{summary['post_off_sessions']:<14}"
            f"{format_latency(summary['avg_post_off_duration_ms'])}"
        )

    cheat_sessions = [
        s for s in sessions
        if str(s["manifest"].get("label", "")).upper() == "CHEAT"
    ]
    if cheat_sessions:
        print()
        print("CHEAT session detail")
        print("-" * 78)
        for threshold in thresholds:
            for session in cheat_sessions:
                result = analyze_session(
                    module,
                    session["manifest"],
                    session["events"],
                    threshold,
                )

                start_text = (
                    f"{result['cheat_start_ms']}ms"
                    if result["cheat_start_ms"] is not None
                    else "N/A"
                )
                end_text = (
                    f"{result['cheat_end_ms']}ms"
                    if result["cheat_end_ms"] is not None
                    else "N/A"
                )

                if result.get("cheat_open_ended"):
                    post_off_text = "N/A(open-ended)"
                elif (
                    result.get("post_off_censored")
                    and result.get("post_off_detection_count", 0) > 0
                    and result["post_off_duration_ms"] is not None
                ):
                    post_off_text = f">={format_latency(result['post_off_duration_ms'])} (censored)"
                else:
                    post_off_text = format_latency(result["post_off_duration_ms"])

                print(
                    f"threshold={format_threshold(threshold):<6} "
                    f"session={result['session_id']:<16} "
                    f"cheat={str(result.get('cheat_type')):<18} "
                    f"result={result['result']:<12} "
                    f"window={result['window_mode']:<14} "
                    f"ON={start_text:<10} OFF={end_text:<10} "
                    f"first={result['first_detection_ms']} "
                    f"last={result['last_detection_ms']} "
                    f"latency={format_latency(result['detection_latency_ms'])} "
                    f"post_off_count={result['post_off_detection_count']} "
                    f"post_off={post_off_text}"
                )

    return comparison_rows, detail_rows


def save_threshold_csv(output_dir, rows):
    """threshold 비교 결과를 CSV로 저장."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "threshold_comparison.csv"

    fieldnames = [
        "module",
        "threshold",
        "tp",
        "fp",
        "fn",
        "tn",
        "excluded_sessions",
        "out_of_scope_sessions",
        "advisory_sessions",
        "unknown_sessions",
        "scope_mode",
        "excluded_event_count",
        "detection_rate",
        "false_positive_rate",
        "false_negative_rate",
        "avg_latency_ms",
        "post_off_sessions",
        "post_off_detection_count",
        "censored_post_off_sessions",
        "open_ended_sessions",
        "avg_post_off_duration_ms",
    ]

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def save_post_off_csv(output_dir, rows):
    """CHEAT 세션별 탐지/Post-OFF 상세 결과를 CSV로 저장."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "post_off_analysis.csv"

    fieldnames = [
        "module",
        "session_id",
        "player_id",
        "cheat_type",
        "scope_mode",
        "threshold",
        "result",
        "window_mode",
        "cheat_start_ms",
        "cheat_end_ms",
        "cheat_windows_ms",
        "first_detection_ms",
        "last_detection_ms",
        "detection_latency_ms",
        "post_off_detection_count",
        "first_post_off_detection_ms",
        "last_post_off_detection_ms",
        "post_off_duration_ms",
        "post_off_censored",
        "cheat_open_ended",
        "excluded_event_count",
    ]

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def main():
    repo_root, replay_data = find_repo_root_and_replay_data()

    print(f"Replay Data: {replay_data}")

    sessions = find_sessions(replay_data)
    if not sessions:
        print("분석할 세션이 없습니다.")
        return

    modules = defaultdict(list)
    for session in sessions:
        modules[session["module"]].append(session)

    all_comparison_rows = []
    all_post_off_rows = []

    for module in sorted(modules.keys()):
        comparison_rows, post_off_rows = analyze_module(
            module,
            modules[module],
        )
        all_comparison_rows.extend(comparison_rows)
        all_post_off_rows.extend(post_off_rows)

    output_dir = repo_root / OUTPUT_DIR_NAME

    threshold_csv_path = save_threshold_csv(
        output_dir,
        all_comparison_rows,
    )

    post_off_csv_path = save_post_off_csv(
        output_dir,
        all_post_off_rows,
    )

    print()
    print("=" * 78)
    print("분석 완료")
    print(f"Threshold 비교 결과 저장: {threshold_csv_path}")
    print(f"CHEAT 상세/Post-OFF 결과 저장: {post_off_csv_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()
