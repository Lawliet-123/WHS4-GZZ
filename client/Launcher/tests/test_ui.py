"""런처 상태 화면에 재시작·실패 이유가 보이는지 확인한다."""
from contextlib import redirect_stderr  # 실제 콘솔 대신 문자열로 상태 출력을 받는다.
import io  # 캡처용 메모리 스트림을 만든다.
from pathlib import Path  # 테스트 파일에서 Launcher 폴더를 찾는다.
import sys  # UI 모듈을 import할 경로를 등록한다.
import unittest  # 독립적인 출력 검증을 실행한다.

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 패키지 설치 없이 로컬 파일을 읽는다.
import ui  # 실제 런처 상태 화면 함수다.


class StatusUITests(unittest.TestCase):
    def test_render_shows_restart_owner_server_and_ue4ss(self):
        """재시작·실행 주체·서버·UE4SS 문제를 숨기지 않고 표시한다."""
        rows = [  # process_manager.snapshot()과 같은 형태의 가짜 모듈 목록이다.
            {"name": "aimbot", "status": "RESTART", "mode": "continuous",
             "runs": 3, "restarts": 2, "started_by": "watchdog", "detail": "재시작 대기",
             "log": "logs/aimbot.log"},  # 죽은 뒤 워치독이 되살리는 행이다.
            {"name": "input_signature", "status": "RUNNING", "mode": "continuous",
             "runs": 1, "restarts": 0, "started_by": "launcher", "uptime_s": 12.0},  # 정상 실행 행이다.
        ]
        stream = io.StringIO()  # 표준 오류 출력의 임시 목적지다.
        with redirect_stderr(stream):  # 실제 터미널에 테스트 표를 찍지 않는다.
            ui.render(rows, {"session": "run_001", "game_pid": 1234,
                             "server": "연결됨", "ue4ss": "행동 탐지 불가"})  # 상위 런처 문맥이다.
        rendered = stream.getvalue()  # 그려진 전체 문자열을 가져온다.
        for expected in ("run_001", "PID 1234", "서버 연결됨", "UE4SS 행동 탐지 불가",
                         "RESTART", "watchdog", "launcher", "logs/aimbot.log",
                         "확인 필요 1", "RUNNING은 탐지 성공"):  # 상태 해석도 분명해야 한다.
            self.assertIn(expected, rendered)  # 하나라도 빠지면 출력 회귀로 판단한다.

    def test_missing_ue4ss_and_full_failure_reason_are_visible(self):
        """UE4SS 검증 누락과 표에서 잘린 실패 이유를 정상으로 숨기지 않는다."""
        reason = "게임 폴더에서 필요한 구성 요소를 찾지 못해 행동 검사를 실행할 수 없습니다"  # 긴 원인이다.
        stream = io.StringIO()  # 실제 콘솔 대신 결과를 보관한다.
        with redirect_stderr(stream):  # 화면 출력만 캡처한다.
            ui.render([{"name": "observer", "status": "MISSING", "detail": reason}],
                      {"session": "run_002"})  # UE4SS 상태 키를 일부러 생략한다.
        rendered = stream.getvalue()  # 전체 문제 목록까지 본다.
        self.assertIn("UE4SS 검증 정보 없음", rendered)  # 알 수 없는 상태를 표시한다.
        self.assertIn(reason, rendered)  # 30칸 표에서 잘린 설명도 하단에 온전히 나온다.
        self.assertIn("확인 필요 1", rendered)  # 미가동 항목을 요약에서도 센다.

    def test_long_korean_details_fit_display_width(self):
        """한글을 한 칸으로 오인해 표 열이 밀리는 문제를 막는다."""
        self.assertEqual(ui._width(ui._fit("한글", 8)), 8)  # 짧은 글은 공백으로 채운다.
        self.assertEqual(ui._width(ui._fit("한글" * 20, 10)), 10)  # 긴 글도 지정 칸 안에서 자른다.


if __name__ == "__main__":
    unittest.main()  # 직접 실행해도 이 UI 테스트를 수행한다.
