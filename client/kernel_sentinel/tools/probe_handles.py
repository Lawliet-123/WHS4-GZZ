"""Requests a fresh handle, then closes it. No game memory is read or written."""
import argparse
import ctypes as C
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.windows import Windows

parser = argparse.ArgumentParser()
parser.add_argument('--pid', type=int, required=True)
args = parser.parse_args()
api = Windows()
for label, mask in [('query', 0x1000), ('read', 0x10), ('write_and_operation', 0x28)]:
    h = api.open_process(mask, False, args.pid)
    if not h:
        print(label, 'OpenProcess failed:', C.get_last_error())
    else:
        print(label, 'handle opened; inspect handle_post.granted_access for actual rights')
        api.close(h)
