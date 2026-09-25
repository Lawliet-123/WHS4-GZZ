"""게임 프로세스에 열린 외부 핸들과 접근 권한을 관찰한다."""

from .detector import ProcessAccessDetector
from .handle_sensor import ExternalHandleSensor, HandleSensorUnavailable
from .models import ExternalHandleObservation, ScanContext

__all__ = [
    "ExternalHandleObservation",
    "ExternalHandleSensor",
    "HandleSensorUnavailable",
    "ProcessAccessDetector",
    "ScanContext",
]
