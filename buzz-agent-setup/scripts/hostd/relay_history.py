"""Bounded verified NIP-01 interval reads; no mutation or negative cache.

The relay's supported history contract is newest-first, inclusive since/until,
with at most 1000 events per filter. A full page's boundary second is read to
completion before advancing. Dense seconds that cannot fit even one complete
bounded response remain UNKNOWN, never skipped via ``until = second - 1``.
Multiple requests are not an atomic snapshot: callers deciding absence before a
new effect must require_single_response. Paging is for positive event discovery.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import buzz_feishu_group_sync as gs

PAGE_SIZE = 64
MAX_PAGE_SIZE = 1000
MAX_QUERIES = 256
MAX_EVENTS = 10000
MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 30
KINDS = frozenset({5, 7, 9, 40003})


class HistoryIncomplete(gs.GroupSyncError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('relay history is not complete: ' + reason)


@dataclass(frozen=True)
class ResponsePage:
    events: object
    byte_count: int


def _filter(query, now, allow_unscoped_reactions):
    if (not isinstance(query, dict) or set(query) - {'kinds', '#h', 'authors', 'ids', 'since', 'until'}
            or type(now) is not int or not 0 <= now <= 2**63 - 1):
        raise HistoryIncomplete('unsupported filter')
    kinds, channels = query.get('kinds'), query.get('#h')
    if (not isinstance(kinds, list) or not kinds
            or any(type(k) is not int or k not in KINDS for k in kinds) or len(kinds) != len(set(kinds))
            or not isinstance(channels, list) or len(channels) != 1
            or not isinstance(channels[0], str) or not gs.UUID_RE.fullmatch(channels[0])):
        raise HistoryIncomplete('unsupported scope')
    for name in ('authors', 'ids'):
        if name in query:
            values = query[name]
            if (not isinstance(values, list) or not 1 <= len(values) <= 64
                    or any(not isinstance(v, str) or not gs.HEX64_RE.fullmatch(v) for v in values)
                    or len(values) != len(set(values))):
                raise HistoryIncomplete('unsupported identity filter')
    since, until = query.get('since', 0), query.get('until', now)
    if type(since) is not int or type(until) is not int or not 0 <= since <= until <= now:
        raise HistoryIncomplete('invalid or future interval')
    if (type(allow_unscoped_reactions) is not bool or allow_unscoped_reactions
            and (len(query.get('authors', [])) != 1 or not set(kinds) & {5, 7})):
        raise HistoryIncomplete('unscoped reaction requires exact author')
    return dict(query, since=since, until=until)


def _matches(event, query, allow_unscoped_reactions):
    if (not isinstance(event, dict) or type(event.get('created_at')) is not int
            or not query['since'] <= event['created_at'] <= query['until']
            or event.get('kind') not in query['kinds']
            or not isinstance(event.get('tags'), list)
            or any(not isinstance(t, list) or not t or any(not isinstance(v, str) for v in t)
                   for t in event['tags']) or not gs._nip01_event_verified(event)):
        return False
    if any(event[field] not in query[name] for name, field in (('authors', 'pubkey'), ('ids', 'id')) if name in query):
        return False
    channels = [t for t in event['tags'] if t[0] == 'h']
    if channels == [['h', query['#h'][0]]]:
        return True
    # Stock kind5/7 events may be indexed by their target's channel. Returning
    # one here is not channel authority: the outlet still requires its own exact
    # prior receipt/target via _channel_matches before any effect.
    return allow_unscoped_reactions and event['kind'] in (5, 7) and not channels


def read_history(fetch_page, query, *, now, page_size=PAGE_SIZE, max_page_size=MAX_PAGE_SIZE,
                 max_queries=MAX_QUERIES, max_events=MAX_EVENTS, max_bytes=MAX_BYTES,
                 max_seconds=MAX_SECONDS, allow_unscoped_reactions=False, require_single_response=False, diagnostics=None):
    """Return every verified row or raise; fetch_page(filters, timeout) is read-only.

    No partial result escapes, including on a failed later page. Every call
    re-reads the relay; a prior absence cannot hide a newly completed effect.
    """
    query = _filter(query, now, allow_unscoped_reactions)
    if type(require_single_response) is not bool:
        raise HistoryIncomplete('invalid snapshot requirement')
    for value, ceiling in ((page_size, MAX_PAGE_SIZE), (max_page_size, MAX_PAGE_SIZE),
                           (max_queries, MAX_QUERIES), (max_events, MAX_EVENTS), (max_bytes, MAX_BYTES)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise HistoryIncomplete('invalid budget')
    if max_page_size < page_size or type(max_seconds) not in (int, float) or not 0 < max_seconds <= MAX_SECONDS:
        raise HistoryIncomplete('invalid budget')
    started = time.monotonic()
    metrics = {'queries': 0, 'size_retries': 0, 'response_bytes': 0,
               'events': 0, 'until': query['until'], 'complete': False, 'single_response': False, 'pages': []}
    all_rows = {}

    def page(low, high, limit):
        while True:
            remaining = max_seconds - (time.monotonic() - started)
            if metrics['queries'] >= max_queries or remaining <= 0:
                raise HistoryIncomplete('read budget exhausted')
            current = dict(query, since=low, until=high, limit=limit)
            metrics['queries'] += 1
            measured = {'since': low, 'until': high, 'limit': limit}
            metrics['pages'].append(measured)
            page_started = time.monotonic()
            try:
                response = fetch_page([current], min(gs.DIRECTORY_TIMEOUT, remaining))
            except gs.ResponseTooLarge:
                measured['outcome'] = 'byte_cap'
                if limit == 1:
                    raise HistoryIncomplete('single event exceeds byte cap') from None
                metrics['size_retries'] += 1
                limit = max(1, limit // 2)
                continue
            except Exception as exc:
                measured['outcome'] = type(exc).__name__
                raise
            finally:
                measured['seconds'] = time.monotonic() - page_started
            if time.monotonic() - started >= max_seconds:
                raise HistoryIncomplete('read budget exhausted')
            if (not isinstance(response, ResponsePage) or type(response.byte_count) is not int
                    or not 0 <= response.byte_count <= gs.PEOPLE_API_MAX_BYTES):
                raise HistoryIncomplete('invalid response metadata')
            rows = response.events
            if not isinstance(rows, list) or len(rows) > limit:
                raise HistoryIncomplete('invalid page shape or limit')
            metrics['response_bytes'] += response.byte_count
            measured.update(outcome='response', bytes=response.byte_count, events=len(rows))
            if metrics['response_bytes'] > max_bytes:
                raise HistoryIncomplete('response budget exhausted')
            if (any(not _matches(row, current, allow_unscoped_reactions) for row in rows)
                    or len({row['id'] for row in rows}) != len(rows)):
                raise HistoryIncomplete('unverified or out-of-filter page')
            return rows, limit

    def keep(rows):
        for row in rows:
            all_rows[row['id']] = row
        metrics['events'] = len(all_rows)
        if len(all_rows) > max_events:
            raise HistoryIncomplete('event budget exhausted')

    try:
        low, high = query['since'], query['until']
        while low <= high:
            rows, page_size = page(low, high, page_size)
            if len(rows) < page_size:
                keep(rows)
                metrics['single_response'] = not any(p.get('events', 0) for p in metrics['pages'][:-1])
                break
            if require_single_response:
                raise HistoryIncomplete('single response is not complete')
            boundary = min(row['created_at'] for row in rows)
            observed = {row['id'] for row in rows if row['created_at'] == boundary}
            limit, prior_full = page_size, 0
            while True:
                tied, effective = page(boundary, boundary, limit)
                if len(tied) < effective:
                    if not observed <= {row['id'] for row in tied}:
                        raise HistoryIncomplete('boundary changed during read')
                    break
                if effective <= prior_full or effective >= max_page_size:
                    raise HistoryIncomplete('dense second is not complete')
                prior_full, limit = effective, min(max_page_size, effective * 2)
            keep([row for row in rows if row['created_at'] > boundary])
            keep(tied)
            high = boundary - 1
        metrics['complete'] = True
        return sorted(all_rows.values(), key=lambda row: (row['created_at'], row['id']))
    finally:
        metrics['seconds'] = time.monotonic() - started
        if diagnostics is not None:
            diagnostics.update(metrics)


def signed_history(query, *, relay, key, http, clock, auth_tag=None, **options):
    """Same caller key/profile and transport as its original read, fresh per page."""
    now = int(clock().timestamp())
    url = gs.relay_query_url(relay)

    def fetch(filters, timeout):
        body = json.dumps(filters, separators=(',', ':')).encode()
        headers = {'Authorization': gs.nip98_header(key, 'POST', url, clock(), body=body),
                   'Content-Type': 'application/json', 'Accept': 'application/json'}
        if auth_tag is not None:
            headers['x-auth-tag'] = json.dumps(auth_tag, separators=(',', ':'))
        status, raw = http(url, headers, timeout, body=body)
        if len(raw) > gs.PEOPLE_API_MAX_BYTES:
            raise gs.ResponseTooLarge('relay response exceeds byte cap')
        if status != 200:
            raise HistoryIncomplete('HTTP read failed')
        try:
            return ResponsePage(json.loads(raw), len(raw))
        except (TypeError, ValueError):
            raise HistoryIncomplete('invalid response JSON') from None

    return read_history(fetch, query, now=now, **options)
