from pathlib import Path


def count(args):
    print(len(Path(args.path).read_text(encoding="utf-8").splitlines()))
    return 0
