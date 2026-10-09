"""게임이 안 켜져 있을 때 여섯 탐지기가 **같은 말**을 하는가.

게임이 없는 것은 볼 대상이 없는 것이지 검사가 깨진 게 아니다. 그런데 runtime
탐지기 셋(godmode·noclip·aimbot)만 ERROR 로 끝내서, 같은 표에서 말이 갈렸다.

  pymem 쪽 셋 (injection·value_tamper·overlay_hook) -> `- 게임이 실행 중이 아닙니다`
  ctypes 쪽 셋 (godmode·noclip·aimbot_runtime)      -> `X RuntimeError: Process not found`

읽는 사람이 뒤쪽을 탐지기 고장으로 읽었다(10/7 찬준님 보고, 전체 E2E 중).

**런처 모듈 칸은 이걸 고쳐도 그대로 WARN/"검사 실패" 다.** 종료코드 2 가 ERROR 와
OFFLINE 을 함께 덮기 때문이고(run_session.exit_code), 그건 의도된 계약이다 —
검사를 못 한 세션을 CLEAN 으로 넘기지 않으려는 것이다. 여기서 바뀌는 건 표의
기호·사유와 팀 스키마 status 다. 그 계약도 아래 4절에서 같이 고정한다.

게임 없이 돈다. 러너를 거치지 않고 탐지기를 직접 불러서, 이 파일 혼자 돌려도
결과가 같게 한다.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
MI = os.path.dirname(HERE)
if MI not in sys.path:
    sys.path.insert(0, MI)

from core.process_memory import ProcessMemory, ProcessNotRunning  # noqa: E402

# 게임과 같은 이름의 프로세스가 떠 있으면 이 시험은 의미가 없다.
GAME = "PenguinHotel-Win64-Shipping.exe"
# 실제로 존재할 수 없는 이름. find_process_id 가 반드시 None 을 돌려준다.
ABSENT = "gzz-this-process-does-not-exist-1759.exe"

RUNTIME_DETECTORS = ("godmode_runtime", "noclip_runtime", "aimbot_runtime")
PYMEM_DETECTORS = ("injection", "value_tamper", "overlay_hook")
OFFLINE_WHY = "게임이 실행 중이 아닙니다"

ok = fail = skipped = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f"  [FAIL] {label}{(' — ' + extra) if extra else ''}")


def skip(why):
    """건너뛴 것을 **세어서** 끝에 보여 준다.

    조용히 넘어가면 '여섯이 같은 말을 하는가' 를 한 번도 안 보고 초록불이 난다.
    이 시험이 막으려는 회귀가 그때 그냥 통과한다.
    """
    global skipped
    skipped += 1
    print(f"  [건너뜀] {why}")


def game_is_running():
    try:
        return ProcessMemory(GAME).find_process_id(GAME) is not None
    except Exception:
        return False


print("1. ProcessMemory 가 '프로세스 없음' 을 따로 구분하는가")

raised = None
try:
    ProcessMemory(ABSENT).connect()
except Exception as e:      # noqa: BLE001  — 무엇이 오는지가 이 시험의 요점이다
    raised = e

check("없는 프로세스에 붙으면 예외가 난다", raised is not None)
check("그 예외가 ProcessNotRunning 이다", isinstance(raised, ProcessNotRunning),
      f"실제: {type(raised).__name__}")
# 이걸 깨면 이 예외를 RuntimeError 로 잡던 기존 코드가 조용히 안 잡게 된다.
check("ProcessNotRunning 은 RuntimeError 를 물려받는다",
      issubclass(ProcessNotRunning, RuntimeError))
check("메시지에 찾던 이름이 들어 있다", ABSENT in str(raised), str(raised))


print("2. 게임이 없을 때 여섯 탐지기가 모두 OFFLINE 으로 같은 말을 하는가")

if game_is_running():
    skip("게임이 켜져 있습니다. 끄고 다시 돌려야 이 절이 돕니다.")
else:
    import importlib

    for name in RUNTIME_DETECTORS + PYMEM_DETECTORS:
        try:
            mod = importlib.import_module(f"detectors.{name}")
        except ImportError as e:
            # pymem 이 없는 파이썬 등. 환경 문제지 탐지기 결함이 아니다 —
            # 이 둘을 섞어 보고하는 게 바로 이 PR 이 없애려는 혼선이다.
            skip(f"{name}: {e}")
            continue
        except Exception as e:      # noqa: BLE001
            check(f"{name} 를 불러온다", False, f"{type(e).__name__}: {e}")
            continue
        try:
            res = mod.scan()
        except Exception as e:      # noqa: BLE001
            # 예외가 밖으로 새면 러너가 ERROR 로 바꾼다. 그 자체가 결함이다.
            check(f"{name}.scan() 이 예외를 밖으로 내지 않는다", False,
                  f"{type(e).__name__}: {e}")
            continue
        # unreachable 이 팀 규격의 OFFLINE 으로 나간다(core/result.py unavailable).
        # fail() 과 등급은 ERROR 로 같고, 이 플래그가 둘을 가른다.
        check(f"{name} 가 OFFLINE(unreachable) 로 끝난다", res.unreachable is True,
              f"error={res.error!r}")
        check(f"{name} 사유가 '{OFFLINE_WHY}' 다", res.error == OFFLINE_WHY,
              repr(res.error))
        check(f"{name} 점수가 0 이다", res.score == 0, str(res.score))


print("3. 진짜 고장은 여전히 ERROR 로 남는가")

# 게임이 없는 것만 OFFLINE 이어야 한다. 다른 예외까지 OFFLINE 으로 뭉개면
# 조용한 미탐지가 된다.
#
# **RuntimeError 계열을 꼭 넣는다.** ProcessNotRunning 이 RuntimeError 를
# 물려받으므로, except 절이 `except RuntimeError:` 로 넓어지면 아래 고장들이
# OFFLINE 으로 뭉개진다. ValueError 만 넣으면 그 변경이 그대로 통과한다.
import importlib  # noqa: E402

FAULTS = (
    RuntimeError(f"Module not found: {GAME}"),       # process_memory 가 실제로 던진다
    RuntimeError("Process is not connected"),
    ValueError("시험용 고장"),
)

for name in RUNTIME_DETECTORS:
    mod = importlib.import_module(f"detectors.{name}")
    original = mod.ProcessMemory
    for fault in FAULTS:
        class Boom:
            def __init__(self, *a, **k):
                pass

            def __enter__(self, _f=fault):
                raise _f

            def __exit__(self, *a):
                return False

        mod.ProcessMemory = Boom
        try:
            res = mod.scan()
        finally:
            mod.ProcessMemory = original
        tag = f"{name} / {type(fault).__name__}"
        check(f"{tag} 는 검사 실패로 보고한다", res.unreachable is False,
              f"unreachable={res.unreachable} error={res.error!r}")
        check(f"{tag} 실패 사유에 원인이 남는다", str(fault) in str(res.error),
              repr(res.error))


print("4. 런처 칸이 왜 그대로인가 — 종료코드 계약")

# 이 PR 로 런처 모듈 칸의 "검사 실패" 는 안 사라진다. 종료코드 2 가 ERROR 와
# OFFLINE 을 함께 덮기 때문이다. 검사를 못 한 세션을 CLEAN 으로 넘기지 않으려는
# 의도된 계약이라 그대로 두되, 여기에 박아 둔다. 누가 OFFLINE 을 0 으로 빼려
# 하면 걸린다.
import run_session  # noqa: E402

check("여섯이 다 OFFLINE 이어도 종료코드는 2 다",
      run_session.exit_code([{"status": "OFFLINE"}] * 6) == 2)
check("ERROR 도 2 다", run_session.exit_code([{"status": "ERROR"}]) == 2)
check("정상만 있을 때만 0 이다", run_session.exit_code([{"status": "NORMAL"}]) == 0)


print(f"\n통과 {ok} / 실패 {fail} / 건너뜀 {skipped}")
if skipped:
    print("  건너뛴 절이 있습니다. 위 사유를 보고 조건을 맞춘 뒤 다시 돌려 주세요.")
# 건너뛴 게 있으면 3 으로 끝낸다. 0 으로 끝내면 '다 봤다' 와 구분이 안 된다.
sys.exit(1 if fail else (3 if skipped else 0))
