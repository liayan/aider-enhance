"""Benchmark-only entrypoint for pinned Aider, without ambient user config files.

The public CLI searches the home directory even with --config / --env-file.
Keep this patch confined to the generation subprocess; preserve builtin model
metadata while disabling user config, dotenv and model metadata/settings files.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import aider.main
from aider_local.cli import main

get_parser = aider.main.get_parser

aider.main.get_parser = lambda default_config_files, git_root: get_parser([], git_root)
aider.main.load_dotenv_files = lambda *args, **kwargs: []
aider.main.generate_search_path_list = lambda *args, **kwargs: []

if __name__ == '__main__':
    sys.exit(main())
