"""Produce a diagnostic copy of the supplied v3 client; never overwrite its source."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

REPLACEMENTS = {
    'OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid)':
        'CcpAuditOpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid)',
    'QueryFullProcessImageNameW(process, 0, image, &imageLength)':
        'CcpAuditQueryFullProcessImageNameW(process, 0, image, &imageLength, pid)',
    'GetProcessTimes(process, &created, &exited, &kernel, &user)':
        'CcpAuditGetProcessTimes(process, &created, &exited, &kernel, &user, pid)',
    'CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)':
        'CcpAuditCreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)',
    'Module32FirstW(snapshot, &module)': 'CcpAuditModule32FirstW(snapshot, &module, pid)',
    'int wmain(int argc, wchar_t** argv) {':
        'int wmain(int argc, wchar_t** argv) {\n'
        '    CcpAuditSession audit;\n'
        '    if (!audit.ok()) { std::wcerr << L"Cannot create API audit log\\n"; return 3; }',
}

def instrument(source):
    if 'client_call_trace.h' in source: raise ValueError('client is already instrumented')
    for old, new in REPLACEMENTS.items():
        if source.count(old) != 1:
            raise ValueError(f'expected exactly one source pattern: {old}; inspect this client version')
        source = source.replace(old, new)
    return '#include "client_call_trace.h"\n' + source

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True); p.add_argument('--out', required=True)
    a = p.parse_args(); source = Path(a.source)
    result = instrument(source.read_text(encoding='utf-8-sig'))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=False)
    target = out / source.name; target.write_text(result, encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('client_call_trace.h'), out/'client_call_trace.h')
    (out/'instrumentation.json').write_text(json.dumps({
        'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'instrumented_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'meaning': 'API intervals only; native stack and actual kernel reads are not observed'}, indent=2)+'\n')
    print(f'Created diagnostic copy: {target}; copy both C++ and header into a separate client project and rebuild.')

if __name__ == '__main__': main()
