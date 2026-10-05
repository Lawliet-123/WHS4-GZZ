"""UE4SS 등록부 경로를 환경변수로 갈아끼울 수 없는지 확인한다. (회귀 시험)

## 무엇을 막는 시험인가

`filesystem`·`injection`·`whistle` 은 **런처가 설치한 UE4SS 만** 점수에서 뺀다.
빼는 기준은 이름이 아니라 `client/Launcher/logs/ue4ss_install.json` 에 적힌 해시다.

그런데 그 등록부 **경로** 를 `GZZ_UE4SS_MANIFEST` 환경변수로 바꿀 수 있었다.
즉 자기 `ue4ss.dll` 의 해시를 적은 등록부를 하나 만들어 그 변수로 가리키면
세 탐지기가 **동시에** 그 파일을 봐줬다. 해시로 가려 놓고 해시 목록을
갈아끼울 길을 열어 둔 셈이다. 런처가 자식 프로세스에 환경을 그대로
물려주므로 더 그렇다 (#102 의 하네스 PID 환경변수와 같은 종류).

여기서는 **위조 등록부를 만들어 환경변수로 가리킨 뒤**, 그래도 탐지가
유지되는지 본다. 같은 등록부를 함수 인자로 주면 제외가 동작하는 것도 같이
확인한다 — 안 그러면 "기능이 고장나서 통과" 하는 시험이 된다.

게임은 필요 없다. 가짜 게임 폴더만 만든다.

    python client\\LocalGuard\\memory_integrity\\tests\\t_manifest_env_ignored.py
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
sys.path.insert(0, MI)

from core import ue4ss_trust                  # noqa: E402
from detectors import filesystem as F         # noqa: E402

ENV = ue4ss_trust.LEGACY_ENV
WORK = tempfile.mkdtemp(prefix="ac_mani_env_")
ROOT = os.path.join(WORK, "MECCHA CHAMELEON")
WIN64 = os.path.join(ROOT, "Chameleon", "Binaries", "Win64")
UE = os.path.join(WIN64, "ue4ss")
FORGED = os.path.join(WORK, "위조_등록부.json")

# UE4SS 세 규칙만 본다. 이 PC 에는 핵 연구 잔재(C:\Dumper-7 등)가 실제로 있어서
# 총점으로 보면 가짜 트리와 무관한 규칙이 섞인다.
UE_RULES = {"proxy_dll_in_game_dir", "ue4ss_runtime", "third_party_lua_mod"}

fails = []


def check(c, msg):
    print(("  [통과] " if c else "  [실패] ") + msg)
    if not c:
        fails.append(msg)


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def build():
    """런처가 정상 설치한 것처럼 보이는 가짜 트리. 돌려주는 건 '우리 파일' 목록."""
    shutil.rmtree(ROOT, ignore_errors=True)
    ours = [
        put(os.path.join(WIN64, "dwmapi.dll"), "UE4SS proxy v3.0.1"),
        put(os.path.join(UE, "UE4SS.dll"), "UE4SS runtime v3.0.1"),
        put(os.path.join(UE, "Mods", "DamageLogger", "Scripts", "main.lua"), "-- 은지님"),
        put(os.path.join(UE, "Mods", "GZZPaintObserver", "Scripts", "observer.lua"), "-- 성민님"),
    ]
    put(os.path.join(WIN64, "PenguinHotel-Win64-Shipping.exe"), "game")
    put(os.path.join(UE, "UE4SS-settings.ini"), "[General]")
    put(os.path.join(UE, "Mods", "mods.txt"),
        "DamageLogger : 1\nGZZPaintObserver : 1\n")
    return ours


def write_manifest(path, files, mods):
    """런처 ue4ss_manifest.record() 와 같은 형식으로 적는다 (여기서는 위조본)."""
    data = {
        "game_root": ROOT,
        "files": {os.path.relpath(p, ROOT).replace("\\", "/"): sha(p) for p in files},
        "mods": mods,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


try:
    ours = build()
    write_manifest(FORGED, ours, ["DamageLogger", "GZZPaintObserver"])
    os.environ.pop(ENV, None)

    print("0) 등록부가 없으면 UE4SS 세 규칙이 전부 뜬다 (기준선)")
    base = F.scan(ROOT, manifest_path=os.path.join(WORK, "없음.json"))
    print(f"   점수 {base.score} / {sorted(set(base.reasons) & UE_RULES)}")
    check(UE_RULES <= set(base.reasons), "세 규칙이 다 뜬다")
    check(base.meta.get("ue4ss_manifest") == "none", "등록부 상태 none")

    print("\n1) 같은 등록부를 함수 인자로 주면 제외가 동작한다 (기능 확인)")
    good = F.scan(ROOT, manifest_path=FORGED)
    print(f"   점수 {good.score} / 뺀 것 {len(good.meta.get('exempt', []))}건")
    check(not (UE_RULES & set(good.reasons)),
          f"세 규칙이 빠진다 (남은 것 {sorted(UE_RULES & set(good.reasons))})")
    check(len(good.meta.get("exempt", [])) >= 3, "무엇을 뺐는지 근거에 남는다")
    check(good.meta.get("ue4ss_manifest") == "ok", "등록부 상태 ok")

    print("\n2) 환경변수로 가리키면 **안 봐준다** (이게 이 시험의 본문)")
    os.environ[ENV] = FORGED
    env_r = F.scan(ROOT)
    print(f"   점수 {env_r.score} / {sorted(set(env_r.reasons) & UE_RULES)}")
    check(UE_RULES <= set(env_r.reasons),
          f"세 규칙이 그대로 뜬다 (뜬 것 {sorted(set(env_r.reasons) & UE_RULES)})")
    exempt_files = {str(e.get("file", "")) + str(e.get("mod", ""))
                    for e in env_r.meta.get("exempt", [])}
    check(not any("ue4ss" in x.lower() or "dwmapi" in x.lower() for x in exempt_files),
          f"위조 등록부의 파일이 제외 목록에 없다 ({sorted(exempt_files)[:3]})")
    check(env_r.meta.get("ue4ss_manifest_env_ignored") == FORGED,
          "무시했다는 사실이 meta 에 남는다 (조용히 빼지 않는다)")

    print("\n3) 환경변수가 설정돼 있어도 기본 경로만 읽는다")
    _, st_env = ue4ss_trust.load()
    _, st_fix = ue4ss_trust.load(ue4ss_trust.MANIFEST_PATH)
    check(st_env == st_fix, f"load() 와 load(기본경로) 가 같다 ({st_env} / {st_fix})")
    check(ue4ss_trust.load(FORGED)[1] == "ok",
          "위조본 자체는 읽을 수 있는 형식이다 (형식 오류로 통과한 게 아니다)")
    check(ue4ss_trust.MANIFEST_PATH.replace("\\", "/").endswith(
        "client/Launcher/logs/ue4ss_install.json"), "기본 경로는 런처 로그 폴더다")
    check(not hasattr(ue4ss_trust, "manifest_path"),
          "환경변수를 읽던 manifest_path() 가 남아 있지 않다")

    print("\n4) trusted_modules 도 같다 (injection·whistle 이 쓰는 길)")
    mod = {"ue4ss.dll": os.path.join(UE, "UE4SS.dll")}
    by_arg, _ = ue4ss_trust.trusted_modules(mod, manifest_path=FORGED)
    check(by_arg == {"ue4ss.dll"}, f"인자로 주면 신뢰한다 ({by_arg})")
    by_env, _ = ue4ss_trust.trusted_modules(mod)
    check("ue4ss.dll" not in by_env, f"환경변수로는 신뢰하지 않는다 ({by_env})")

    print("\n5) 파일이 바뀌면 인자로 줘도 안 봐준다 (해시 기준이 살아 있다)")
    put(os.path.join(UE, "UE4SS.dll"), "UE4SS runtime v3.0.1 + 핵 코드")
    tampered, _ = ue4ss_trust.trusted_modules(mod, manifest_path=FORGED)
    check("ue4ss.dll" not in tampered, f"바뀐 파일은 신뢰하지 않는다 ({tampered})")
    r = F.scan(ROOT, manifest_path=FORGED)
    check("ue4ss_runtime" in r.reasons, "filesystem 도 다시 잡는다")

    print("\n6) 등록부의 game_root 가 다른 폴더면 그 폴더 해시로 통과시키지 않는다")
    # 10/5 에 이 시험이 잡은 구멍이다. 상대경로만 같은 가짜 트리를 검사시키면
    # 정품 설치본의 해시가 악성 파일을 통과시켰다.
    OTHER = os.path.join(WORK, "정품사본")
    legit = []
    for rel, text in [("Chameleon/Binaries/Win64/dwmapi.dll", "UE4SS proxy v3.0.1"),
                      ("Chameleon/Binaries/Win64/ue4ss/UE4SS.dll", "UE4SS runtime v3.0.1")]:
        legit.append(put(os.path.join(OTHER, rel.replace("/", os.sep)), text))
    other_mani = os.path.join(WORK, "다른루트.json")
    with open(other_mani, "w", encoding="utf-8") as f:
        json.dump({"game_root": OTHER,
                   "files": {os.path.relpath(p, OTHER).replace("\\", "/"): sha(p)
                             for p in legit},
                   "mods": []}, f, ensure_ascii=False)
    build()
    put(os.path.join(WIN64, "dwmapi.dll"), "핵 로더")        # 같은 상대경로, 다른 내용
    put(os.path.join(UE, "UE4SS.dll"), "핵 런타임")
    r = F.scan(ROOT, manifest_path=other_mani)
    print(f"   점수 {r.score} / {sorted(set(r.reasons) & UE_RULES)}")
    check("proxy_dll_in_game_dir" in r.reasons and "ue4ss_runtime" in r.reasons,
          f"남의 폴더 해시로 통과하지 않는다 (뜬 것 {sorted(set(r.reasons) & UE_RULES)})")
    check(not r.meta.get("exempt"), f"아무것도 안 뺀다 ({r.meta.get('exempt')})")
    check(r.meta.get("ue4ss_manifest_other_root") == OTHER,
          "검사한 폴더와 등록부 폴더가 다르다는 사실이 남는다")
finally:
    os.environ.pop(ENV, None)
    shutil.rmtree(WORK, ignore_errors=True)

print("\n" + ("전부 통과" if not fails else f"실패 {len(fails)}건: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
