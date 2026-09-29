import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

# repo 루트의 shared 패키지를 import할 수 있게 한다.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.config import ClientConfig
from shared.errors import SharedError
from shared.logger import (
    configure_client,
    flush_client,
    send_detection,
    shutdown_client,
)

# =============================================
# 기본 설정
# =============================================

DEFAULT_LOG_FILE = "noclip_log.csv"
DEFAULT_RESULT_FILE = "detection_results.csv"
DEFAULT_EVENT_FILE = "events.jsonl"

MODULE_NAME = "noclip"

# Collision이 5초 이상 꺼져 있으면 이상 상태로 판단
COLLISION_OFF_THRESHOLD_MS = 5000

# 탐지 후 5초 동안 SUSPICIOUS 상태 유지
DETECTION_HOLD_MS = 5000

# 기존 Noclip Detector의 탐지 기준
SUSPICIOUS_SCORE = 3

collision_off_start = None
suspicious_until = 0


# =============================================
# Noclip 탐지
# =============================================

def detect_noclip(current):
    global collision_off_start
    global suspicious_until

    score = 0
    reasons = []

    current_time = int(current["elapsed_ms"])
    collision = int(current["collision"])
    blocked_path = int(current["blocked_path"])

    # 1. Collision OFF 검사
    if collision == 0:
        score += 1
        reasons.append("Collision Disabled")

        if collision_off_start is None:
            collision_off_start = current_time
    else:
        collision_off_start = None

    # 2. Collision OFF 지속시간 검사
    if collision_off_start is not None:
        collision_off_duration = current_time - collision_off_start

        if collision_off_duration >= COLLISION_OFF_THRESHOLD_MS:
            score += 2
            reasons.append("Collision Disabled Too Long")

    # 3. 벽 통과 검사
    if blocked_path == 1:
        score += 2
        reasons.append("Blocked Path Detected")

    # 4. 기존 Detector 최종 판정
    if score >= SUSPICIOUS_SCORE:
        result = "SUSPICIOUS"
        suspicious_until = current_time + DETECTION_HOLD_MS
    elif current_time < suspicious_until:
        result = "SUSPICIOUS"
        reasons.append("Recent Noclip Detection")
    else:
        result = "NORMAL"

    return {
        "time": current_time,
        "score": score,
        "reasons": reasons,
        "result": result,
        "collision": collision,
        "blocked_path": blocked_path,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Realtime Noclip detector")
    parser.add_argument("--session-id", default=os.environ.get("GZZ_SESSION_ID", "normal_001"))
    parser.add_argument("--player-id", default=os.environ.get("GZZ_PLAYER_ID", "player_001"))
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE)
    parser.add_argument("--result-file", default=DEFAULT_RESULT_FILE)
    parser.add_argument("--event-file", default=DEFAULT_EVENT_FILE)
    parser.add_argument("--poll-interval", type=float, default=0.2)
    return parser.parse_args()


def prepare_output_file(path):
    path = Path(path)
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_common_event(event_file, event):
    with event_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def write_detection_header(result_file):
    with result_file.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "event_id",
                "start_ms",
                "end_ms",
                "duration_ms",
                "max_score",
                "reasons",
            ],
        )
        writer.writeheader()


def append_detection_event(result_file, event_id, event):
    with result_file.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "event_id",
                "start_ms",
                "end_ms",
                "duration_ms",
                "max_score",
                "reasons",
            ],
        )
        writer.writerow(
            {
                "event_id": event_id,
                "start_ms": event["start_time"],
                "end_ms": event["end_time"],
                "duration_ms": event["end_time"] - event["start_time"],
                "max_score": event["max_score"],
                "reasons": " | ".join(sorted(event["reasons"])),
            }
        )


def stream_csv_rows(log_file, poll_interval):
    """CSV에 이미 있는 행부터 읽고, 이후 새로 추가되는 행을 계속 yield한다."""
    log_file = Path(log_file)

    while True:
        if not log_file.exists() or log_file.stat().st_size == 0:
            time.sleep(poll_interval)
            continue

        try:
            with log_file.open("r", encoding="utf-8-sig", newline="") as file:
                # DictReader는 내부 iterator를 한 번 거친 뒤 tell()과 섞어 쓰면
                # "telling position disabled by next() call" 오류가 날 수 있다.
                # 그래서 헤더와 데이터 행을 readline()으로 직접 읽는다.
                header_line = file.readline()

                if not header_line:
                    time.sleep(poll_interval)
                    continue

                fieldnames = next(csv.reader([header_line]))

                if not fieldnames:
                    time.sleep(poll_interval)
                    continue

                while True:
                    position = file.tell()
                    line = file.readline()

                    if line:
                        if not line.strip():
                            continue

                        values = next(csv.reader([line]))

                        if len(values) != len(fieldnames):
                            print("[NoclipDetector] incomplete CSV row skipped")
                            continue

                        yield dict(zip(fieldnames, values))
                        continue

                    # Lua가 파일을 새로 만들거나 truncate한 경우 다시 연다.
                    try:
                        current_stat = log_file.stat()
                        open_stat = os.fstat(file.fileno())

                        replaced = (
                            getattr(current_stat, "st_ino", None)
                            != getattr(open_stat, "st_ino", None)
                        )
                        truncated = current_stat.st_size < position
                    except OSError:
                        break

                    if replaced or truncated:
                        print("[NoclipDetector] log file reset detected; reopening")
                        break

                    time.sleep(poll_interval)

        except (OSError, UnicodeError, csv.Error) as exc:
            print(f"[NoclipDetector] log read error: {exc}")
            time.sleep(poll_interval)


def configure_telemetry():
    """shared client를 한 번 설정한다. 실패해도 로컬 탐지는 계속한다."""
    try:
        configure_client(ClientConfig.from_env())
        print("[Telemetry] shared client configured")
        return True
    except SharedError as exc:
        print(
            "[Telemetry] shared client unavailable; "
            f"local detection continues ({type(exc).__name__}: {exc})"
        )
        return False


def main():
    args = parse_args()

    log_file = Path(args.log_file)
    result_file = prepare_output_file(args.result_file)
    event_file = prepare_output_file(args.event_file)

    # 기존 실행처럼 새 세션 시작 시 결과 파일을 새로 만든다.
    event_file.write_text("", encoding="utf-8")
    write_detection_header(result_file)

    telemetry_enabled = configure_telemetry()

    session_start_ms = None
    active_event = None
    detection_event_id = 0
    total_samples = 0

    print("===== Realtime Noclip Detector =====")
    print(f"Session ID : {args.session_id}")
    print(f"Player ID  : {args.player_id}")
    print(f"Log file   : {log_file}")
    print(f"Event file : {event_file}")
    print(f"Result file: {result_file}")
    print("Waiting for NoclipLogger samples...")

    try:
        for row in stream_csv_rows(log_file, args.poll_interval):
            try:
                result = detect_noclip(row)
            except (KeyError, TypeError, ValueError) as exc:
                print(f"[NoclipDetector] invalid row skipped: {exc}")
                continue

            total_samples += 1
            current_time = result["time"]

            # 기존 detector와 동일하게 첫 샘플을 세션 timestamp 0으로 사용한다.
            if session_start_ms is None:
                session_start_ms = current_time

            timestamp_ms = current_time - session_start_ms

            raw_reasons = [
                reason
                for reason in result["reasons"]
                if reason != "Recent Noclip Detection"
            ]

            # 기존 공통 7필드 Event 형식 유지
            common_event = {
                "session_id": args.session_id,
                "player_id": args.player_id,
                "module": MODULE_NAME,
                "timestamp_ms": timestamp_ms,
                "evidence": {
                    "collision": result["collision"],
                    "blocked_path": result["blocked_path"],
                },
                "reasons": raw_reasons,
                "raw_score": result["score"],
            }

            # 기존 로컬 JSONL 기록 유지
            write_common_event(event_file, common_event)

            # shared 중앙 전송 추가. raw_score 0 이벤트도 그대로 전송한다.
            if telemetry_enabled:
                try:
                    receipt = send_detection(common_event)
                    print(
                        f"[Telemetry] queued event_id={receipt.event_id} "
                        f"status={receipt.status}"
                    )
                except SharedError as exc:
                    # 중앙 전송 실패가 detector 자체를 종료시키지 않도록 한다.
                    print(f"[Telemetry] send failed: {type(exc).__name__}: {exc}")

            # 기존 Detection Event 집계 로직 유지
            if result["result"] == "SUSPICIOUS":
                if active_event is None:
                    active_event = {
                        "start_time": current_time,
                        "end_time": current_time,
                        "max_score": result["score"],
                        "reasons": set(),
                    }
                else:
                    active_event["end_time"] = current_time
                    if result["score"] > active_event["max_score"]:
                        active_event["max_score"] = result["score"]

                for reason in result["reasons"]:
                    if reason != "Recent Noclip Detection":
                        active_event["reasons"].add(reason)
            elif active_event is not None:
                detection_event_id += 1
                append_detection_event(
                    result_file,
                    detection_event_id,
                    active_event,
                )
                print(
                    f"[Detection {detection_event_id}] "
                    f"{active_event['start_time']}ms ~ {active_event['end_time']}ms "
                    f"max_score={active_event['max_score']}"
                )
                active_event = None

            print(
                f"[Sample {total_samples}] "
                f"T={timestamp_ms}ms "
                f"score={result['score']} "
                f"result={result['result']}"
            )

    except KeyboardInterrupt:
        print("\n[NoclipDetector] stopping...")

    finally:
        # 마지막까지 SUSPICIOUS인 경우 기존 결과 형식으로 저장한다.
        if active_event is not None:
            detection_event_id += 1
            append_detection_event(
                result_file,
                detection_event_id,
                active_event,
            )

        if telemetry_enabled:
            try:
                delivered = flush_client(timeout=3)
                print(f"[Telemetry] flush delivered={delivered}")
            except SharedError as exc:
                print(f"[Telemetry] flush failed: {type(exc).__name__}: {exc}")

            try:
                stopped = shutdown_client(timeout=5)
                print(f"[Telemetry] shutdown stopped={stopped}")
            except SharedError as exc:
                print(f"[Telemetry] shutdown failed: {type(exc).__name__}: {exc}")

        print(f"Common events saved to: {event_file}")
        print(f"Detection results saved to: {result_file}")


if __name__ == "__main__":
    main()
