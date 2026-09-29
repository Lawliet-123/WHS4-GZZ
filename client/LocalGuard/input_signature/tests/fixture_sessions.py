"""무해한 자식 프로세스로 YARA ON/OFF 기록을 재현하는 테스트 도구.

실제 게임이나 핵을 실행하지 않는다. 메모리에 넣는 고정 marker와 수동 표식
시각을 비교해 검사기·세션 출력 형식을 검증할 뿐 실전 탐지율을 뜻하지 않는다.
"""
import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from replay_events import ReplaySession, json_line
from yara_scanner import load_rules, scan_once
from windows_process import ProcessIdentity
from tests.validate_session import validate

ROOT = Path(__file__).resolve().parents[1]
MARKER = b'LOCALGUARD_TEST_ONLY_7837A742_7C05_46AB_9E2B'


class ControlledTarget:
    """표식 버퍼를 가진 자식 프로세스를 띄우고 명령·응답으로 제어한다."""
    def __enter__(self):
        """자식이 보고한 실제 PID를 열어 fixture 준비 완료를 확인한다."""
        self.child = subprocess.Popen([sys.executable, '-u', str(ROOT/'tests/fixtures/memory_target.py')],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding='utf-8',
                                      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.messages = queue.Queue()
        def read():
            for line in self.child.stdout: self.messages.put(line)
        self.reader = threading.Thread(target=read,daemon=True); self.reader.start()
        try:
            hello = self.receive()
            if not hello.get('ready'): raise RuntimeError('fixture did not start')
            self.buffer_address = hello['buffer_address']
            # Windows venv python.exe can be a redirector spawning the actual interpreter.
            # The controlled child reports its own PID; do not scan the redirector.
            self.process = ProcessIdentity(hello['pid'])
            return self
        except BaseException:
            self.__exit__(None,None,None); raise

    def receive(self):
        """자식의 JSON 응답을 제한 시간 안에 받아 무한 대기를 피한다."""
        try: return json.loads(self.messages.get(timeout=5))
        except queue.Empty: raise RuntimeError('fixture response timeout') from None

    def command(self, action, marker=MARKER):
        """ON이면 marker 바이트를 쓰고 OFF면 지우도록 자식에게 요청한다."""
        item = {'action':action}
        if action == 'on':
            if len(marker) > 4096: raise ValueError('fixture marker too large')
            item['bytes'] = list(marker)
        self.child.stdin.write(json.dumps(item)+'\n'); self.child.stdin.flush()
        reply = self.receive()
        if reply.get('ack') != action: raise RuntimeError('unexpected fixture response')

    def __exit__(self, *args):
        """우리가 만든 자식만 종료하고 읽기 스레드·프로세스 핸들을 정리한다."""
        if hasattr(self,'process'): self.process.close()
        if self.child.poll() is None:
            self.child.stdin.write('{"action":"stop"}\n'); self.child.stdin.flush()
            try: self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.terminate(); self.child.wait(timeout=3)  # Only our own harmless child.
        self.child.stdin.close()
        self.reader.join(timeout=2)
        self.child.stdout.close()


def generate(root):
    """정상 marker 없음/양성 marker 있음 세션을 만들고 출력 형식을 검증한다."""
    rules, info = load_rules([ROOT/'tests/fixtures/marker.yar'])
    results = []
    for positive in (False,True):
        sid = 'fixture_yara_pattern_001' if positive else 'fixture_yara_normal_001'
        session = ReplaySession(root,sid,label='cheat' if positive else 'normal',
                                cheat_name='HARMLESS_TEST_MARKER' if positive else '',
                                player_id='fixture_player',data_origin='controlled_fixture',modules=['localguard_yara'])
        session.update(yara=info,test_rules_present=True,
                       notice='Not game data. The cheat label refers ONLY to a harmless synthetic memory marker.')
        code = 0
        try:
            with (session.raw/'yara_scan.jsonl').open('x',encoding='utf-8') as raw, ControlledTarget() as target:
                session.update(process=target.process.initial)
                for index in range(10):
                    if positive and index in (3,7):
                        action = 'on' if index == 3 else 'off'
                        target.command(action)
                        stamp = session.mark(action,source='controlled_fixture_ack')
                        json_line(raw,{'type':'fixture_marker','timestamp_ms':stamp,'action':action})
                    event = scan_once(rules,target.process,session,raw,timeout=3)
                    if event is None: raise RuntimeError('fixture scan failed; see raw log')
                    # marker를 켠 3~6번째 평가만 양성이어야 한다. OFF 이후
                    # 재검사가 0점으로 돌아오는지도 확인한다.
                    expected = 3 if positive and 3 <= index < 7 else 0
                    if event['raw_score'] != expected:
                        raise RuntimeError(f'fixture unexpected score: {event["raw_score"]} != {expected}')
                    time.sleep(.1)
        except BaseException:
            code = 1; session.error('fixture_failed'); raise
        finally:
            session.finish(code)
        result = validate(session.path)
        if not result['valid_format']: raise RuntimeError(str(result))
        results.append({'session':sid,**result})
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-root',type=Path,default=ROOT/'fixture-sessions')
    args = parser.parse_args()
    print(json.dumps(generate(args.log_root),ensure_ascii=False,indent=2))
