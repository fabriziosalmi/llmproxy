"""Crash-safe file replacement, in one place.

`open(path, "w")` destroys the file before it writes a byte, so an
interruption between the two leaves a truncated file on disk. For the formats
this project persists that is worse than an obvious corruption: a truncated
YAML document frequently still parses, yielding a valid-looking document that
is missing whatever came after the cut.

That mattered twice here. The config editor wrote config.yaml and its backup
this way, so a torn backup would restore silently wrong. And the plugin
manifests were written this way at four sites — one of them reachable over
HTTP through the toggle endpoint — while carrying the SHA-256 pins that detect
plugin tampering. A pin lost to a torn write is not fail-closed: the loader
treats an absent pin as a warning and loads the plugin anyway.

The helper started life inside proxy/routes/config.py; it lives here so the
plugin engine and the plugin routes use the same one rather than growing their
own.
"""

from __future__ import annotations

import contextlib
import os
import tempfile


def atomic_write(content: str, target: str, directory: str, prefix: str) -> None:
    """Write `content` to `target` so a reader never sees a partial file.

    Temp file in the same directory — so os.replace is a rename and not a copy
    across filesystems — flushed and fsynced before the rename, so the data is
    on disk before anything points at it. Then the directory is fsynced, so the
    name that points at those bytes is as durable as the bytes.

    On any failure the temp file is removed and the exception propagates; the
    target is left exactly as it was.
    """
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
