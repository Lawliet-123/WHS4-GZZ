from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import CommonEvent


KNOWN_AUTOPAINT_BRIDGE_HASHES = {
    "d83cfe507728e287eb320a1038cc3f2ba8a79a00676a35f1a79a945208458fcf",
}

REQUIRED_AUTOPAINT_MARKERS = {
    "paint_full_route",
    "f10_mesh_first_paint",
    "ServerPaintBatch",
}


def _int(value: Any) -> int:
    return int(value) if value else 0


class AutoPaintDetector:
    """Score independent client-side indicators from one sensor snapshot."""

    module_name = "autopaint"

    def evaluate(
        self,
        snapshot: Mapping[str, Any],
        *,
        session_id: str,
        player_id: str,
    ) -> CommonEvent:
        integrity_valid = bool(snapshot.get("sensor_healthy", True)) and bool(snapshot.get("target", {}).get("found", True))
        modules = list(snapshot.get("modules", [])) if integrity_valid else []
        threads = list(snapshot.get("threads", [])) if integrity_valid else []
        endpoints = list(snapshot.get("tcp_endpoints", [])) if integrity_valid else []
        processes = list(snapshot.get("processes", [])) if integrity_valid else []
        artifacts = list(snapshot.get("artifacts", [])) if integrity_valid else []

        known_hash_paths: set[str] = set()
        marker_paths: set[str] = set()
        runtime_paths: set[str] = set()
        unsigned_user_paths: set[str] = set()

        for module in modules:
            path = str(module.get("path", "")).casefold()
            digest = str(module.get("sha256", "")).casefold()
            markers = set(module.get("markers", []))
            if digest in KNOWN_AUTOPAINT_BRIDGE_HASHES:
                known_hash_paths.add(path)
            if REQUIRED_AUTOPAINT_MARKERS.issubset(markers):
                marker_paths.add(path)
            if module.get("autopaint_runtime_path"):
                runtime_paths.add(path)
            if module.get("user_writable_path") and module.get("signature") == "untrusted":
                unsigned_user_paths.add(path)

        suspicious_module_paths = (
            known_hash_paths | marker_paths | runtime_paths | unsigned_user_paths
        )
        suspicious_threads = {
            _int(thread.get("thread_id"))
            for thread in threads
            if str(thread.get("origin_module", "")).casefold() in suspicious_module_paths
        }

        loopback_ports = {
            _int(endpoint.get("local_port"))
            for endpoint in endpoints
            if endpoint.get("local_ip") in {"127.0.0.1", "::1"}
        }
        matched_sidecars = {
            str(artifact.get("path", "")).casefold()
            for artifact in artifacts
            if _int(artifact.get("port")) in loopback_ports
            and artifact.get("kind") == "autopaint_port_sidecar"
        }
        runtime_artifacts = {
            str(artifact.get("path", "")).casefold()
            for artifact in artifacts
            if artifact.get("kind") == "autopaint_runtime_file"
        }

        injector_names = {
            "runtime-injector.exe",
        }
        controller_names = {
            "meccha-chameleon-litev2.exe",
        }
        injector_processes = {
            _int(process.get("pid"))
            for process in processes
            if str(process.get("name", "")).casefold() in injector_names
        }
        controller_processes = {
            _int(process.get("pid"))
            for process in processes
            if str(process.get("name", "")).casefold() in controller_names
        }

        evidence = {
            "known_bridge_hash": len(known_hash_paths),
            "autopaint_marker_set": len(marker_paths),
            "autopaint_runtime_module": len(runtime_paths),
            "unsigned_user_module": len(unsigned_user_paths),
            "loopback_tcp_endpoint": len(loopback_ports),
            "matched_port_sidecar": len(matched_sidecars),
            "suspicious_module_thread": len(suspicious_threads),
            "injector_process": len(injector_processes),
            "controller_process": len(controller_processes),
            "runtime_artifact": len(runtime_artifacts),
        }
        if "paint_observer" in snapshot:
            paint = snapshot["paint_observer"]
            evidence.update({
                "paint_collector_receiving": int(paint["state"] == "RECEIVING"),
                "paint_calls_observed": paint["calls"],
                "paint_registered_hooks": _int(paint["health"].get("registered_hooks")),
                "paint_dropped_records": _int(paint["health"].get("dropped")),
                "behavioral_scoring_enabled": 0,
            })

        reasons: list[str] = []
        score = 0

        if evidence["known_bridge_hash"]:
            score += 10
            reasons.append("Known AutoPaint Bridge Loaded")
        if evidence["autopaint_marker_set"]:
            score += 6
            reasons.append("AutoPaint Bridge Fingerprint Detected")
        if evidence["autopaint_runtime_module"]:
            score += 4
            reasons.append("AutoPaint Runtime Module Path Detected")
        if evidence["unsigned_user_module"]:
            score += 1
            reasons.append("Unsigned Module Loaded From User-Writable Path")
        if evidence["matched_port_sidecar"]:
            score += 4
            reasons.append("AutoPaint Port Sidecar Matched Game TCP Endpoint")
        if evidence["suspicious_module_thread"]:
            score += 3
            reasons.append("Thread Started Inside Suspicious Module")
        if evidence["injector_process"]:
            score += 3
            reasons.append("AutoPaint Injector Process Running")
        if evidence["controller_process"]:
            score += 2
            reasons.append("AutoPaint Controller Process Running")
        # A DLL left on disk from an earlier test is context, not current execution.

        if suspicious_module_paths and loopback_ports:
            score += 2
            reasons.append("Suspicious Module Correlated With Loopback IPC")

        behavior = snapshot.get("behavior_assessment")
        if behavior is not None:
            evidence.update(behavior["evidence"])
            evidence.update(integrity_score=score, integrity_valid=int(integrity_valid),
                            integrity_detected=int(integrity_valid and score >= 10))
            if behavior["valid"]:
                # Each channel independently reaches its own threshold; do not sum them.
                score = max(score, behavior["score"])
                reasons.extend(behavior["reasons"])

        return CommonEvent(
            session_id=session_id,
            player_id=player_id,
            module=self.module_name,
            timestamp_ms=_int(snapshot.get("timestamp_ms")),
            evidence=evidence,
            reasons=reasons,
            raw_score=score,
        )
