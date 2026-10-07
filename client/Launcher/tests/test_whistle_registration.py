"""휘파람 모듈이 후크 없이도 깨끗하게 돌아야 한다.

whistle_rpc 는 게임 안에 넣은 관측용 후크(ac_whistle DLL)가 남긴 로그를 읽는다.
런처는 그 DLL 을 주입하지 않으므로, 기본 실행에 넣어 두면 배포본으로 돌리는 모든
PC 에서 ERROR 가 나고 모듈 전체가 검사 실패가 된다(10/7 은지님 배포 보고, 재민님
전체 런처 시험의 whistle_spoofing WARN). 기본 실행은 whistle 만 돌린다.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LAUNCHER_DIR = Path(__file__).resolve().parents[1]
REPO = LAUNCHER_DIR.parents[1]
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import modules  # noqa: E402

RUNNER = REPO / "client/detectors/whistle-spoofing/main.py"


class WhistleRegistrationTests(unittest.TestCase):

    def setUp(self):
        self.module = modules.by_name()["whistle_spoofing"]
        self.argv = self.module.resolved({
            "session": "whistle_001", "player": "player_042",
            "t0": "1000.250", "window": "0", "telemetry": "managed",
        })

    def test_default_run_is_whistle_only(self):
        self.assertEqual(self.argv.count("--only"), 1)
        self.assertEqual(self.argv[self.argv.index("--only") + 1], "whistle")
        self.assertEqual(self.argv[self.argv.index("--session") + 1], "whistle_001")
        self.assertEqual(self.argv[self.argv.index("--t0") + 1], "1000.250")
        self.assertEqual(self.module.mode, modules.ONESHOT)

    def test_hook_only_detector_is_not_in_the_default_run(self):
        # 이름으로 본다. --only 값이 쉼표 목록이 되어도 걸린다.
        joined = ",".join(self.argv)
        self.assertNotIn("whistle_rpc", joined)

    def test_no_final_run(self):
        # final_run 은 whistle_rpc 전용이었다. 스냅샷 검사인 whistle 을 게임이 꺼진 뒤
        # 한 번 더 돌리면 OFFLINE 이 세션 중 탐지를 덮는다.
        self.assertIsNone(self.module.final_run)

    def test_runner_still_offers_the_hook_detector_for_manual_runs(self):
        # 등록부에서 지운 게 아니라 기본 실행에서만 뺐다. 후크를 설치한 PC 는
        # --only whistle_rpc 로 직접 부른다.
        out = subprocess.run([sys.executable, str(RUNNER), "--list"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=str(REPO), timeout=120).stdout
        self.assertIn("whistle_rpc", out)
        self.assertIn("whistle", out)

    def _run(self, session, only):
        """러너를 돌리고 그 세션 이벤트를 돌려준다. 게임도 후크도 없는 PC 기준이다."""
        with tempfile.TemporaryDirectory() as out:
            subprocess.run(
                [sys.executable, str(RUNNER), "--session", session,
                 "--player", "p", "--t0", "0", "--window", "0",
                 "--log-dir", out, "--only", only],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=str(REPO), timeout=300)
            path = Path(out) / f"{session}.jsonl"
            return [json.loads(x) for x in
                    path.read_text(encoding="utf-8").splitlines() if x.strip()]

    # 두 검사가 **왜** 못 돌았는지는 다르다. 종료코드는 둘 다 2(검사 실패)라
    # 구분이 안 되므로 상태와 사유로 본다.
    #   whistle     — 게임이 없어서 OFFLINE. 런처 세션에서는 게임이 떠 있으니 풀린다
    #   whistle_rpc — 후크가 없어서 ERROR. 게임이 떠도 안 풀린다. 주입이 수동이라
    #                 배포본에는 그 단계가 없다. 그래서 기본 실행에서 뺀다

    def test_default_run_is_never_blocked_by_the_hook(self):
        events = self._run("t_whistle_only", "whistle")
        self.assertEqual([e["module"] for e in events], ["whistle"])
        self.assertNotIn("후크", json.dumps(events[0], ensure_ascii=False))
        if importlib.util.find_spec("pymem") is None:
            # 게임 메모리를 읽는 pymem 이 없는 파이썬(예: 서버용 .venv-server)이면
            # 탐지기 자체가 못 뜬다. 그건 후크와 상관없는 환경 문제라 여기서 안 따진다.
            self.assertEqual(events[0]["status"], "ERROR")
            return
        self.assertEqual(events[0]["status"], "OFFLINE", "게임만 켜면 풀리는 상태여야 한다")

    def test_hook_detector_fails_for_a_reason_the_game_cannot_fix(self):
        """whistle_rpc 가 ERROR 를 내는 것 자체는 올바르다 — 그래서 뺐다.

        후크가 안 붙은 것을 CLEAN 으로 뭉개면 조용한 미탐지가 된다. 고칠 곳은
        탐지기가 아니라 '후크 없는 PC 에서 이걸 기본으로 돌리는 것' 쪽이다.
        """
        events = self._run("t_rpc_only", "whistle_rpc")
        self.assertEqual([e["module"] for e in events], ["whistle_rpc"])
        self.assertEqual(events[0]["status"], "ERROR")
        self.assertIn("후크", json.dumps(events[0], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
