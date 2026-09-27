"""Harmless child holding a caller-supplied marker. No game access or injection."""
import ctypes
import json
import os
import sys

region = (ctypes.c_ubyte * 4096)()
print(json.dumps({'ready':True,'pid':os.getpid(),'buffer_address':ctypes.addressof(region)}),flush=True)
for line in sys.stdin:
    message = json.loads(line)
    action = message['action']
    if action == 'on':
        # Encoded input avoids keeping an immutable plaintext marker in Python strings.
        ctypes.memset(ctypes.addressof(region),0,ctypes.sizeof(region))
        for i, value in enumerate(message['bytes']): region[i] = value
    elif action == 'off':
        ctypes.memset(ctypes.addressof(region),0,ctypes.sizeof(region))
    elif action == 'stop': break
    print(json.dumps({'ack':action}),flush=True)
