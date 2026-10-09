"""Strict native bot scope envelopes; user scopes never grant bot authority.

This stdlib-only boundary validates the whole response atomically. Callers retain
all app/profile, feature permission, signed source, membership and SQL gates.
"""

NOTICE = ('应用的 bot 权限尚未完整核验，保留待处理。怎么解决：检查完整 bot 权限响应、'
          'tenant/user 类型、实际授权状态和分页完整性；不要借用 user 权限。'
          '\n复制给 AI：帮我核查 hostd 原生 bot scope 响应；不要输出凭据、响应正文或个人资料。')
MAX_ROWS = 4096


def _need(value):
    if not value:
        raise ValueError(NOTICE) from None


def _complete(value):
    """An optional pagination marker must explicitly say no rows remain."""
    if 'has_more' in value:
        _need(value['has_more'] is False)
    for name in ('page_token', 'next_page_token', 'next_token'):
        if name in value:
            _need(value[name] is None or type(value[name]) is str and value[name] == '')
    if 'complete' in value:
        _need(value['complete'] is True)
    if 'truncated' in value:
        _need(value['truncated'] is False)
    if 'truncations' in value:
        _need(type(value['truncations']) is list and not value['truncations'])


def parse_bot_scope_envelope(payload):
    """Return only tenant grants after every row and marker passes validation.

    The nonpaged scopes endpoint may omit pagination metadata. If supplied,
    pagination must be complete; missing scope_type is never inferred as tenant.
    """
    _need(type(payload) is dict and payload.get('ok') is True
          and payload.get('identity') == 'bot')
    data = payload.get('data')
    _need(type(data) is dict)
    rows = data.get('scopes')
    _need(type(rows) is list and len(rows) <= MAX_ROWS)
    _complete(payload)
    _complete(data)
    if 'meta' in payload:
        meta = payload['meta']
        _need(type(meta) is dict)
        _complete(meta)
        if 'pagination' in meta:
            pagination = meta['pagination']
            _need(type(pagination) is dict and pagination.get('complete') is True)
            _complete(pagination)
    seen, granted = set(), set()
    for row in rows:
        _need(type(row) is dict)
        name, scope_type, status = row.get('scope_name'), row.get('scope_type'), row.get('grant_status')
        _need(type(name) is str and 0 < len(name) <= 256 and name == name.strip()
              and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in name))
        _need(type(scope_type) is str and scope_type in ('tenant', 'user')
              and type(status) is int and status in (0, 1))
        pair = (name, scope_type)
        _need(pair not in seen)
        seen.add(pair)
        if scope_type == 'tenant' and status == 1:
            granted.add(name)
    return frozenset(granted)
