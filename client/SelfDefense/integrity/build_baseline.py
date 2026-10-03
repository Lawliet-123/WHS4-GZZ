"""Offline release step. Explicit files/scopes; never refreshes an existing baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

if __package__:
    from .baseline import IntegrityError, make_baseline, discover_files, relative_name
else:
    from baseline import IntegrityError, make_baseline, discover_files, relative_name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--file", action="append", default=[], help="Root-relative file; repeat to include more")
    parser.add_argument("--file-list", type=Path, help="JSON array of root-relative paths")
    parser.add_argument("--include-dir", action="append", default=[], help="Root-relative code directory; repeat")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    # abspath preserves reparse-point components for the scanner's checks.
    root = Path(os.path.abspath(args.root))
    try:
        names = args.file
        if args.file_list:
            with args.file_list.open("rb") as stream:
                data = stream.read(512 * 1024 + 1)
            if len(data) > 512 * 1024:
                raise IntegrityError("FILE_LIST_TOO_LARGE")
            supplied = json.loads(data.decode("utf-8"))
            if type(supplied) is not list:
                raise IntegrityError("INVALID_FILE_LIST")
            names = names + supplied
        if args.output.exists():
            raise IntegrityError("BASELINE_ALREADY_EXISTS")
        for name in names:
            relative_name(name)
        if args.include_dir:
            names = sorted(set(names) | set(discover_files(root, args.include_dir)))
        data = make_baseline(root, names, args.release_id, scope_dirs=args.include_dir)
        if any((root / n).resolve() == args.output.resolve() for n in names):
            raise IntegrityError("BASELINE_CANNOT_INCLUDE_ITSELF")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(data)
        print(json.dumps({"baseline": str(args.output), "baseline_sha256": hashlib.sha256(data).hexdigest(),
                          "release_id": args.release_id, "files": len(names),
                          "trust": "unsigned_manifest_with_external_sha256_pin"}))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        code = str(exc) if isinstance(exc, IntegrityError) else "BASELINE_BUILD_FAILED"
        print("[Integrity] " + code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
