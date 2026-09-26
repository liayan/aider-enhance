import argparse
import hashlib
from pathlib import Path
import sys


def make_parser():
    parser = argparse.ArgumentParser(prog="mycli", description="Small text utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    hello = sub.add_parser("greet", help="greet a person")
    hello.add_argument("name")
    hello.add_argument("--upper", action="store_true")
    add = sub.add_parser("add", help="sum integers")
    add.add_argument("numbers", type=int, nargs="+")
    count = sub.add_parser("count", help="count input lines")
    count.add_argument("path")
    copy = sub.add_parser("copy", help="copy a file")
    copy.add_argument("source")
    copy.add_argument("destination")
    copy.add_argument("--force", action="store_true")
    digest = sub.add_parser("digest", help="hash a file")
    digest.add_argument("path")
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    try:
        if args.command == "greet":
            text = f"Hello, {args.name}!"
            print(text.upper() if args.upper else text)
            return 0
        if args.command == "add":
            print(sum(args.numbers))
            return 0
        if args.command == "count":
            print(len(Path(args.path).read_text(encoding="utf-8").splitlines()))
            return 0
        if args.command == "copy":
            destination = Path(args.destination)
            if destination.exists() and not args.force:
                print("mycli: destination exists (use --force)", file=sys.stderr)
                return 3
            destination.write_bytes(Path(args.source).read_bytes())
            print("copied")
            return 0
        if args.command == "digest":
            print(hashlib.sha256(Path(args.path).read_bytes()).hexdigest())
            return 0
    except (OSError, UnicodeError) as exc:
        print(f"mycli: {type(exc).__name__}", file=sys.stderr)
        return 2
