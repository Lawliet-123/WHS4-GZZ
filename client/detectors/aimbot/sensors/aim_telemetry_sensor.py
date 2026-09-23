"""
sensors/aim_telemetry_sensor.py
Sensor라면 따라야 하는 공통 규칙.

어떤 방식(pymem 폴링, UE4SS 후킹, 로그 파일 재생 등)으로 구현하든
read_events() 하나만 지키면 AimbotDetector에 그대로 연결할 수 있다.
Detector는 이 인터페이스만 알고, 실제 구현체가 뭔지는 모른다.
"""

from abc import ABC, abstractmethod
from typing import Iterator

from core.models import TelemetryEvent


class AimTelemetrySensor(ABC):
    @abstractmethod
    def read_events(self) -> Iterator[TelemetryEvent]:
        """새로 발생한 발사/성공 결과 이벤트를 순서대로 내놓는다.
        아직 새 이벤트가 없으면 아무것도 내놓지 않아도 된다."""
        raise NotImplementedError

