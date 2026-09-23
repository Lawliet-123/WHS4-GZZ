"""
sensors/fake_aim_telemetry_sensor.py
게임 없이 탐지 로직만 테스트하기 위한 가짜 Sensor.
미리 만들어둔 텔레메트리 이벤트 목록을 그대로 흘려보낸다.
"""

from typing import Iterator, List

from core.models import TelemetryEvent
from sensors.aim_telemetry_sensor import AimTelemetrySensor


class FakeAimTelemetrySensor(AimTelemetrySensor):
    def __init__(self, events: List[TelemetryEvent]):
        self._events = list(events)

    def read_events(self) -> Iterator[TelemetryEvent]:
        events, self._events = self._events, []  # 한 번 내보낸 건 다시 안 내보냄
        yield from events

