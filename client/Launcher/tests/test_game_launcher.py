"""게임 위치 선택과 UE4SS 안전 설치의 임시 묶음 테스트."""
import hashlib  # 테스트용 ZIP·시그니처의 예상 SHA-256을 계산한다.
import io  # 직접 실행 명령의 화면 출력을 검사한다.
import json  # 설치 등록부에 기록된 필드를 확인한다.
import os  # 임시 환경변수와 로그 수정 시각을 설정한다.
from pathlib import Path  # 가짜 게임 폴더와 파일 경로를 만든다.
from contextlib import redirect_stdout  # 테스트 중 준비 명령 출력을 캡처한다.
import sys  # 테스트 대상 모듈의 import 경로를 등록한다.
import tempfile  # 실제 게임 폴더 대신 자동 정리되는 임시 폴더를 쓴다.
import time  # 로그가 이번 실행의 것인지 확인할 기준 시각이다.
import unittest  # 각 안전 조건을 독립 테스트로 실행한다.
from unittest import mock  # 게임 실행·Steam·환경변수를 임시로 바꾼다.
import zipfile  # 작은 가짜 UE4SS ZIP을 만들어 설치 흐름만 검증한다.

LAUNCHER = Path(__file__).resolve().parents[1]  # 테스트 파일에서 Launcher 폴더를 찾는다.
sys.path.insert(0, str(LAUNCHER))  # 직접 실행 파일 형태의 모듈을 import할 수 있게 한다.

import game_launcher as gl  # 실제 게임 설치 코드를 테스트한다.


class GameLauncherTests(unittest.TestCase):
    def setUp(self):
        """매 테스트마다 별도 가짜 게임·ZIP·선택적 시그니처 파일을 준비한다."""
        self.temp = tempfile.TemporaryDirectory()  # PC의 실제 게임 디렉터리와 격리한다.
        self.addCleanup(self.temp.cleanup)  # 테스트가 끝나면 임시 파일을 자동 정리한다.
        self.base = Path(self.temp.name)  # 파일 생성 기준 경로를 보관한다.
        self.root = self.base / "MECCHA CHAMELEON"  # 가짜 Steam 게임 설치 루트다.
        self.bin = self.root / gl.WIN64_REL  # 게임 exe와 UE4SS가 놓일 가짜 Win64다.
        self.bin.mkdir(parents=True)  # 부모 디렉터리까지 한 번에 만든다.
        (self.bin / gl.GAME_EXE).write_bytes(b"fake game exe")  # 경로 검증을 통과할 최소 파일이다.
        self.zip_path = self.base / "ue4ss.zip"  # 테스트용 묶음 파일 위치다.
        with zipfile.ZipFile(self.zip_path, "w") as archive:  # 외부 다운로드 없이 가짜 ZIP을 만든다.
            for target in gl.UE4SS_BUNDLE_FILES:  # 코드가 요구하는 필수 항목을 모두 넣는다.
                archive.writestr("release/" + target, target.encode("ascii"))  # 상위 폴더로 감싼 ZIP을 시험한다.
            archive.writestr("release/ue4ss/Mods/mods.txt", "GodMode : 1\r\nDamageLogger : 0\r\n")  # 타 모드 보존 시험이다.
        self.zip_sha = hashlib.sha256(self.zip_path.read_bytes()).hexdigest()  # 같은 ZIP의 예상 해시다.
        self.signature = self.base / "StaticConstructObject.lua"  # 게임 전용 파일은 ZIP 밖에 둔다.
        self.signature.write_bytes(b"-- test-only signature")  # 실제 게임 시그니처는 쓰지 않는다.
        self.signature_sha = hashlib.sha256(self.signature.read_bytes()).hexdigest()  # 테스트용 고정 해시다.
        self.manifest = self.base / "ue4ss_install.json"  # 등록부도 임시 디렉터리로 보낸다.
        self.env = mock.patch.dict(os.environ, {"GZZ_UE4SS_MANIFEST": str(self.manifest),
                                                "GZZ_UE4SS_SIGNATURE": ""}, clear=False)  # 경로 주입이다.
        self.env.start()  # 설치 함수가 임시 등록부 위치를 보게 한다.
        self.addCleanup(self.env.stop)  # 테스트 뒤 원래 환경변수를 복원한다.
        gl._cache.clear()  # 앞 테스트가 찾은 게임 경로 캐시를 제거한다.
        self.addCleanup(gl._cache.clear)  # 다음 테스트에 경로가 새지 않게 한다.

    def install(self, **kwargs):
        """반복 테스트에서 검증된 가짜 번들 인자를 동일하게 넘긴다."""
        return gl.prepare_ue4ss(str(self.root), str(self.zip_path), self.zip_sha,
                                game_running=False, **kwargs)  # 기본 배포본은 별도 시그니처가 없다.

    def test_game_root_accepts_install_bin_and_exe_paths(self):
        """루트·Win64·exe 어느 경로를 받아도 같은 게임 루트로 정규화한다."""
        for path in (self.root, self.bin, self.bin / gl.GAME_EXE):  # 허용할 세 가지 입력 형태다.
            with self.subTest(path=path):  # 실패 시 어떤 경로가 문제인지 보여준다.
                self.assertEqual(gl._validated_game_root(str(path)), str(self.root))  # 결과는 같아야 한다.
        self.assertIsNone(gl._validated_game_root(str(self.base)))  # exe 없는 임의 폴더는 거절한다.

    def test_selected_path_persists_after_steam_discovery_fails(self):
        """직접 고른 게임 경로가 자동 탐색 실패 뒤에도 다시 사용된다."""
        saved = self.base / "game_path.json"  # 실제 사용자 설정 대신 임시 파일을 쓴다.
        with mock.patch.object(gl, "SAVED_GAME_PATH", str(saved)):  # 저장 대상을 가짜 위치로 바꾼다.
            self.assertEqual(gl.save_game_root(str(self.bin / gl.GAME_EXE)), str(self.root))  # exe를 선택한다.
            with mock.patch.object(gl, "find_game_pid", return_value=None), \
                 mock.patch.object(gl, "_steam_libraries", return_value=[]), \
                 mock.patch.object(gl, "GAME_DIR", str(self.base / "other")), \
                 mock.patch.dict(os.environ, {"GZZ_GAME_DIR": ""}):  # 다른 모든 발견 경로를 제거한다.
                self.assertEqual(gl.find_game_root(), str(self.root))  # 저장된 경로가 선택돼야 한다.
            self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))["game_root"], str(self.root))  # JSON도 확인한다.

    def test_missing_fixed_hash_never_writes_game_folder(self):
        """팀 ZIP 해시 설정이 빠지면 게임 폴더에 단 한 파일도 쓰지 않는다."""
        with mock.patch.object(gl, "PINNED_UE4SS_ZIP_SHA256", ""):  # 잘못된 배포 설정만 흉내 낸다.
            result = gl.prepare_ue4ss(str(self.root), str(self.zip_path), game_running=False)  # 고정 해시 없이 시도한다.
        self.assertEqual(result.status, "MISSING")  # 원인이 부족한 배포 자료임을 구분한다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 프록시 DLL을 만들지 않았다.
        self.assertFalse(self.manifest.exists())  # 설치 완료 등록부도 만들지 않았다.

    def test_approved_release_zip_hash_is_pinned(self):
        """문서와 대조한 팀 배포 ZIP만 기본 설치 후보로 인정한다."""
        self.assertEqual(gl.PINNED_UE4SS_ZIP_SHA256,
                         "050948bdf6b4aae2ff8d834aaebadbf7535d4cb8478fbb579966a5ca3142f86a")  # 실물 ZIP 해시다.

    def test_real_bundle_installs_into_fake_game_when_supplied(self):
        """실제 배포 ZIP을 가짜 게임 폴더에 설치해 경로·해시·파일 구성을 확인한다."""
        bundle = os.environ.get("GZZ_TEST_UE4SS_ZIP")  # CI에는 없는 선택적 실물 검증 입력이다.
        if not bundle:  # 팀 ZIP이 없는 다른 개발자 PC에서도 기본 테스트는 실행된다.
            self.skipTest("GZZ_TEST_UE4SS_ZIP이 지정되지 않았습니다")
        self.assertEqual(hashlib.sha256(Path(bundle).read_bytes()).hexdigest(),
                         gl.PINNED_UE4SS_ZIP_SHA256)  # 승인된 ZIP과 바이트까지 같다.
        result = gl.prepare_ue4ss(str(self.root), bundle, game_running=False)
        self.assertEqual(result.status, "READY", result.detail)  # 실제 ZIP 구조가 코드의 기대와 맞는다.
        self.assertFalse((self.bin / "ue4ss" / "UE4SS_Signatures" / "StaticConstructObject.lua").exists())
        self.assertEqual(result.installed, len(gl.UE4SS_BUNDLE_FILES) + len(gl.TEAM_MOD_FILES))
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["bundle"]["sha256"], gl.PINNED_UE4SS_ZIP_SHA256)

    def test_approved_bundle_installs_without_optional_game_signature(self):
        """팀이 검증한 런타임 ZIP은 별도 시그니처 없이 설치한다."""
        with mock.patch.object(gl, "PINNED_UE4SS_ZIP_SHA256", self.zip_sha):  # 가짜 ZIP만 고정값과 맞춘다.
            result = gl.prepare_ue4ss(str(self.root), str(self.zip_path), game_running=False)  # 시그니처 없이 시도한다.
        self.assertEqual(result.status, "READY")  # 없는 선택 파일 때문에 설치가 막히지 않는다.
        self.assertTrue((self.bin / "dwmapi.dll").exists())  # 필수 런타임은 설치한다.
        self.assertFalse((self.bin / "ue4ss" / "UE4SS_Signatures" / "StaticConstructObject.lua").exists())
        self.assertTrue(self.manifest.exists())  # 설치한 파일 해시는 정상적으로 기록한다.

    def test_prepare_cli_uses_approved_bundle_without_signature(self):
        """사용자가 실행할 준비 명령도 동일한 설치 경로를 거친다."""
        output = io.StringIO()
        with mock.patch.object(gl, "PINNED_UE4SS_ZIP_SHA256", self.zip_sha), redirect_stdout(output):
            code = gl._cli(["prepare", "--game-dir", str(self.root), "--bundle", str(self.zip_path)])
        self.assertEqual(code, 0)
        self.assertIn("READY:", output.getvalue())
        self.assertFalse((self.bin / "ue4ss" / "UE4SS_Signatures" / "StaticConstructObject.lua").exists())

    def test_wait_load_cli_requires_new_log(self):
        """대기 명령은 과거 로그를 정상 로드 근거로 재사용하지 않는다."""
        output = io.StringIO()
        with mock.patch.object(gl, "wait_for_ue4ss_log", return_value=gl.UE4SSResult("READY", "로드 확인")) as waiter, \
             redirect_stdout(output):
            code = gl._cli(["wait-load", "--game-dir", str(self.root), "--timeout", "2"])
        self.assertEqual(code, 0)
        self.assertIsNone(waiter.call_args.args[1])  # 세션 연결은 지정할 때만 요구한다.
        self.assertGreater(waiter.call_args.args[2], 0)  # 새 로그의 기준 시각을 넘긴다.
        self.assertIn("READY:", output.getvalue())

    def test_explicit_signature_requires_hash_and_is_recorded(self):
        """나중에 별도 파일이 제공되면 해시 확인 후에만 설치·등록한다."""
        missing_hash = self.install(signature_path=str(self.signature))
        self.assertEqual(missing_hash.status, "MISSING")
        self.assertFalse((self.bin / "dwmapi.dll").exists())
        result = self.install(signature_path=str(self.signature),
                              expected_signature_sha256=self.signature_sha)
        self.assertEqual(result.status, "READY")
        self.assertEqual(result.installed, len(gl.UE4SS_BUNDLE_FILES) + len(gl.TEAM_MOD_FILES) + 1)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertIn("Chameleon/Binaries/Win64/ue4ss/UE4SS_Signatures/StaticConstructObject.lua",
                      manifest["files"])

    def test_unrequested_existing_signature_is_a_conflict(self):
        """기존에 깔린 게임별 패턴을 검증 없이 정상 설치물로 오인하지 않는다."""
        target = self.bin / "ue4ss" / "UE4SS_Signatures" / "StaticConstructObject.lua"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"unverified override")
        result = self.install()
        self.assertEqual(result.status, "CONFLICT")
        self.assertFalse((self.bin / "dwmapi.dll").exists())
        self.assertFalse(self.manifest.exists())

    def test_installs_once_preserves_other_mod_and_records_hashes(self):
        """필수 파일만 설치하고, 기존 타 모드·줄바꿈·해시 기록을 유지한다."""
        mods_path = self.bin / "ue4ss" / "Mods" / "mods.txt"  # 이미 쓰던 사용자 모드 설정이다.
        mods_path.parent.mkdir(parents=True)  # 가짜 게임에 기존 UE4SS 설정 경로를 만든다.
        mods_path.write_bytes(b"GodMode : 1\r\nCheatManagerEnablerMod : 1\r\nDamageLogger : 0\r\n")  # 기존 다른 모드는 판정·수정하지 않고 보존한다.
        result = self.install()  # 처음 설치한다.
        self.assertEqual(result.status, "READY")  # 파일 준비가 끝났다는 뜻이다.
        self.assertIn("기존 타 모드 안전성은 평가하지 않음", result.detail)
        self.assertEqual(result.installed, len(gl.UE4SS_BUNDLE_FILES) + len(gl.TEAM_MOD_FILES))  # 실제로 받은 파일만 센다.
        mods = mods_path.read_bytes()  # 설정을 바이트로 읽는다.
        self.assertIn(b"GodMode : 1\r\n", mods)  # 다른 모드를 임의로 제거하지 않는다.
        self.assertIn(b"CheatManagerEnablerMod : 1\r\n", mods)  # UE4SS 기본 모드 설정도 보존하며 핵 판정으로 해석하지 않는다.
        for name in gl.TEAM_MODS:  # 네 관측 모드 모두 활성화해야 한다.
            self.assertIn(f"{name} : 1\r\n".encode("ascii"), mods)  # 기존 줄바꿈 방식도 유지한다.
        self.assertEqual(mods.count(b"DamageLogger"), 1)  # 중복 줄은 만들지 않는다.
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))  # 탐지기용 등록부다.
        self.assertEqual(manifest["bundle"]["sha256"], self.zip_sha)  # 실제 설치 ZIP의 해시가 기록됐다.
        self.assertEqual(manifest["game_root"], str(self.root))  # 설치 루트가 가짜 게임과 같다.
        self.assertIn("Chameleon/Binaries/Win64/dwmapi.dll", manifest["files"])  # 프록시도 등록됐다.
        self.assertEqual(manifest["mods"], list(gl.TEAM_MODS))  # 설치한 네 모드를 빠짐없이 예외 등록한다.
        client_root = LAUNCHER.parent  # 각 관측 모드 원본을 찾을 client 폴더다.
        for target, relative in gl.TEAM_MOD_FILES.items():  # 네 모드의 모든 Lua 파일을 확인한다.
            installed = self.bin / target  # 게임 폴더에 복사된 파일이다.
            self.assertEqual(installed.read_bytes(), (client_root / relative).read_bytes())  # 원본과 바이트가 같다.
            self.assertIn("Chameleon/Binaries/Win64/" + target, manifest["files"])  # 해시 등록부에서도 빠지지 않는다.
        second = self.install()  # 정상 상태에서 다시 실행한다.
        self.assertEqual(second.status, "READY")  # 재검사도 정상이다.
        self.assertEqual(second.installed, 0)  # 같은 파일을 반복 복사하지 않는다.
        self.assertEqual((self.bin / "ue4ss" / "Mods" / "mods.txt").read_bytes(), mods)  # 설정도 그대로다.

    def test_fresh_install_does_not_enable_bundle_default_cheat_mods(self):
        """새 설치는 배포 ZIP의 CheatManager/GodMode 활성화 목록을 물려받지 않는다."""
        result = self.install()  # 기존 mods.txt가 없는 가짜 게임에 설치한다.
        self.assertEqual(result.status, "READY")  # 팀 모드만 켜는 설치는 성공해야 한다.
        mods = (self.bin / "ue4ss" / "Mods" / "mods.txt").read_text(encoding="utf-8")  # 새 목록이다.
        self.assertEqual(mods, "".join(f"{name} : 1\n" for name in gl.TEAM_MODS))  # 팀 모드 네 개만 있다.
        self.assertNotIn("GodMode : 1", mods)  # 핵 PoC GodMode와 관측 모드 GodModeTelemetry를 구분한다.

    def test_verified_directory_bundle_installs_only_required_runtime_files(self):
        """압축 해제 폴더도 설치 파일 지문이 고정됐을 때만 사용할 수 있다."""
        source = self.base / "unpacked_bundle"  # 다운로드 폴더 대신 임시 폴더를 쓴다.
        for target in gl.UE4SS_BUNDLE_FILES:  # 설치 함수가 읽을 네 파일을 준비한다.
            item = source / target  # 허용한 상대경로 그대로 둔다.
            item.parent.mkdir(parents=True, exist_ok=True)  # UE4SS 폴더층을 만든다.
            item.write_bytes(target.encode("utf-8"))  # 실제 바이너리 대신 테스트 바이트다.
        bundled_mods = source / "ue4ss" / "Mods" / "mods.txt"  # 원본에 있는 위험한 기본 설정이다.
        bundled_mods.parent.mkdir(parents=True, exist_ok=True)  # 기본 설정 폴더를 만든다.
        bundled_mods.write_text("CheatManagerEnablerMod : 1\n", encoding="utf-8")  # 켜진 치트 모드다.
        _, fingerprint = gl._directory_payload(source)  # 설치 대상 파일만의 고정 지문이다.
        result = gl.prepare_ue4ss(str(self.root), str(source), fingerprint,
                                  signature_path=str(self.signature),  # 별도 게임 시그니처다.
                                  expected_signature_sha256=self.signature_sha,
                                  game_running=False)  # 실제 게임을 띄우지 않는다.
        self.assertEqual(result.status, "READY")  # 검증된 폴더 형식도 설치 가능하다.
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))  # 설치 기록을 확인한다.
        self.assertEqual(manifest["bundle"]["kind"], "directory-payload")  # ZIP과 지문 방식을 구분한다.
        self.assertEqual(manifest["bundle"]["sha256"], fingerprint)  # 검증한 지문 그대로다.
        mods = (self.bin / "ue4ss" / "Mods" / "mods.txt").read_text(encoding="utf-8")  # 게임 쪽 설정이다.
        self.assertNotIn("CheatManagerEnablerMod", mods)  # 위험한 기본 설정을 가져오지 않았다.
        self.assertFalse((self.bin / "ue4ss" / "Mods" / "CheatManagerEnablerMod").exists())  # 기본 모드 스크립트도 복사하지 않았다.

    def test_directory_bundle_with_wrong_fingerprint_never_writes(self):
        """폴더 안 파일 바이트가 팀 고정값과 다르면 게임을 수정하지 않는다."""
        source = self.base / "unpacked_bundle"  # 검증 실패를 시험할 임시 폴더다.
        for target in gl.UE4SS_BUNDLE_FILES:  # 필수 파일을 모두 갖추게 한다.
            item = source / target  # 배포본의 각 경로다.
            item.parent.mkdir(parents=True, exist_ok=True)  # 상위 폴더를 준비한다.
            item.write_bytes(target.encode("utf-8"))  # 고정값과 다른 가짜 파일이다.
        result = gl.prepare_ue4ss(str(self.root), str(source), "0" * 64,
                                  signature_path=str(self.signature),  # 시그니처는 정상이다.
                                  expected_signature_sha256=self.signature_sha,
                                  game_running=False)  # 게임은 실행 중이 아니다.
        self.assertEqual(result.status, "CONFLICT")  # 조작된 폴더를 거부한다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 게임 쪽 파일은 그대로다.
        self.assertFalse(self.manifest.exists())  # 설치 성공 기록도 만들지 않았다.

    def test_file_picker_runs_without_interactive_console(self):
        """콘솔 없는 EXE에서도 자동 탐색 실패 시 파일 선택 창을 호출한다."""
        with mock.patch.object(gl, "find_game_pid", return_value=None), \
             mock.patch.object(gl, "_steam_libraries", return_value=[]), \
             mock.patch.object(gl, "GAME_DIR", str(self.base / "missing")), \
             mock.patch.object(gl, "choose_game_root", return_value=str(self.root)) as chooser, \
             mock.patch.object(sys, "stdin", None), \
             mock.patch.dict(os.environ, {"GZZ_GAME_DIR": ""}):  # 자동 탐색 경로를 모두 제거한다.
            self.assertEqual(gl.find_game_root(), str(self.root))  # 선택 창의 결과를 쓴다.
            chooser.assert_called_once_with()  # TTY 부재가 GUI 호출을 막지 않았다.

    def test_steam_launch_is_primary_even_if_game_exe_exists(self):
        """Steam 게임은 EXE가 있어도 Steam 프로토콜로 먼저 실행한다."""
        with mock.patch.object(gl, "find_game_pid", return_value=None), \
             mock.patch.object(gl, "find_game_dir", return_value=str(self.bin)), \
             mock.patch.object(gl.subprocess, "Popen") as popen, \
             mock.patch.object(gl.os, "startfile") as steam:  # 실제 게임·Steam은 실행하지 않는다.
            self.assertTrue(gl.launch())  # Steam 실행 요청을 전달한다.
            steam.assert_called_once_with(f"steam://rungameid/{gl.STEAM_APPID}")  # 정확한 게임 ID다.
            popen.assert_not_called()  # EXE 직접 실행을 먼저 하지 않는다.

    def test_direct_game_is_only_a_fallback_when_steam_uri_fails(self):
        """Steam URI 자체를 열 수 없을 때에만 EXE 직접 실행을 시도한다."""
        with mock.patch.object(gl, "find_game_pid", return_value=None), \
             mock.patch.object(gl, "find_game_dir", return_value=str(self.bin)), \
             mock.patch.object(gl.subprocess, "Popen") as popen, \
             mock.patch.object(gl.os, "startfile") as steam:  # 실제 게임·Steam은 실행하지 않는다.
            steam.side_effect = OSError("Steam URI handler missing")
            self.assertTrue(gl.launch())  # 직접 실행 요청만 성공했다는 뜻이다.
            popen.assert_called_once_with([str(self.bin / gl.GAME_EXE)], cwd=str(self.bin))

    def test_existing_game_is_not_launched_twice(self):
        """게임이 이미 켜져 있을 때는 Steam과 EXE 어느 쪽도 다시 실행하지 않는다."""
        with mock.patch.object(gl, "find_game_pid", return_value=12345), \
             mock.patch.object(gl.subprocess, "Popen") as popen, \
             mock.patch.object(gl.os, "startfile") as steam:
            self.assertFalse(gl.launch())
            steam.assert_not_called()
            popen.assert_not_called()

    def test_conflicting_proxy_dll_is_not_overwritten(self):
        """다른 dwmapi.dll이 있으면 기존 파일과 나머지 폴더를 건드리지 않는다."""
        proxy = self.bin / "dwmapi.dll"  # 게임 폴더의 충돌 대상이다.
        proxy.write_bytes(b"other installation")  # 다른 설치본 바이트를 먼저 둔다.
        result = self.install()  # 팀 ZIP 설치를 시도한다.
        self.assertEqual(result.status, "CONFLICT")  # 다른 빌드 충돌로 분류한다.
        self.assertEqual(proxy.read_bytes(), b"other installation")  # 기존 바이트를 보존했다.
        self.assertFalse((self.bin / "ue4ss" / "UE4SS.dll").exists())  # 일부 설치도 시작하지 않았다.
        self.assertFalse(self.manifest.exists())  # 잘못된 정상 등록부를 만들지 않았다.

    def test_disabled_proxy_and_wrong_zip_are_rejected(self):
        """꺼 둔 기존 프록시와 고정 SHA가 다른 ZIP은 모두 거부한다."""
        (self.bin / "dwmapi.dll.off").write_bytes(b"disabled")  # 기존 UE4SS 사용 흔적이다.
        self.assertEqual(self.install().status, "CONFLICT")  # 자동으로 활성화·덮어쓰지 않는다.
        (self.bin / "dwmapi.dll.off").unlink()  # 다음 해시 실패 조건만 따로 시험한다.
        result = gl.prepare_ue4ss(str(self.root), str(self.zip_path), "0" * 64,
                                  signature_path=str(self.signature),  # 시그니처는 정상 파일이다.
                                  expected_signature_sha256=self.signature_sha,  # 시그니처 해시는 맞춘다.
                                  game_running=False)  # 게임 실행 여부는 실패 원인이 아니다.
        self.assertEqual(result.status, "CONFLICT")  # ZIP 해시 불일치만으로 거부된다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 복사는 시작되지 않았다.

    def test_wrong_game_signature_hash_is_rejected_before_copy(self):
        """런타임 ZIP이 맞아도 게임 전용 시그니처가 다르면 쓰지 않는다."""
        result = gl.prepare_ue4ss(str(self.root), str(self.zip_path), self.zip_sha,
                                  signature_path=str(self.signature),  # 실제 테스트 시그니처 파일이다.
                                  expected_signature_sha256="0" * 64,  # 일부러 틀린 해시다.
                                  game_running=False)  # 실행 중인 게임은 없다.
        self.assertEqual(result.status, "CONFLICT")  # 시그니처 변경을 탐지한다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 부분 설치도 하지 않는다.

    def test_malformed_existing_mod_setting_fails_before_copy(self):
        """해석할 수 없는 기존 설정은 추측해 바꾸지 않는다."""
        mods = self.bin / "ue4ss" / "Mods" / "mods.txt"  # 기존 사용자 설정 파일이다.
        mods.parent.mkdir(parents=True)  # 가짜 모드 설정 폴더를 만든다.
        mods.write_text("NoclipLogger : maybe\n", encoding="utf-8")  # 새 팀 모드의 0/1이 아닌 값이다.
        result = self.install()  # 팀 모드 활성화를 시도한다.
        self.assertEqual(result.status, "ERROR")  # 모호한 설정을 오류로 보고한다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 설치 전 단계에서 멈췄다.
        self.assertEqual(mods.read_text(encoding="utf-8"), "NoclipLogger : maybe\n")  # 기존 값을 보존했다.

    def test_missing_bundle_file_fails_before_copy(self):
        """ZIP 전체 해시가 맞더라도 필수 DLL이 누락되면 설치하지 않는다."""
        incomplete = self.base / "incomplete.zip"  # 누락된 배포본 시험용 ZIP이다.
        with zipfile.ZipFile(incomplete, "w") as archive:  # 항목 한 개만 넣는다.
            archive.writestr("dwmapi.dll", b"only one file")  # UE4SS.dll 등이 없다.
        sha = hashlib.sha256(incomplete.read_bytes()).hexdigest()  # 이 ZIP 자체 해시는 맞춘다.
        result = gl.prepare_ue4ss(str(self.root), str(incomplete), sha,
                                  signature_path=str(self.signature),  # 별도 시그니처는 정상이다.
                                  expected_signature_sha256=self.signature_sha,  # 시그니처 해시도 맞춘다.
                                  game_running=False)  # 게임은 실행 중이 아니다.
        self.assertEqual(result.status, "ERROR")  # ZIP 구조 오류를 보고한다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 들어 있던 파일도 부분 설치하지 않는다.

    def test_game_running_blocks_changes(self):
        """이미 게임이 실행 중이면 디스크의 UE4SS 파일을 새로 수정하지 않는다."""
        result = gl.prepare_ue4ss(str(self.root), str(self.zip_path), self.zip_sha,
                                  signature_path=str(self.signature),  # 배포 자료 자체는 정상이다.
                                  expected_signature_sha256=self.signature_sha,  # 해시도 맞는다.
                                  game_running=True)  # 게임만 실행 중이라고 가정한다.
        self.assertEqual(result.status, "GAME_RUNNING")  # 종료 후 설치가 필요하다.
        self.assertFalse((self.bin / "dwmapi.dll").exists())  # 실행 중 바이트를 변경하지 않았다.

    def test_mods_txt_preserves_comments_and_deduplicates_team_mods(self):
        """기존 주석·타 모드·CRLF를 유지하고 팀 모드 중복만 제거한다."""
        original = (b"# note\r\nGodMode : 1\r\nDamageLogger : 0 ; team\r\n"
                    b"DamageLogger : 0\r\nGZZPaintObserver : 0\r\n"
                    b"NoclipLogger : 0\r\nNoclipLogger : 0\r\nGodModeTelemetry : 0\r\n")  # 새 모드도 중복·비활성화한다.
        result = gl._desired_mods_text(original)  # 설치 코드의 설정 변환을 직접 호출한다.
        self.assertIn(b"# note\r\nGodMode : 1\r\n", result)  # 다른 모드·주석이 남았다.
        self.assertIn(b"DamageLogger : 1 ; team\r\n", result)  # 값만 바꾸고 주석·CRLF는 보존했다.
        self.assertEqual(result.count(b"DamageLogger"), 1)  # 중복 줄을 정리했다.
        for name in gl.TEAM_MODS:  # 네 팀 모드는 각각 한 번만 남는다.
            self.assertEqual(result.count(name.encode("ascii")), 1)  # 중복을 제거한다.
            self.assertIn(f"{name} : 1".encode("ascii"), result)  # 모두 활성화한다.
        self.assertEqual(gl._desired_mods_text(result), result)  # 재실행해도 파일을 또 바꾸지 않는다.

    def test_log_verification_distinguishes_stale_incomplete_and_current(self):
        """오래된 로그와 다른 세션 로그는 실제 로드 성공으로 취급하지 않는다."""
        self.install()  # 로그 파일을 둘 폴더만 먼저 준비한다.
        log = self.bin / "ue4ss" / "UE4SS.log"  # 팀 배치의 로그 경로다.
        now = time.time()  # 이번 게임 시작 시각을 흉내 낸다.
        log.write_text("PS scan successful\nGit SHA #f6d5f942\n"
                       "[DamageLogger] loaded\n"
                       "[GZZPaintObserver] loaded; waiting for Python session control\n"
                       "[GZZPaintObserver] session=sample_001\n"
                       "[NoclipLogger] loaded\n"
                       "[GodModeTelemetry] Telemetry sensor loaded\n", encoding="utf-8")  # 네 모드의 실제 시작 문자열이다.
        os.utime(log, (now - 100, now - 100))  # 먼저 100초 전 로그로 만든다.
        self.assertEqual(gl.verify_ue4ss_log(str(self.root), "sample_001", now).status, "STALE")  # 재사용 금지다.
        os.utime(log, (now - 1, now - 1))  # 바로 직전 실행의 로그도 이번 확인에 재사용하지 않는다.
        self.assertEqual(gl.verify_ue4ss_log(str(self.root), None, now).status, "STALE")
        os.utime(log, (now, now))  # 이제 이번 실행의 새 로그로 바꾼다.
        self.assertEqual(gl.verify_ue4ss_log(str(self.root), "other", now).status, "UNAVAILABLE")  # 세션이 다르다.
        self.assertEqual(gl.verify_ue4ss_log(str(self.root), None, now).status, "READY")  # 로드만 확인할 수도 있다.
        self.assertEqual(gl.verify_ue4ss_log(str(self.root), "sample_001", now).status, "READY")  # 전부 맞는다.
        self.assertIn("기존 타 모드 안전성은 평가하지 않음",
                      gl.verify_ue4ss_log(str(self.root), "sample_001", now).detail)
        self.assertEqual(gl.wait_for_ue4ss_log(str(self.root), "sample_001", now,
                                               timeout_s=0).status, "READY")  # 대기 함수도 즉시 성공한다.
        log.write_text(log.read_text(encoding="utf-8").replace("[NoclipLogger] loaded\n", ""),
                       encoding="utf-8")  # 한 모드가 누락된 로그로 바꾼다.
        self.assertEqual(gl.verify_ue4ss_log(str(self.root), "sample_001", now).status,
                         "UNAVAILABLE")  # 일부만 로드됐는데 전체 정상으로 표시하면 안 된다.


if __name__ == "__main__":
    unittest.main()  # 이 파일을 직접 실행했을 때도 테스트를 돌린다.
