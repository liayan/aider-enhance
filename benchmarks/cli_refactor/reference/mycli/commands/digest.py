import hashlib
from pathlib import Path


def digest(args):
    print(hashlib.sha256(Path(args.path).read_bytes()).hexdigest())
    return 0
