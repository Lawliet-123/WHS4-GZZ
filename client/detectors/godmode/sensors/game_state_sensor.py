from abc import ABC, abstractmethod
from typing import Optional

from core.models import PlayerSnapshot


class GameStateSensor(ABC):
    """
    MECCHA CHAMELEON의 플레이어 상태를 수집하는 Sensor의 공통 인터페이스.

    Detector는 게임 메모리를 직접 읽지 않는다.
    Sensor가 실제 게임 상태를 읽어서 PlayerSnapshot으로 변환하고,
    GodModeDetector는 그 Snapshot만 분석한다.
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        게임 프로세스 또는 데이터 소스에 연결한다.

        연결 성공:
            True

        연결 실패:
            False
        """
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """
        Sensor 연결을 종료한다.
        """
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        """
        현재 게임 데이터에 접근 가능한 상태인지 반환한다.
        """
        raise NotImplementedError

    @abstractmethod
    def read_snapshot(self) -> Optional[PlayerSnapshot]:
        """
        현재 플레이어 상태를 읽어 PlayerSnapshot으로 반환한다.

        읽기에 실패했거나 플레이어가 아직 존재하지 않는 경우
        None을 반환한다.
        """
        raise NotImplementedError