# -*- coding: utf-8 -*-
"""Shared utilities for the Job Assistant pipeline."""
import json, os, tempfile, shutil


def backup_store(store_path, keep=5):
    """Copy store.json to data/backups/ with a timestamp, keep last `keep` copies."""
    if not os.path.exists(store_path):
        return
    from datetime import datetime
    backups_dir = os.path.join(os.path.dirname(os.path.abspath(store_path)), "backups")
    os.makedirs(backups_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    shutil.copy2(store_path, os.path.join(backups_dir, f"store-{ts}.json"))
    files = sorted(f for f in os.listdir(backups_dir)
                   if f.startswith("store-") and f.endswith(".json"))
    for old in files[:-keep]:
        try:
            os.unlink(os.path.join(backups_dir, old))
        except OSError:
            pass


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
