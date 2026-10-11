"""Watch positions in the node-owned directory, written atomically.

The document holds no credential and no video text: only a digest per
watched channel, its uploads playlist ID, a bounded list of video IDs and the
last check time. A held advisory lock keeps a second watch of the same node
storage (listener or watch_uploads request) from polling concurrently.
"""

import fcntl
import json
import os
from pathlib import Path
import stat

from . import logic

STATE_NAME = "youtube-watch.json"
LOCK_NAME = "youtube-watch.lock"
STATE_LIMIT = 1048576


class WatchStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = None

    def acquire(self):
        """Take the exclusive listener lock; False when another listener holds it."""
        if self.directory.is_symlink() or not self.directory.is_dir():
            logic.fail("storage")
        try:
            descriptor = os.open(self.directory / LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError:
            logic.fail("storage")
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            logic.fail("storage")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        self.lock = descriptor
        return True

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def load(self):
        """Return the validated document; a missing file is an empty state, never a guess."""
        path = self.directory / STATE_NAME
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            return {"schema_version": 1, "watches": {}}
        except OSError:
            logic.fail("state_invalid")
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > STATE_LIMIT:
                logic.fail("state_invalid")
            raw = stream.read(STATE_LIMIT + 1)
        try:
            value = logic.strict_json(raw, STATE_LIMIT, "state_invalid")
        except logic.YouTubeError:
            logic.fail("state_invalid")
        return logic.watch_document(value)

    def save(self, document):
        """Write a complete validated document with fsync and an atomic rename."""
        logic.watch_document(document)
        data = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        if len(data) > STATE_LIMIT:
            logic.fail("storage")
        temporary = self.directory / (STATE_NAME + ".tmp")
        try:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / STATE_NAME)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            logic.fail("storage")
