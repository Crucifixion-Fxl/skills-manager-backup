"""Opt-in bounded metadata timing trace, never message bodies or credentials."""
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

STAGES = frozenset(('startup_onboarding_started', 'startup_onboarding_finished', 'startup_restore_started', 'startup_restore_finished', 'startup_feeds_started', 'feishu_received', 'relay_received', 'round_queued', 'round_refreshed',
    'round_started', 'round_finished', 'worker_started', 'people_ready', 'claim_ready',
    'directory_ready', 'members_started', 'members_finished', 'buzz_started', 'buzz_finished',
    'feishu_started', 'feishu_finished', 'outlets_started', 'outlets_finished',
    'mirror_publish_started', 'mirror_publish_finished', 'outlet_send_started', 'outlet_send_finished',
    'outlet_selected', 'outlet_attempt_started', 'outlet_attempt_finished'))
IDENT = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')


class LatencyTrace:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.resolve() != self.path:
            raise ValueError('timing trace must use a private absolute path')
        meta = self.path.parent.stat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
            raise ValueError('timing trace parent must be owned and mode 0700')
        self.parent_pin = (meta.st_dev, meta.st_ino)
        self.lock = threading.Lock()

    def record(self, binding, stage, *, source='', target='', sdk_at=None, kind=None):
        # Diagnostics never authorize work, mutate the ledger, or make a
        # successful business call fail. Saturation simply stops this capture.
        try:
            if stage not in STAGES or not isinstance(binding, str) or not IDENT.fullmatch(binding): return
            if any(v and (not isinstance(v, str) or not IDENT.fullmatch(v)) for v in (source, target)): return
            row = dict(binding=binding, stage=stage, wall=time.time(), monotonic=time.monotonic(), pid=os.getpid())
            if source: row['source'] = source
            if target: row['target'] = target
            if type(sdk_at) in (int, float) and math.isfinite(sdk_at): row['sdk_at'] = sdk_at
            if type(kind) is int and 0 <= kind <= 65535: row['kind'] = kind
            raw = (json.dumps(row, separators=(',', ':'))+'\n').encode()
            with self.lock:
                parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    meta = os.fstat(parent)
                    if ((meta.st_dev, meta.st_ino) != self.parent_pin or meta.st_uid != os.getuid()
                            or stat.S_IMODE(meta.st_mode) != 0o700): return
                    fd = os.open(self.path.name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=parent)
                    try:
                        meta = os.fstat(fd)
                        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_nlink != 1
                                or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_size + len(raw) > 8 * 2**20): return
                        os.write(fd, raw)
                    finally: os.close(fd)
                finally: os.close(parent)
        except Exception:
            return
