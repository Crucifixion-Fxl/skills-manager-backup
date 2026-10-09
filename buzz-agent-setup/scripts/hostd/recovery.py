"""Metadata-only retry planning; timers are armed only for identified work.

The caller supplies validated StateAdapter state and explicit partial-read
phases. This helper does not send, advance cursors, acknowledge, or mutate state.
UNKNOWN/expired outcomes require a separate readback-only receipt reconciler;
re-running an entire send phase is not authorization to resend them.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import buzz_feishu_group_sync as gs

PHASES = frozenset({'buzz', 'feishu', 'members'})


@dataclass(frozen=True)
class RecoveryPlan:
    phases: frozenset[str]
    next_retry_at: int | None
    reasons: tuple[str, ...]


def plan_recovery(state, *, now, partial_reads=(), pending_targets=False, attempt=0, feishu_ingest_retry_at=None):
    """Select retries from unsettled metadata, not an aggregate error counter.

    Marker timestamps retain their original retry window; planning never creates
    a new key or extends idempotency. Signed member events retain their exact
    original event, and persistent refusal blocks are not retried automatically.
    """
    if (not isinstance(state, gs.State) or type(now) is not int or not 0 <= now < 2**63
            or type(attempt) is not int or attempt < 0 or type(pending_targets) is not bool
            or not isinstance(partial_reads, (tuple, list, set, frozenset))
            or any(not isinstance(phase, str) or phase not in PHASES for phase in partial_reads)):
        raise ValueError('恢复计划输入无法验证；怎么解决：使用已验证的状态与明确未完成的读取阶段。复制给 AI：检查 hostd 恢复计划输入，不要显示消息正文。')
    phases = set(partial_reads)
    reasons = {'partial_' + phase + '_read' for phase in partial_reads}
    if pending_targets:
        phases.add('feishu'); reasons.add('pending_target_read')
    if any(source not in state.b2f for source in state.unresolved):
        # A dependency hold is unsent work, not a partial read or authority to
        # send. The next round must freshly resolve its actual native root.
        phases.add('buzz'); reasons.add('held_buzz_dependency')
    windows = ((('b2f', 'e2f', 'images'), 'buzz', gs.FEISHU_RETRY_WINDOW_SECONDS),
               (('f2b', 'f2r'), 'feishu', gs.RELAY_CLOCK_SKEW_SECONDS - 60),
               (('agent_intros',), 'members', gs.FEISHU_RETRY_WINDOW_SECONDS))
    for fields, phase, window in windows:
        for field in fields:
            for value in getattr(state, field).values():
                if not isinstance(value, str):
                    continue  # validated state is required; never interpret arbitrary values
                if value == gs.UNKNOWN:
                    reasons.add('readback_required')
                    continue
                match = re.match(r'^(?:pending|retry):([0-9]{1,12})(?::|$)', value)
                if match is None:
                    continue
                first = int(match[1])
                if 0 <= now - first <= window:
                    phases.add(phase); reasons.add('unsettled_' + phase)
                elif first <= now:
                    reasons.add('readback_required')
    for operation, event in state.member_events.items():
        if operation in state.member_event_blocks:
            reasons.add('member_retry_blocked')
            continue
        created = event.get('created_at') if isinstance(event, dict) else None
        if type(created) is int and 0 <= now - created <= gs.RELAY_CLOCK_SKEW_SECONDS - 60:
            phases.add('members'); reasons.add('signed_member_retry')
        else:
            reasons.add('readback_required')
    if feishu_ingest_retry_at is not None:
        if type(feishu_ingest_retry_at) is not int or feishu_ingest_retry_at < 0:
            raise ValueError('invalid ingest retry time')
        phases.add('feishu'); reasons.add('retained_feishu_source')
    delay = min(60, 2**min(attempt, 6))
    if reasons == {'retained_feishu_source'}:
        delay = max(1, min(300, feishu_ingest_retry_at - now))
    return RecoveryPlan(frozenset(phases), now + delay if phases else None, tuple(sorted(reasons)))
