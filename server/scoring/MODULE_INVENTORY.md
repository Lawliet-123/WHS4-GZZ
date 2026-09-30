# Server B — Detector score inventory / policy gate (draft)

**Basis:** user-supplied `WHS4-GZZ-scoring-latest.zip` snapshot on 2026-09-30. This is a **code audit**, not a production calibration or confirmation that every detector is connected end-to-end. Existing B1 commit (`25e38da`) is left intact.

## Critical distinction: different `raw_score` meanings

The seven-field transport schema promises only a finite, nonnegative `raw_score` and a `module` string. It does **not** promise that a score is a current state, a cumulative counter, a probability, or that every detector transmits zero-point samples. `sequence` is server receipt order, not game time.

| Exact module sent to receiver | Implementation/source | Audited score shape | Central emission / meaning | B policy consequence |
|---|---|---|---|---|
| `noclip` | `client/detectors/noclip/main.py` | 0–5 (1 collision OFF, +2 sustained, +2 blocked path) | Only **positive** results sent; zero-point samples remain local | Latest positive event is **last observed evidence**, not proof of current active Noclip or of later cleanliness. Within-module raw fraction 0..100 may be previewed using source-code max 5, not used as final risk. |
| `aimbot` | `client/detectors/aimbot/detector/aimbot_detector.py`; `main.py` | 0–8 from 3+1+3+1 independent signals (the earlier 0–7 summary was incomplete) | Only positive reports sent; computes per current evaluated engagement/window | Retain positive signal without treating silence as all-clear. Preview max 8 is not a validated risk scale. |
| `godmode` | `client/detectors/godmode/main.py`; `detector/godmode_detector.py` | `raw_score = result.new_score`, **new incident points per snapshot**; internal `result.score` is separate cumulative session value | Sends **new events** rather than cumulative value | **B1 latest-state replacement loses event history.** Before player-level aggregation, B needs separate idempotent per-event history, event-time policy, and calibrated decay/retention or session-latched semantics. Do **not** normalize latest raw event as if it were cumulative or current state. |
| `autopaint` | `client/detectors/autopaint/gzz_anticheat/detector.py`; `behavior.py` | Source-code integrity upper bound 35 (=10+6+4+1+4+3+3+2+2); behavior upper bound 15 (=6+4+3+2); raw is **maximum**, not their sum | Sends each evaluated result, including 0; evidence exposes validity and each channel | Respect `integrity_valid`/`behavior_valid`; no behavior evidence is not a true zero. Raw 35 is code-derived upper bound, not empirical threshold; module's own 10-point channel thresholds are **not** a cross-detector verdict. |
| `hide_anywhere` | `client/detectors/Hide_anywhere_detector/mecha_detector_v9.py` | 0–3 (complete value pattern = 3; otherwise two one-point auxiliary signals) | Adapter documents sending every event including zero | Preview max 3. **Transport integration bug to fix in owning detector:** `server_bridge.py` currently passes `uuid.uuid4().hex + ':' + sequence` as `event_id`, while shared requires canonical UUID (`shared/client.py`/`shared/schema.py`). Real shared E2E requires correcting this, independent of B. |
| `external_access` | `client/LocalGuard/external_access/process_access/detector.py`; `access_rights.py` | 2+2+3 rights, max +2 signature and +1 suspicious path = source-derived upper bound **10** | Positive detections for **individual source process/handles**, `evidence.submodule=external_process` | `(session,player,module)` latest state is **insufficient** for multi-process/handle scoring: the last event could replace a more serious observation from another source. Need identity/correlation/expiry policy. **Exact module name is `external_access`, not `localguard`.** |
| `localguard_executable_hash` | `client/LocalGuard/input_signature/hash_monitor.py` | 0 or 1 known EXE SHA-256 | Positive sent; negative measurement remains local | Signature presence is not proof of active cheating. Coverage and process scope matter; no global 0–100 normalization implied. |
| `localguard_yara` | `client/LocalGuard/input_signature/yara_scanner.py`; current YARA rule metadata | 0 or 3 **in this snapshot** (max matched rule; not sum) | Positive sent; can scan multiple processes and scopes | Scope/coverage and test-only status required. A max-rule hit does not establish activity. New rules could change bound. |
| `filesystem`, `injection`, `value_tamper`, `overlay_hook` | `client/LocalGuard/memory_integrity/run_session.py` | LocalGuard's own 0–100 capped scoring model | Only positive shared events sent; local logs include zero/error/offline | Internal 20/60 thresholds are provisional LocalGuard interpretation, not verified B-wide thresholds. Different checks can inspect overlapping instrumentation. |
| `godmode_runtime`, `noclip_runtime`, `aimbot_runtime` | `client/LocalGuard/memory_integrity/run_session.py` and `detectors/*_runtime.py` | Same LocalGuard 0–100 capped model | Positive events only; runtime memory evidence | Separate module names from Python `godmode`/`noclip`/`aimbot`. Potential **correlated signals**: do not count as independent without validating evidence overlap. |
| `whistle`, `whistle_rpc` | `client/detectors/whistle-spoofing/main.py` delegates to memory-integrity runner | Same 0–100 capped model | Hook evidence vs RPC-log evidence; some scans may ERROR/OFFLINE locally | Two observation channels can corroborate one suspected cheat; avoid automatic double-counting. |
| `esp` | No ESP detector implementation in audited ZIP | **Unknown** | **Pending** | Unknown module must not crash receiver/B1, but no assumed max/weight/verdict until its owner provides code and tests. |

## Why B2 is split into two steps

**This patch = audited policy preview (B2a):** `policy.py` classifies emission semantics and computes a *within-module descriptive raw fraction* only for code-reviewed bounded signals (`noclip`, `aimbot`, `autopaint`, `hide_anywhere`). `RAW_FRACTION_ONLY` is **not** a calibrated risk score, confidence/probability, weighted total, or cheat verdict. Other profiles are explicitly marked `POLICY_NOT_CALIBRATED`. Unknown detectors are `UNKNOWN_MODULE`; ESP is `AWAITING_DETECTOR`; explicit ERROR/OFFLINE is `MEASUREMENT_UNAVAILABLE`; a changed/out-of-bound raw score requires review. A/C interfaces and B1's SQLite format remain unchanged.

**Follow-up = real scoring engine (B2b):** First agree with detector owners how to interpret missing zero events, time expiry, per-source grouping, GodMode event history, expected coverage, and correlated LocalGuard observations. Get normal and cheat replay sessions with aligned `t0` and verified ground truth. Then choose/test normalization and fusion rules, version their configs, and only after that expose a player-level risk and verdict to C. Do not sum the percentages generated by B2a.

## Team decisions / checks to unblock B2b

1. **Godmode:** Is the intended player status session-latched once a new reason occurs, or should points decay over time? Which event-history and distinct-reason rules should B keep? No policy should add every 0/positive sample as a new cheat incident.
2. **Positive-only feeds:** Who provides a reliable clear/expiry indication or central heartbeat? Absence of a positive result is not clean; latest positive signal is not proof it remains active.
3. **Correlated signals:** Agree how runtime LocalGuard and gameplay detectors reinforce/corroborate rather than blindly double-count the same attack.
4. **External access/YARA:** Agree grouping by source PID + creation time / scope and retention, rather than module-only last-value selection.
5. **ESP:** Have owner provide exact `module`, `raw_score` construction/max, event semantics, evidence validity, normal/cheat datasets and how central send works.
6. **Hide Anywhere:** Owner should verify canonical UUID `event_id` with actual shared client and server /api/detection (existing fake adapter tests do not catch invalid UUID).
7. **Deployment/coverage:** A/C must complete the actual `server/main.py` wiring and central E2E; `queued` only certifies the local client's outbox, not central arrival.

## Quick API (B2a)

```python
from server.scoring.main import get_player_signal_inventory

for signal in get_player_signal_inventory("session_1", "player_1"):
    print(signal.module, signal.state, signal.raw_fraction_pct, signal.issues)
```

No schema migration or detector rewrite is required to use this **inspection-only** API. It reads existing B1 latest states. For `godmode` and `external_access` those states are **not sufficient for full scoring** and this API reports the limitation.

## B2b-1 추가 점검(업로드된 병합 HEAD 스냅샷 기준)

- Godmode는 `result.new_reasons`가 있을 때만 Shared로 보내고,
  `raw_score=result.new_score`, `reasons=new_reasons`로 구성함을 재확인했다.
- B2b-1은 수신한 각 Godmode Event를 원본 7필드 값 그대로 별도 사건 이력으로
  보존한다. 동일 `event_id` 재전송은 이력에 중복 삽입하지 않는다.
- 단, detector가 재시작해 같은 이유를 새로운 `event_id`로 보내는 경우에는
  현재 공통 Event만으로 같은 실제 사건인지 단정할 수 없다.
- 이 단계는 **관측 이력 보존**이며, 중복 원인 통합·점수 감쇠·정규화·최종 판정
  공식을 확정하지 않는다. ESP의 실제 전송 규격도 추후 재검토한다.
