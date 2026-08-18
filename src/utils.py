# -*- coding: utf-8 -*-
"""Shared utilities for the Job Assistant pipeline."""
import json, os, tempfile


def write_json_atomic(path, obj, indent=2):
    """Write JSON atomically via a temp file in the same directory.

    On Windows and POSIX, os.replace() is atomic when src and dst are on
    the same filesystem — the file is never left in a half-written state.
    """
    abs_path = os.path.abspath(path)
    dir_ = os.path.dirname(abs_path)
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, abs_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
