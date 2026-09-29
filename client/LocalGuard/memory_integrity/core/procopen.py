"""게임 프로세스를 읽기 전용 권한으로 연다. pymem.Pymem(이름) 대신 쓴다.

왜 필요한가
  pymem.Pymem(이름) 은 PROCESS_ALL_ACCESS(0x001F3FFF) 로 열고 SeDebugPrivilege 까지 켠다.
  우리 탐지기는 게임 메모리를 읽고 모듈 목록을 볼 뿐이다(read_bytes, list_modules,
  process_base 만 쓰고 쓰기 계열 호출은 한 군데도 없다). 그런데 쓰지도 않는 쓰기·조작·
  스레드 생성 권한까지 열어 두는 바람에 1번 external_access 가 우리 탐지기를 핵으로
  잡았다(2026-09-27, 35건 전부 우리 python.exe, 전부 0x001F3FFF, raw_score 8).

무엇을 여나
  PROCESS_QUERY_INFORMATION (0x0400)  모듈 목록, WoW64 확인
  PROCESS_VM_READ           (0x0010)  메모리 읽기

  1번은 쓰기(2)·조작(2)·스레드 생성(3)에만 점수를 주고, 0점이면 기록을 남기지 않는다
  (external_access/process_access/access_rights.py, detector.py). 그래서 1번 코드를 고치거나
  허용 목록에 우리를 넣을 필요가 없다. 우리를 봐 달라는 게 아니라, 애초에 의심받을
  권한을 들고 있지 않는 것이다.

디버그 권한은 켜지 않는다
  같은 사용자로 뜬 게임을 여는 데는 필요 없다. 게임이 관리자 권한으로 떠 있고 우리가
  아니면 디버그 권한은 켤 수도 없으니 도움이 안 된다. 필요 없는 권한은 들지 않는다.

한계
  1번이 나중에 VM_READ 단독 핸들에도 점수를 주면(ESP 는 읽기만 한다) 우리도 다시 잡힌다.
  그때는 런처가 남기는 anticheat_pids.json 을 근거로 "런처의 자식인지"를 확인하는 방식이
  필요하다. 파일만 믿으면 핵도 자기 PID 를 적어 넣을 수 있다.
"""
import pymem
import pymem.exception
import pymem.process

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
READ_ONLY_ACCESS = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ   # 0x0410


def open_pid(pid):
    """pid 를 읽기 전용으로 연 pymem.Pymem 을 돌려준다."""
    handle = pymem.process.open(pid, debug=False, process_access=READ_ONLY_ACCESS)
    if not handle:
        raise pymem.exception.CouldNotOpenProcess(pid)
    pm = pymem.Pymem()
    pm.process_id = pid
    pm.process_handle = handle
    pm.check_wow64()
    return pm


def open_game(exe_name):
    """실행 파일 이름으로 찾아 읽기 전용으로 연다.

    pymem.Pymem(이름) 은 이름 일부만 맞아도 연다. 여기서는 정확히 같은 이름만 연다.
    못 찾으면 pymem 과 같은 ProcessNotFound 를 내서 호출부의 예외 처리를 그대로 쓴다.
    """
    entry = pymem.process.process_from_name(exe_name, exact_match=True)
    if not entry:
        raise pymem.exception.ProcessNotFound(exe_name)
    return open_pid(entry.th32ProcessID)
