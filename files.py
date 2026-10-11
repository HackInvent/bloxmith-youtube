"""Local files: a bounded upload source inside upload_root, read chunk by chunk, and the node storage directory."""

import os
from pathlib import Path
import stat

from . import logic


class Source:
    """One open regular file: read by offset, checked unchanged at the end, always closed."""

    def __init__(self, fd, size, name, stamp):
        self.fd, self.size, self.name, self.stamp = fd, size, name, stamp

    def read(self, offset, length):
        data = os.pread(self.fd, length, offset)
        if len(data) != length:
            logic.fail("file_changed")
        return data

    def verify(self):
        info = os.fstat(self.fd)
        if (info.st_size, info.st_mtime_ns) != self.stamp:
            logic.fail("file_changed")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def open_upload(value, config):
    """Open one regular file inside upload_root without following a swapped final symlink."""
    if not config["upload_root"]:
        logic.fail("upload_root_required")
    try:
        root = Path(config["upload_root"]).resolve(strict=True)
    except (OSError, RuntimeError):
        logic.fail("path", "upload_root does not exist; nothing was sent.")
    if not root.is_dir():
        logic.fail("path", "upload_root must be a directory; nothing was sent.")
    candidate = Path(value).expanduser()
    try:
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
    except (OSError, RuntimeError):
        logic.fail("path", "The file to upload does not exist; nothing was sent.")
    if not resolved.is_relative_to(root):
        logic.fail("path_denied")
    try:
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        logic.fail("path", "The file to upload cannot be opened; nothing was sent.")
    try:
        info = os.fstat(fd)
        current = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            logic.fail("path", "Only regular files can be uploaded; nothing was sent.")
        if info.st_size == 0:
            logic.fail("path", "The file to upload is empty; nothing was sent.")
        if info.st_size > config["max_upload_bytes"]:
            logic.fail("file_too_large")
        return Source(fd, info.st_size, resolved.name, (info.st_size, info.st_mtime_ns))
    except BaseException:
        os.close(fd)
        raise


def storage_directory(storage):
    """Resolve the node storage directory before any request, so a missing scope sends nothing."""
    if not callable(storage):
        logic.fail("storage")
    try:
        directory = Path(storage())
    except Exception:
        raise logic.YouTubeError("storage") from None
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        logic.fail("storage", "The block storage directory is not a real directory.")
    return directory
