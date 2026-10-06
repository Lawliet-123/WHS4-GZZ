"""Render only the hostname; never reads or embeds an API token."""
import argparse
import re
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--stage", choices=("bootstrap", "https"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    labels = args.domain.split(".")
    if (len(labels) < 2 or len(args.domain) > 253
            or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", x) for x in labels)):
        parser.error("domain must be a DNS hostname without scheme, path, or port")
    template = Path(__file__).parent / "nginx" / f"dashboard-{args.stage}.conf.template"
    args.output.write_text(template.read_text(encoding="utf-8").replace("__DOMAIN__", args.domain), encoding="utf-8")

if __name__ == "__main__":
    main()
