import argparse
import sys
from . import commands


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
        return getattr(commands, args.command)(args)
    except (OSError, UnicodeError) as exc:
        print(f"mycli: {type(exc).__name__}", file=sys.stderr)
        return 2
