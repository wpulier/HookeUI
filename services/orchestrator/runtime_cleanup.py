"""Withdraw managed runtime directories before deleting them; never own their parent.

Callers hold their existing generation/retention boundary. Interrupted cleanup stays
under a reserved name, separate from any newly prepared copy of that generation.
"""
from pathlib import Path
import re
import shutil
import stat
import uuid


def pending(folder: Path) -> bool:
    return bool(re.fullmatch(r'\.removing-[A-Za-z0-9][A-Za-z0-9._-]{0,191}', folder.name))


def withdraw(folders: list[Path]) -> list[Path]:
    destinations = [(folder, folder if pending(folder) else folder.with_name(
        '.removing-' + folder.name + '-' + uuid.uuid4().hex)) for folder in folders]
    moved = []
    try:
        for original, destination in destinations:
            if original.is_symlink() or not original.is_dir():
                raise OSError('The runtime directory changed; refresh storage before removing it')
            if original != destination:
                # macOS 15 refuses to rename a folder that is itself read-only (a published download), even within
                # its own parent: it is made writable for the move, and given back its mode if the move is undone.
                mode = stat.S_IMODE(original.stat().st_mode)
                original.chmod(mode | stat.S_IWUSR)
                try:
                    original.rename(destination)
                except OSError:
                    original.chmod(mode)
                    raise
                moved.append((original, destination, mode))
    except OSError:
        for original, destination, mode in reversed(moved):
            destination.rename(original)
            original.chmod(mode)
        raise
    return [destination for _, destination in destinations]


def discard(folder: Path) -> None:
    if not pending(folder) or folder.is_symlink():
        raise OSError('Only a withdrawn runtime directory can be cleaned up')
    # Unlinking requires writable directories, not writable files. Shared hardlinked
    # package/cache files must retain their original permissions in every other copy.
    for directory in [folder, *folder.rglob('*')]:
        if not directory.is_symlink() and directory.is_dir():
            directory.chmod(directory.stat().st_mode | stat.S_IWUSR)
    shutil.rmtree(folder)
