"""Read-only explanation of the existing Scoring policy for Dashboard.

The projection uses the supplied Scoring reader and the same pure B builders.
It does not configure a store, sum detector scores, change thresholds, or issue
a replacement verdict. A retained incident is a representative original Event,
not the newest zero-point status snapshot and not a client-side inference.
"""

from dataclasses import asdict, replace

from server.scoring.aggregate import build_aggregate_evidence
from server.scoring.aggregate_risk import build_aggregate_risk
from server.scoring.autopaint_history import summarize_autopaint_history
from server.scoring.calibration import get_external_access_calibration
from server.scoring.external_access_summary import summarize_external_access_history
from server.scoring.fusion import build_fusion_plan
from server.scoring.history_summary import summarize_godmode_history
from server.scoring.risk_input import build_player_risk_input
from server.scoring.storage import EXTERNAL_ACCESS_SUBMODULES


def _history(reader, session_id, player_id, module, submodule=None):
    method_name = (
        "get_external_access_history" if module == "external_access"
        else "get_event_delta_history" if module == "godmode"
        else "get_snapshot_history"
    )
    method = getattr(reader, method_name, None)
    if not callable(method):
        return None
    cursor, rows = 0, []
    while True:
        args = (session_id, player_id, submodule) if submodule else (session_id, player_id)
        kwargs = {"after_sequence": cursor, "limit": 1000}
        if not submodule:
            kwargs["module"] = module
        batch = method(*args, **kwargs)
        if not batch:
            return rows
        for item in batch:
            if (
                item.session_id != session_id or item.player_id != player_id
                or item.module != module
                or (submodule is not None and item.submodule != submodule)
            ):
                raise RuntimeError("Scoring explanation history identity mismatch")
        next_cursor = batch[-1].sequence
        if next_cursor <= cursor:
            raise RuntimeError("Scoring explanation history did not advance")
        rows.extend(batch)
        cursor = next_cursor
        if len(batch) < 1000:
            return rows


def _representative(items):
    return max(items, key=lambda item: (item.raw_score, item.timestamp_ms, item.sequence), default=None)


def _event(item):
    return asdict(item) if item is not None else None


def _external_signal(summary, calibration):
    """Expose the same scoped conditions used by B's external-access resolver."""
    status, met = "UNRESOLVED", None
    if summary is not None and calibration is not None and calibration.calibrated:
        historical_met = (
            summary.submodule == "module_integrity"
            and summary.max_positive_raw_score is not None
            and calibration.meets_threshold(summary.max_positive_raw_score) is True
        )
        if historical_met:
            status = "ACTIVE"
        elif summary.latest_measurement_available is False:
            status = "UNAVAILABLE"
        elif summary.latest_measurement_available is True and summary.latest_raw_score is not None:
            met = calibration.meets_threshold(summary.latest_raw_score)
            status = "ACTIVE" if met is True else "INACTIVE" if met is False else "UNRESOLVED"
        if summary.latest_measurement_available is True and summary.latest_raw_score is not None:
            met = calibration.meets_threshold(summary.latest_raw_score)
    return {
        "status": status,
        "raw_score": summary.latest_raw_score if summary is not None else None,
        "calibration_threshold": calibration.threshold if calibration is not None else None,
        "calibration_mode": calibration.mode if calibration is not None else None,
        "calibration_version": calibration.version if calibration is not None else None,
        "threshold_met": met,
        "history_resolved": status in ("ACTIVE", "INACTIVE"),
        "history_total_events": summary.total_events if summary is not None else None,
    }


def build_evidence_projection(reader, policy_snapshot):
    session_id, player_id = policy_snapshot.session_id, policy_snapshot.player_id
    modules = {entry.state.module: entry.state for entry in policy_snapshot.modules}
    risk_input = build_player_risk_input(policy_snapshot)
    histories, external_summaries = {}, None
    autopaint_summary = godmode_summary = None

    if "autopaint" in modules:
        histories["autopaint"] = _history(reader, session_id, player_id, "autopaint")
        if histories["autopaint"] is not None:
            autopaint_summary = summarize_autopaint_history(histories["autopaint"], session_id=session_id, player_id=player_id)
    if "godmode" in modules:
        histories["godmode"] = _history(reader, session_id, player_id, "godmode")
        if histories["godmode"] is not None:
            godmode_summary = summarize_godmode_history(histories["godmode"], session_id=session_id, player_id=player_id)
    if "external_access" in modules:
        scoped_histories = {
            scope: _history(reader, session_id, player_id, "external_access", scope)
            for scope in EXTERNAL_ACCESS_SUBMODULES
        }
        histories["external_access"] = scoped_histories
        # Absence of a history reader is not an empty/healthy observation.
        if all(items is not None for items in scoped_histories.values()):
            external_summaries = {
                scope: summarize_external_access_history(items, session_id=session_id, player_id=player_id, submodule=scope)
                for scope, items in scoped_histories.items()
            }

    # AutoPaint is a snapshot producer but its session risk is history-aware.
    # A compatibility reader without history cannot certify that a latest zero
    # erased earlier session evidence. Keep that missing coverage deferred.
    if "autopaint" in modules and histories.get("autopaint") is None:
        risk_input = replace(risk_input, signals=tuple(
            replace(signal, requires_event_history=True) if signal.module == "autopaint" else signal
            for signal in risk_input.signals
        ))

    aggregate = build_aggregate_evidence(
        risk_input, autopaint_history=autopaint_summary,
        godmode_history=godmode_summary, external_access_summaries=external_summaries,
    )
    risk = build_aggregate_risk(build_fusion_plan(aggregate))
    explanations = []
    for signal in aggregate.signals:
        module, latest = signal.module, modules[signal.module]
        if module == "external_access":
            for scope in EXTERNAL_ACCESS_SUBMODULES:
                items = histories[module][scope]
                summary = external_summaries.get(scope) if external_summaries is not None else None
                calibration = get_external_access_calibration(scope)
                scoped_latest = max(items, key=lambda item: (item.timestamp_ms, item.sequence), default=None) if items is not None else None
                qualifying = []
                if scope == "module_integrity" and items is not None and calibration is not None:
                    qualifying = [
                        item for item in items
                        if summarize_external_access_history([item], session_id=session_id, player_id=player_id, submodule=scope).available_events > 0
                        and calibration.meets_threshold(item.raw_score) is True
                    ]
                scoped_signal = _external_signal(summary, calibration)
                explanations.append({
                    "module": module, "submodule": scope, "signal": scoped_signal,
                    "latest_event": _event(scoped_latest),
                    "retained_incident_event": _event(_representative(qualifying)) if scoped_signal["status"] == "ACTIVE" else None,
                })
            continue

        retained = None
        items = histories.get(module)
        if signal.status == "ACTIVE" and items is not None:
            if module == "autopaint":
                qualifying = [item for item in items if summarize_autopaint_history([item], session_id=session_id, player_id=player_id).positive_seen]
            elif module == "godmode":
                qualifying = [item for item in items if summarize_godmode_history([item], session_id=session_id, player_id=player_id).qualifying_events > 0]
            else:
                qualifying = []
            retained = _representative(qualifying)
        explanations.append({
            "module": module,
            "submodule": latest.evidence.get("submodule") if isinstance(latest.evidence.get("submodule"), str) else None,
            "signal": asdict(signal), "latest_event": _event(latest),
            "retained_incident_event": _event(retained),
        })

    # Operational-only modules are deliberately absent from aggregate.signals.
    return {"aggregate_evidence": asdict(aggregate), "aggregate_risk": asdict(risk), "module_evidence": explanations}
