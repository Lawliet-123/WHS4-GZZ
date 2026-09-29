"""YARA 테스트용 무해한 자식 프로세스의 메모리 marker 버퍼.

표준입력 JSON으로 ON/OFF를 받아 자기 메모리만 바꾼다. 게임 접근이나 주입은
없으며, 부모는 자식이 보고한 실제 PID를 스캔한다.
"""
import ctypes
import json
import os
import sys

# marker가 없는 상태로 시작한다. 주소는 부모가 제어된 fixture의 메모리만
# 읽고 있음을 확인하는 데 쓰며, 운영 로그에 보낼 정보는 아니다.
region = (ctypes.c_ubyte * 4096)()
print(json.dumps({'ready':True,'pid':os.getpid(),'buffer_address':ctypes.addressof(region)}),flush=True)
for line in sys.stdin:
    message = json.loads(line)
    action = message['action']
    if action == 'on':
        # marker를 Python의 불변 평문 문자열로 남기지 않으려고 바이트 배열로
        # 전달받는다. 먼저 버퍼를 지워 이전 표식이 결과에 섞이지 않게 한다.
        ctypes.memset(ctypes.addressof(region),0,ctypes.sizeof(region))
        for i, value in enumerate(message['bytes']): region[i] = value
    elif action == 'off':
        # OFF 후에도 marker가 남아 있으면 잘못된 양성 결과가 되므로 0으로 채운다.
        ctypes.memset(ctypes.addressof(region),0,ctypes.sizeof(region))
    elif action == 'stop': break
    print(json.dumps({'ack':action}),flush=True)
