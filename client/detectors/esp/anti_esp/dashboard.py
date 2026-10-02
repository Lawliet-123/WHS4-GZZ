"""PyQt5 dashboard for the real-time Anti-ESP evidence monitor.

The formatting helpers in this module deliberately do not depend on Qt.  PyQt5
is imported only when :class:`DashboardWindow` is instantiated, so headless
collectors and test runners can import the rest of the package without PyQt5.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import importlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


STATUS_LEVELS = ("LOW", "REVIEW", "HIGH", "CRITICAL", "INSUFFICIENT")
EVENT_COLUMNS = ("timestamp", "category", "source", "access", "points", "reason")
SENSOR_NAMES = (
    "collector",
    "sysmon",
    "game",
    "handles",
    "overlay",
    "modules",
    "telemetry",
)

_MISSING = object()
_QT_WINDOW_CLASS: type | None = None


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read the first available name from a mapping or an attribute object."""

    if value is None:
        return default
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        candidate = getattr(value, name, _MISSING)
        if candidate is not _MISSING:
            return candidate
    return default


def _plain_text(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    return text if text else fallback


def normalize_percent(value: Any) -> float | None:
    """Return a finite percentage clamped to 0..100, or ``None``."""

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return min(100.0, max(0.0, number))


def format_metric(value: Any) -> str:
    """Format a score or observation-confidence value without probabilistic wording."""

    number = normalize_percent(value)
    if number is None:
        return "— / 100"
    if number.is_integer():
        return f"{int(number)} / 100"
    return f"{number:.1f} / 100"


def classify_status(suspicion: Any, observation_confidence: Any) -> str:
    """Fallback presentation status when the scoring engine supplied none.

    The scoring engine remains authoritative.  These transparent thresholds are
    only a UI fallback and do not trigger an enforcement action.
    """

    score = normalize_percent(suspicion)
    confidence = normalize_percent(observation_confidence)
    if score is None or confidence is None or confidence < 35.0:
        return "INSUFFICIENT"
    if score < 30.0:
        return "LOW"
    if score < 60.0:
        return "REVIEW"
    if score < 80.0:
        return "HIGH"
    return "CRITICAL"


def normalize_status(
    value: Any,
    suspicion: Any = None,
    observation_confidence: Any = None,
) -> str:
    """Normalize a controller status, falling back to transparent UI thresholds."""

    if isinstance(value, Enum):
        value = value.value
    status = str(value).strip().upper() if value is not None else ""
    aliases = {
        "MEDIUM": "REVIEW",
        "WARN": "REVIEW",
        "WARNING": "REVIEW",
        "UNKNOWN": "INSUFFICIENT",
        "NO_DATA": "INSUFFICIENT",
        "NOT_ENOUGH_DATA": "INSUFFICIENT",
    }
    status = aliases.get(status, status)
    if status in STATUS_LEVELS:
        return status
    return classify_status(suspicion, observation_confidence)


def format_timestamp(value: Any) -> str:
    """Format datetimes and Unix timestamps for the recent-event table."""

    if value is None:
        return "—"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            if not math.isfinite(number):
                return "—"
            # Accept millisecond Unix timestamps without affecting ordinary
            # second-resolution event timestamps.
            if abs(number) >= 100_000_000_000:
                number /= 1000.0
            parsed = datetime.fromtimestamp(number)
        except (OSError, OverflowError, ValueError):
            return _plain_text(value)
    else:
        return _plain_text(value)

    milliseconds = parsed.microsecond // 1000
    base = parsed.strftime("%Y-%m-%d %H:%M:%S")
    return f"{base}.{milliseconds:03d}" if milliseconds else base


def format_access(value: Any) -> str:
    """Format a Windows access mask while preserving already-labelled values."""

    if value is None:
        return "—"
    if isinstance(value, bool):
        return _plain_text(value)
    if isinstance(value, int):
        return f"0x{value:08X}"
    if isinstance(value, (list, tuple, set, frozenset)):
        parts = [format_access(item) for item in value]
        return ", ".join(part for part in parts if part != "—") or "—"
    return _plain_text(value)


def format_points(value: Any) -> str:
    """Format an evidence contribution; absent contributions are not inferred."""

    if value is None or isinstance(value, bool):
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _plain_text(value)
    if not math.isfinite(number):
        return "—"
    if number.is_integer():
        return f"{number:+.0f}"
    return f"{number:+.1f}"


def event_row(event: Any) -> tuple[str, str, str, str, str, str]:
    """Convert an EvidenceEvent-like value into the six dashboard columns."""

    details = _field(event, "details", default={})
    if not isinstance(details, Mapping):
        details = {}

    access = _field(event, "access", "granted_access", "access_mask", default=_MISSING)
    if access is _MISSING:
        access = _field(details, "access", "granted_access", "access_mask")

    points = _field(event, "points", "score_contribution", default=_MISSING)
    if points is _MISSING:
        points = _field(details, "points", "score_contribution", "score")

    source_process = _field(details, "source_image", "process_path", default=_MISSING)
    if source_process is _MISSING:
        source_process = _field(event, "source", "source_image", "process")

    return (
        format_timestamp(_field(event, "timestamp", "time", "created_at")),
        _plain_text(_field(event, "category", "type")),
        _plain_text(source_process),
        format_access(access),
        format_points(points),
        _plain_text(_field(event, "reason", "message", "description")),
    )


def _sensor_state(value: Any) -> tuple[str, str]:
    """Return ``(label, semantic_state)`` for a sensor-like value."""

    if value is None:
        return "UNKNOWN", "unknown"
    if isinstance(value, bool):
        return ("ONLINE", "online") if value else ("OFFLINE", "offline")

    # SysmonStatus and mapping variants use availability/enabled rather than a
    # generic status field.  Explicit unavailability wins over other labels.
    available = _field(value, "available", default=_MISSING)
    enabled = _field(value, "enabled", "running", "active", default=_MISSING)
    if available is False:
        return "UNAVAILABLE", "unavailable"
    if enabled is False:
        return "OFFLINE", "offline"
    if enabled is True and (available is True or available is _MISSING):
        return "ONLINE", "online"

    raw = _field(value, "status", "state", "health", "code", default=value)
    if isinstance(raw, Enum):
        raw = raw.value
    text = str(raw).strip().lower()
    if text in {"online", "running", "active", "ok", "healthy", "ready", "connected"}:
        return "ONLINE", "online"
    if text in {"waiting", "starting", "pending", "initializing"}:
        return "WAITING", "waiting"
    if text in {"offline", "stopped", "inactive", "disabled", "not_running"}:
        return "OFFLINE", "offline"
    if text in {"error", "failed", "fault", "unhealthy"}:
        return "ERROR", "error"
    if text in {"unavailable", "unsupported", "not_installed", "missing"}:
        return "UNAVAILABLE", "unavailable"
    if text and not isinstance(value, Mapping):
        return text.upper(), "unknown"
    return "UNKNOWN", "unknown"


def format_sensor_status(value: Any) -> str:
    """Return a stable, user-facing sensor status label."""

    return _sensor_state(value)[0]


def sensor_semantic_state(value: Any) -> str:
    """Return a stable semantic state used only for dashboard styling."""

    return _sensor_state(value)[1]


def snapshot_values(snapshot: Any) -> tuple[float | None, float | None, str]:
    """Extract score, observation confidence, and status from a snapshot."""

    suspicion = normalize_percent(
        _field(snapshot, "suspicion_score", "suspicion", "score")
    )
    confidence = normalize_percent(
        _field(snapshot, "observation_confidence", "confidence")
    )
    status = normalize_status(
        _field(snapshot, "status", "level", "state"), suspicion, confidence
    )
    return suspicion, confidence, status


def _load_qt() -> tuple[Any, Any]:
    """Load PyQt5 only for callers that actually construct the dashboard."""

    try:
        qt_core = importlib.import_module("PyQt5.QtCore")
        qt_widgets = importlib.import_module("PyQt5.QtWidgets")
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "The Anti-ESP dashboard requires PyQt5. "
            "Install it with 'python -m pip install PyQt5', or run the "
            "collector without constructing DashboardWindow."
        ) from exc
    return qt_core, qt_widgets


def _dashboard_window_class() -> type:
    global _QT_WINDOW_CLASS
    if _QT_WINDOW_CLASS is not None:
        return _QT_WINDOW_CLASS

    QtCore, QtWidgets = _load_qt()

    class _DashboardWindow(QtWidgets.QMainWindow):
        REFRESH_INTERVAL_MS = 500

        def __init__(self, controller: Any):
            super().__init__()
            self.controller = controller
            self._monitor_running = False
            self._last_error = ""

            self.setWindowTitle("Anti-ESP Evidence Monitor")
            self.resize(1180, 760)
            self.setMinimumSize(920, 620)
            self._build_ui()
            self._apply_theme()

            self.refresh_timer = QtCore.QTimer(self)
            self.refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
            self.refresh_timer.timeout.connect(self.refresh_view)
            self.refresh_timer.start()
            self.refresh_view()

        def _build_ui(self) -> None:
            root = QtWidgets.QWidget(self)
            root.setObjectName("pageRoot")
            self.setCentralWidget(root)
            page = QtWidgets.QVBoxLayout(root)
            page.setContentsMargins(22, 18, 22, 18)
            page.setSpacing(14)

            header = QtWidgets.QHBoxLayout()
            title_box = QtWidgets.QVBoxLayout()
            title = QtWidgets.QLabel("ANTI-ESP / EVIDENCE MONITOR")
            title.setObjectName("title")
            subtitle = QtWidgets.QLabel(
                "의심도는 근거 검토 우선순위이며 단독 판정값이 아닙니다."
            )
            subtitle.setObjectName("subtitle")
            title_box.addWidget(title)
            title_box.addWidget(subtitle)
            header.addLayout(title_box)
            header.addStretch(1)

            self.start_button = QtWidgets.QPushButton("시작")
            self.start_button.setObjectName("primaryButton")
            self.start_button.clicked.connect(self.start_monitor)
            self.stop_button = QtWidgets.QPushButton("중지")
            self.stop_button.clicked.connect(self.stop_monitor)
            self.stop_button.setEnabled(False)
            self.export_button = QtWidgets.QPushButton("JSONL 내보내기")
            self.export_button.clicked.connect(self.export_jsonl)
            header.addWidget(self.start_button)
            header.addWidget(self.stop_button)
            header.addWidget(self.export_button)
            page.addLayout(header)

            metrics = QtWidgets.QHBoxLayout()
            metrics.setSpacing(12)
            score_card, self.score_value, self.score_bar = self._metric_card(
                "SUSPICION SCORE", "— / 100"
            )
            confidence_card, self.confidence_value, self.confidence_bar = self._metric_card(
                "OBSERVATION CONFIDENCE", "— / 100"
            )
            status_card = QtWidgets.QFrame()
            status_card.setObjectName("card")
            status_layout = QtWidgets.QVBoxLayout(status_card)
            status_name = QtWidgets.QLabel("REVIEW STATUS")
            status_name.setObjectName("cardLabel")
            self.status_badge = QtWidgets.QLabel("INSUFFICIENT")
            self.status_badge.setAlignment(QtCore.Qt.AlignCenter)
            self.status_badge.setObjectName("statusBadge")
            status_layout.addWidget(status_name)
            status_layout.addWidget(self.status_badge, 1)
            metrics.addWidget(score_card, 1)
            metrics.addWidget(confidence_card, 1)
            metrics.addWidget(status_card, 1)
            page.addLayout(metrics)

            sensor_frame = QtWidgets.QFrame()
            sensor_frame.setObjectName("card")
            sensor_layout = QtWidgets.QHBoxLayout(sensor_frame)
            sensor_title = QtWidgets.QLabel("SENSORS")
            sensor_title.setObjectName("cardLabel")
            sensor_layout.addWidget(sensor_title)
            sensor_layout.addStretch(1)
            self.sensor_labels: dict[str, Any] = {}
            display_names = {
                "collector": "COLLECTOR",
                "sysmon": "SYSMON",
                "game": "GAME PROCESS",
                "handles": "HANDLES",
                "overlay": "OVERLAY",
                "modules": "MODULES",
                "telemetry": "TEAM LOG",
            }
            for key in SENSOR_NAMES:
                label = QtWidgets.QLabel(f"{display_names[key]}  UNKNOWN")
                label.setObjectName("sensorPill")
                label.setProperty("sensorState", "unknown")
                self.sensor_labels[key] = label
                sensor_layout.addWidget(label)
            page.addWidget(sensor_frame)

            table_frame = QtWidgets.QFrame()
            table_frame.setObjectName("card")
            table_layout = QtWidgets.QVBoxLayout(table_frame)
            table_header = QtWidgets.QHBoxLayout()
            table_title = QtWidgets.QLabel("RECENT EVIDENCE EVENTS")
            table_title.setObjectName("cardLabel")
            self.event_count_label = QtWidgets.QLabel("0 events")
            self.event_count_label.setObjectName("muted")
            table_header.addWidget(table_title)
            table_header.addStretch(1)
            table_header.addWidget(self.event_count_label)
            table_layout.addLayout(table_header)

            self.events_table = QtWidgets.QTableWidget(0, len(EVENT_COLUMNS))
            self.events_table.setHorizontalHeaderLabels(
                [
                    "TIMESTAMP",
                    "CATEGORY",
                    "SOURCE PROCESS",
                    "ACCESS",
                    "BASE PTS",
                    "REASON",
                ]
            )
            self.events_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.events_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.events_table.setAlternatingRowColors(True)
            self.events_table.verticalHeader().setVisible(False)
            self.events_table.setShowGrid(False)
            horizontal = self.events_table.horizontalHeader()
            horizontal.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            horizontal.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            horizontal.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
            horizontal.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
            horizontal.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
            horizontal.setSectionResizeMode(5, QtWidgets.QHeaderView.Stretch)
            table_layout.addWidget(self.events_table, 1)
            page.addWidget(table_frame, 1)

            self.footer = QtWidgets.QLabel("READY · monitoring stopped")
            self.footer.setObjectName("footer")
            page.addWidget(self.footer)

        def _metric_card(self, title: str, initial: str) -> tuple[Any, Any, Any]:
            card = QtWidgets.QFrame()
            card.setObjectName("card")
            layout = QtWidgets.QVBoxLayout(card)
            label = QtWidgets.QLabel(title)
            label.setObjectName("cardLabel")
            value = QtWidgets.QLabel(initial)
            value.setObjectName("metricValue")
            progress = QtWidgets.QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setTextVisible(False)
            layout.addWidget(label)
            layout.addWidget(value)
            layout.addWidget(progress)
            return card, value, progress

        def _apply_theme(self) -> None:
            self.setStyleSheet(
                """
                QMainWindow, QWidget#pageRoot {
                    background: #09111f;
                }
                QWidget {
                    color: #dbe7f6;
                    font-family: "Segoe UI";
                    font-size: 12px;
                }
                QLabel#title { font-size: 20px; font-weight: 700; color: #f4f8ff; }
                QLabel#subtitle, QLabel#muted { color: #8294ad; }
                QLabel#footer { color: #6f829e; font-family: Consolas; }
                QFrame#card {
                    background: #101b2d;
                    border: 1px solid #1d2c43;
                    border-radius: 9px;
                }
                QLabel#cardLabel {
                    color: #7f95b2;
                    font-size: 11px;
                    font-weight: 700;
                }
                QLabel#metricValue { color: #f4f8ff; font-size: 26px; font-weight: 700; }
                QLabel#statusBadge, QLabel#sensorPill {
                    background: #17253a;
                    border: 1px solid #2a3b55;
                    border-radius: 6px;
                    padding: 7px 12px;
                    font-weight: 700;
                }
                QLabel#sensorPill[sensorState="online"] { color: #66e3b4; border-color: #287b62; }
                QLabel#sensorPill[sensorState="waiting"] { color: #f3c969; border-color: #7f672d; }
                QLabel#sensorPill[sensorState="offline"],
                QLabel#sensorPill[sensorState="error"] { color: #ff7f8d; border-color: #883846; }
                QLabel#sensorPill[sensorState="unavailable"],
                QLabel#sensorPill[sensorState="unknown"] { color: #8da0b9; }
                QProgressBar {
                    min-height: 6px; max-height: 6px;
                    border: 0; border-radius: 3px; background: #1a2940;
                }
                QProgressBar::chunk { background: #42d9b4; border-radius: 3px; }
                QPushButton {
                    background: #16243a; color: #dbe7f6;
                    border: 1px solid #2c3f5d; border-radius: 6px;
                    padding: 8px 14px; font-weight: 600;
                }
                QPushButton:hover { background: #1d304c; }
                QPushButton:disabled { color: #5e708a; background: #101a2a; }
                QPushButton#primaryButton { background: #176f60; border-color: #299c84; }
                QTableWidget {
                    background: #0d1727; alternate-background-color: #111d30;
                    border: 0; color: #d5e0ef; selection-background-color: #203a54;
                }
                QHeaderView::section {
                    background: #132036; color: #7f95b2; border: 0;
                    border-bottom: 1px solid #263751; padding: 8px; font-weight: 700;
                }
                """
            )

        def _set_status_badge(self, status: str) -> None:
            colors = {
                "LOW": ("#66e3b4", "#183e39", "#287b62"),
                "REVIEW": ("#f3c969", "#40351c", "#7f672d"),
                "HIGH": ("#ff9e64", "#452b20", "#914a2f"),
                "CRITICAL": ("#ff7f8d", "#49212a", "#973b4c"),
                "INSUFFICIENT": ("#9eacc0", "#202c3d", "#3c4b61"),
            }
            foreground, background, border = colors[status]
            self.status_badge.setText(status)
            self.status_badge.setStyleSheet(
                f"color:{foreground}; background:{background}; "
                f"border:1px solid {border}; border-radius:6px; padding:10px; "
                "font-size:16px; font-weight:700;"
            )

        def _set_metric(self, label: Any, bar: Any, value: float | None) -> None:
            label.setText(format_metric(value))
            bar.setValue(int(round(value))) if value is not None else bar.setValue(0)

        def _show_error(self, operation: str, exc: Exception) -> None:
            self._last_error = f"{operation}: {type(exc).__name__}: {exc}"
            self.footer.setText(f"ERROR · {self._last_error}")

        def start_monitor(self) -> None:
            try:
                self.controller.start()
            except Exception as exc:
                self._show_error("start", exc)
                return
            self._monitor_running = True
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.footer.setText("MONITORING · collector running")
            self.refresh_view()

        def stop_monitor(self) -> None:
            try:
                self.controller.stop()
            except Exception as exc:
                self._show_error("stop", exc)
                return
            self._monitor_running = False
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.footer.setText("READY · monitoring stopped")
            self.refresh_view()

        def export_jsonl(self) -> None:
            default_name = str(Path.cwd() / "anti-esp-evidence.jsonl")
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Export evidence as JSONL",
                default_name,
                "JSON Lines (*.jsonl);;All files (*)",
            )
            if not path:
                return
            if not Path(path).suffix:
                path += ".jsonl"
            try:
                self.controller.export_jsonl(path)
            except Exception as exc:
                self._show_error("export", exc)
                return
            self.footer.setText(f"EXPORTED · {path}")

        def refresh_view(self) -> None:
            self._last_error = ""
            self._refresh_snapshot()
            self._refresh_sensors()
            self._refresh_events()
            if not self._last_error:
                actual_running = bool(
                    getattr(self.controller, "running", self._monitor_running)
                )
                if self._monitor_running and not actual_running:
                    self._monitor_running = False
                    self.start_button.setEnabled(True)
                    self.stop_button.setEnabled(False)
                state = "collector running" if actual_running else "monitoring stopped"
                self.footer.setText(f"UPDATED · {state} · {datetime.now():%H:%M:%S}")

        def _refresh_snapshot(self) -> None:
            try:
                snapshot = self.controller.snapshot()
                suspicion, confidence, status = snapshot_values(snapshot)
            except Exception as exc:
                self._show_error("snapshot", exc)
                suspicion, confidence, status = None, None, "INSUFFICIENT"
            self._set_metric(self.score_value, self.score_bar, suspicion)
            self._set_metric(self.confidence_value, self.confidence_bar, confidence)
            self._set_status_badge(status)

        def _refresh_sensors(self) -> None:
            try:
                sensors = self.controller.sensor_status()
            except Exception as exc:
                self._show_error("sensor status", exc)
                sensors = {}
            display_names = {
                "collector": "COLLECTOR",
                "sysmon": "SYSMON",
                "game": "GAME PROCESS",
                "handles": "HANDLES",
                "overlay": "OVERLAY",
                "modules": "MODULES",
                "telemetry": "TEAM LOG",
            }
            for key, label in self.sensor_labels.items():
                sensor = _field(sensors, key)
                sensor_text = format_sensor_status(sensor)
                semantic = sensor_semantic_state(sensor)
                label.setText(f"{display_names[key]}  {sensor_text}")
                label.setToolTip(_plain_text(_field(sensor, "message"), ""))
                if label.property("sensorState") != semantic:
                    label.setProperty("sensorState", semantic)
                    label.style().unpolish(label)
                    label.style().polish(label)

        def _refresh_events(self) -> None:
            try:
                events = self.controller.recent_events(limit=100)
            except TypeError:
                # Accommodate controllers that expose a positional-only limit.
                try:
                    events = self.controller.recent_events(100)
                except Exception as exc:
                    self._show_error("recent events", exc)
                    events = []
            except Exception as exc:
                self._show_error("recent events", exc)
                events = []

            if events is None:
                events = []
            elif not isinstance(events, Sequence):
                events = list(events)
            rows = [event_row(event) for event in events]
            self.events_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column_index, text in enumerate(row):
                    item = QtWidgets.QTableWidgetItem(text)
                    if column_index in (3, 4):
                        item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.events_table.setItem(row_index, column_index, item)
            self.event_count_label.setText(f"{len(rows)} events")

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
            self.refresh_timer.stop()
            if self._monitor_running:
                try:
                    self.controller.stop()
                except Exception:
                    pass
            event.accept()

    _QT_WINDOW_CLASS = _DashboardWindow
    return _DashboardWindow


class DashboardWindow:
    """Lazy constructor for the PyQt5 main window.

    ``DashboardWindow(controller)`` returns the concrete ``QMainWindow``.  The
    facade keeps importing :mod:`anti_esp.dashboard` safe on hosts where PyQt5
    is intentionally not installed.
    """

    def __new__(cls, controller: Any) -> Any:
        window_class = _dashboard_window_class()
        return window_class(controller)


__all__ = [
    "DashboardWindow",
    "EVENT_COLUMNS",
    "SENSOR_NAMES",
    "STATUS_LEVELS",
    "classify_status",
    "event_row",
    "format_access",
    "format_metric",
    "format_points",
    "format_sensor_status",
    "format_timestamp",
    "normalize_percent",
    "normalize_status",
    "sensor_semantic_state",
    "snapshot_values",
]
