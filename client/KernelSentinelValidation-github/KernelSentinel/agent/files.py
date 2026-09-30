"""Bounded asynchronous on-disk evidence; never reads another process's memory."""
import hashlib
import base64
import json
import os
import queue
import subprocess
import threading
import time

class FileWorker:
    def __init__(self, api, signatures=False):
        self.api, self.signatures = api, signatures
        self.tasks, self.results = queue.Queue(1024), queue.Queue(256)
        self.stop_event = threading.Event()
        self.seen = {}
        self.skipped = 0
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()
    def submit(self, event):
        path = event.get('path', '')
        key = (path.lower(), event['type'])
        now = time.monotonic()
        if now - self.seen.get(key, -1000) < 60:
            return
        if len(self.seen) > 8192:
            self.seen = {k: v for k, v in self.seen.items() if now-v < 60}
        try:
            self.tasks.put_nowait(dict(event))
            self.seen[key] = now
        except queue.Full:
            self.skipped += 1
    def run(self):
        while not self.stop_event.is_set():
            try:
                e = self.tasks.get(timeout=.2)
            except queue.Empty:
                continue
            r = {'type': 'file_evidence', 'image_kind': 'kernel_image' if e['type'] in ('kernel_image', 'driver_snapshot') else 'game_image',
                 'path': e.get('path', ''), 'source_sequence': e.get('sequence'),
                 'generation': e.get('generation'), 'sha256': None,
                 'signature': 'not_checked', 'scope': 'on_disk_file_at_analysis_time'}
            try:
                path = self.api.local_path(r['path'])
                before = os.stat(path)
                if before.st_size > 256 * 1024 * 1024:
                    raise ValueError('file exceeds 256 MiB analysis limit')
                digest = hashlib.sha256()
                read = 0
                with open(path, 'rb') as f:
                    while chunk := f.read(1024*1024):
                        read += len(chunk)
                        if read > 256 * 1024 * 1024:
                            raise ValueError('file grew beyond analysis limit')
                        digest.update(chunk)
                if self.signatures:
                    # No file path interpolation into PowerShell code. Input is JSON on stdin.
                    # An ASCII envelope also works when CREATE_NO_WINDOW has no
                    # console code page to set; preserve localized status messages.
                    script = "$p = [Console]::In.ReadToEnd() | ConvertFrom-Json; $s = Get-AuthenticodeSignature -LiteralPath $p -ErrorAction Stop; $j = [pscustomobject]@{Status=[int]$s.Status; StatusName=[string]$s.Status; StatusMessage=$s.StatusMessage; SignerThumbprint=$(if($s.SignerCertificate){$s.SignerCertificate.Thumbprint}else{$null})} | ConvertTo-Json -Compress; [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($j))"
                    executable = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
                    # PowerShell 7 may pass incompatible Core module paths to 5.1.
                    # Only this child process needs the built-in Windows modules.
                    child_env = os.environ.copy()
                    child_env['PSModulePath'] = os.path.join(os.path.dirname(executable), 'Modules')
                    result = subprocess.run([executable, '-NoProfile', '-NonInteractive',
                                             '-ExecutionPolicy', 'Bypass', '-Command', script],
                        input=json.dumps(path, ensure_ascii=True), capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=12, creationflags=0x08000000, env=child_env)
                    if result.returncode == 0:
                        try:
                            r['signature'] = json.loads(base64.b64decode(result.stdout.strip(), validate=True).decode('utf-8'))
                        except ValueError:
                            r['signature'] = 'unavailable_invalid_response'
                    else:
                        r['signature'] = 'unavailable_command_failed'
                after = os.stat(path)
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('file changed during analysis')
                r['sha256'] = digest.hexdigest()
                r['resolved_path'] = path
                r['size'] = read
            except (OSError, ValueError, subprocess.SubprocessError) as ex:
                r['error'] = str(ex)
            while not self.stop_event.is_set():
                try:
                    self.results.put(r, timeout=.2)
                    break
                except queue.Full:
                    continue
            self.tasks.task_done()
    def close(self):
        self.stop_event.set()
        self.worker.join(timeout=1)
        return self.tasks.qsize() + int(self.worker.is_alive())
