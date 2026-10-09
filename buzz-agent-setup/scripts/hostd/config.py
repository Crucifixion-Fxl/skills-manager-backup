"""Validated hostd-only compatibility for an observed human membership policy."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs
try:
    from .safety import read_owned, notice
except ImportError:
    from safety import read_owned, notice


def validate_config(value):
    """Validate the legacy contract plus exactly the observed one-way human rule.

    The extension is removed only from the temporary validation copy, then
    retained in the returned runtime configuration. Unknown policies fail closed.
    No credential identity or other optional field is interpreted here.
    """
    if not isinstance(value, dict):
        raise gs.GroupSyncError(notice('config'))
    cfg = copy.deepcopy(value)
    present = 'human_membership_sync' in cfg
    policy = cfg.pop('human_membership_sync', None)
    if present and policy != 'feishu_to_buzz':
        raise gs.GroupSyncError(notice('config'))
    try:
        validated = gs._validated_config(cfg)
    except Exception:
        raise gs.GroupSyncError(notice('config')) from None
    if present:
        validated['human_membership_sync'] = policy
    return validated


def load_config(path):
    try:
        value = json.loads(read_owned(path))
    except Exception:
        raise gs.GroupSyncError(notice('config')) from None
    return validate_config(value)
