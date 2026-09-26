"""Inventory all project entries, including untracked/ignored files, without following links."""
import hashlib
import os
from pathlib import Path
import stat

# Only known tool bookkeeping is exempt. Do not exempt arbitrary dotfiles or secrets.
TOOL_FILES = {'.aider.input.history', '.aider.chat.history.md', '.aider.llm.history'}
TOOL_DIRS = {'.git', '.aider.tags.cache.v4'}
MAX_BYTES = 256 * 1024 * 1024


def inventory(project):
    entries = {}
    total = 0

    def visit(fd, prefix=''):
        nonlocal total
        for name in sorted(os.listdir(fd)):
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not prefix and ((name in TOOL_FILES and stat.S_ISREG(info.st_mode)) or
                               (name in TOOL_DIRS and stat.S_ISDIR(info.st_mode))):
                continue
            if name == '__pycache__' and stat.S_ISDIR(info.st_mode):
                # Do not hide arbitrary files inside a bytecode directory.
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if all(n.endswith('.pyc') and stat.S_ISREG(os.stat(n, dir_fd=child,
                               follow_symlinks=False).st_mode) for n in os.listdir(child)):
                        continue
                finally:
                    os.close(child)
            if stat.S_ISDIR(info.st_mode):
                entries[path] = {'type': 'directory'}
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, path + '/')
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, 'rb') as stream:
                    actual = os.fstat(stream.fileno())
                    if not stat.S_ISREG(actual.st_mode):
                        raise ValueError(f'File type changed during scope check: {path}')
                    while chunk := stream.read(65536):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError('Scope inventory exceeds 256 MiB')
                        digest.update(chunk)
                entries[path] = {'type': 'file', 'sha256': digest.hexdigest(),
                                 'executable': bool(actual.st_mode & 0o111)}
            elif stat.S_ISLNK(info.st_mode):
                entries[path] = {'type': 'symlink', 'target': os.readlink(name, dir_fd=fd)}
            else:
                entries[path] = {'type': 'special'}

    fd = os.open(Path(project), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(fd)
    finally:
        os.close(fd)
    return entries


def project_entries(entries):
    return {name: value for name, value in entries.items()
            if name != '_bench' and not name.startswith('_bench/')}


def scope_report(project, baseline, allowed, trusted_helpers, require_helpers=False):
    current = inventory(project)
    original = project_entries(inventory(baseline))
    source = project_entries(current)
    changed = sorted(name for name in original.keys() | source.keys()
                     if original.get(name) != source.get(name))
    violations = [name for name in changed if not any(
        name == rule or (rule.endswith('/') and name.startswith(rule)) for rule in allowed)]
    # Links and special files are never valid replacement source, even at allowed paths.
    violations += [name for name, value in source.items()
                   if value['type'] not in ('file', 'directory')]
    helpers = {name: value for name, value in current.items()
               if name == '_bench' or name.startswith('_bench/')}
    if helpers or require_helpers:
        violations += [name for name in helpers.keys() | trusted_helpers.keys()
                       if helpers.get(name) != trusted_helpers.get(name)]
    return {'passed': not violations, 'violations': sorted(set(violations)),
            'files_changed': [name for name in changed
                              if source.get(name, original.get(name))['type'] != 'directory']}
