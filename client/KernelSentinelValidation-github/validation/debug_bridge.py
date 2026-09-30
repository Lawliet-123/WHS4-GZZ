"""Narrow client for the host's guarded DbgEng fixture bridge."""
import json
from pathlib import Path
import socket


class Bridge:
    def __init__(self, config):
        settings = json.loads(Path(config).read_text(encoding='utf-8-sig'))
        self.host = settings['host']
        self.port = int(settings['port'])
        self.token = settings['token']
        if not self.host.startswith('192.168.') or not 1 <= self.port <= 65535 or len(self.token) != 64:
            raise ValueError('Invalid VMnet8 bridge configuration')

    def request(self, action):
        if action not in {'INFO', 'ENTRY_MUTATE', 'ENTRY_RESTORE',
                          'DISPATCH_MUTATE', 'DISPATCH_RESTORE'}:
            raise ValueError('Unsupported bridge action')
        with socket.create_connection((self.host, self.port), timeout=60) as connection:
            connection.settimeout(60)
            connection.sendall(f'{self.token} {action}\n'.encode('ascii'))
            response = bytearray()
            while len(response) < 1024 and b'\n' not in response:
                chunk = connection.recv(1024)
                if not chunk:
                    break
                response.extend(chunk)
        message = response.decode('ascii', 'replace').strip()
        if not message.startswith('OK '):
            raise RuntimeError(f'Debugger bridge {action} failed: {message}')
        return message[3:]

    def info(self):
        fields = dict(part.split('=', 1) for part in self.request('INFO').split())
        result = {key: int(fields[key], 16) for key in ('entry', 'dispatch', 'replacement')}
        if any(value < 0xffff800000000000 for value in result.values()):
            raise ValueError('Bridge returned a non-kernel address')
        return result
