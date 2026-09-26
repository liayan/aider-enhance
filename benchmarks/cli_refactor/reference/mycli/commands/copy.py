from pathlib import Path
import sys


def copy(args):
    destination = Path(args.destination)
    if destination.exists() and not args.force:
        print("mycli: destination exists (use --force)", file=sys.stderr)
        return 3
    destination.write_bytes(Path(args.source).read_bytes())
    print("copied")
    return 0
