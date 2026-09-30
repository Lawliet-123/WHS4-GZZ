"""Launch MSBuild with unique Windows environment keys (Path/PATH can coexist in hosts)."""
import argparse
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--msbuild', required=True)
parser.add_argument('--sdk', required=True)
parser.add_argument('--sentinel', action='store_true')
parser.add_argument('--unsigned', action='store_true')
parser.add_argument('--compile-only', action='store_true', help='Compile/link only; does not validate INF or sign a package')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
project = root / ('KernelSentinel/driver/KernelSentinel.vcxproj' if args.sentinel else
                  'validation/driver/KsValidationProbe.vcxproj')
env = {k.upper(): v for k, v in os.environ.items()}
command = [args.msbuild, str(project), '/m', '/t:Build', '/p:Configuration=Debug',
                         '/p:Platform=x64', '/p:WindowsTargetPlatformVersion=' + args.sdk,
                         '/p:TrackFileAccess=false', '/v:minimal']
if args.unsigned: command.append('/p:SignMode=Off')
if args.compile_only:
    command += ['/p:SkipPackageVerification=true', '/p:EnableInf2cat=false', '/p:SignMode=Off']
result = subprocess.run(command, env=env)
if result.returncode == 0:
    binary = root / ('KernelSentinel/build/x64/Debug/KernelSentinel.sys' if args.sentinel else
                     'bin/driver/KsValidationProbe.sys')
    if not binary.is_file():
        raise RuntimeError(f'MSBuild returned success but did not create {binary}')
    print(f'Built: {binary} ({binary.stat().st_size} bytes)', flush=True)
raise SystemExit(result.returncode)
