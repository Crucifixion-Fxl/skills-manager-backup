"""ADR0027: mappings inside actual signed messages, with no extra relay events."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import urllib.error
from urllib.parse import urlsplit, parse_qsl

try:
    from .report_markdown import card1_report_blocks
    from .bot_round import HostdRound
    from .bot_clients import failure, BotCliError
    from .relay_history import signed_history
except ImportError:
    from report_markdown import card1_report_blocks
    from bot_round import HostdRound
    from bot_clients import failure, BotCliError
    from relay_history import signed_history
import buzz_feishu_group_sync as gs

TAG = 'feishu'
ROOT_TAG = 'feishu-root'
LOOKUP_LIMIT = 150
RELAY_LOOKUP_LIMIT = 1000


def _media_put(url, headers, timeout, *, body):
    """Raw BUD02 PUT; exact approved origin, no proxy or redirect with credentials."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method='PUT')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), gs._NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status, answer = response.status, response.read(gs.PEOPLE_API_MAX_BYTES + 1)
    except urllib.error.HTTPError as response:
        status, answer = response.code, response.read(gs.PEOPLE_API_MAX_BYTES + 1)
        response.close()
    if len(answer) > gs.PEOPLE_API_MAX_BYTES:
        raise failure('附件上传应答过大', '核对 relay 的附件接口，保留原消息再重试')
    return status, answer


def upload_image(path, relay, key, now, http=_media_put, *, auth_tag=''):
    """Buzz desktop-v0.5.23 client.rs + relay-v0.2.1 media.rs BUD02 contract.

    Upload does not publish a channel event. Only the returned exact-byte media
    descriptor becomes imeta on the one message that carries its mapping.
    """
    try:
        data, kind = gs.read_image(Path(path), allowed=frozenset({'jpeg', 'png', 'gif', 'webp'}))
        mime = 'image/' + kind
        digest = hashlib.sha256(data).hexdigest()
        clock = int(now.timestamp())
        auth = gs.sign_event(key, 24242, [['t', 'upload'], ['x', digest], ['expiration', str(clock + 600)],
                                       ['server', urlsplit(relay).netloc]], 'Upload file', clock)
        header = 'Nostr ' + base64.urlsafe_b64encode(json.dumps(auth, separators=(',', ':')).encode()).decode().rstrip('=')
        headers = {'Authorization': header, 'Content-Type': mime, 'X-SHA-256': digest, 'Accept': 'application/json'}
        if auth_tag:
            headers['x-auth-tag'] = auth_tag
        status, answer = http(relay.rstrip('/') + '/upload', headers, 120, body=data)
        descriptor = json.loads(answer)
        if status not in (200, 201) or not isinstance(descriptor, dict):
            raise ValueError
        url = descriptor.get('url')
        parsed, origin = urlsplit(url), urlsplit(relay)
        segment = parsed.path.removeprefix('/media/')
        if (descriptor.get('sha256') != digest or type(descriptor.get('size')) is not int or descriptor['size'] != len(data)
                or descriptor.get('type') != mime or parsed.scheme != origin.scheme or parsed.netloc != origin.netloc
                or parsed.query or parsed.fragment or not parsed.path.startswith('/media/')
                or not gs.MEDIA_SEGMENT_RE.fullmatch(segment) or segment.split('.')[0] != digest):
            raise ValueError
        return ['imeta', f'url {url}', f'm {mime}', f'x {digest}', f'size {len(data)}']
    except Exception:
        raise failure('附件上传没有得到可信的完整回执', '核对 relay 的附件权限与网络，保留原消息并重试；不会发布缺失附件的消息') from None


@dataclass(frozen=True)
class DeliveryMapping:
    event_id: str
    message_id: str
    feishu_root: str
    buzz_root: str | None
    direction: str
    sender_app_id: str = ''


def _one(tags, name):
    rows = [row for row in tags if isinstance(row, list) and row[:1] == [name]]
    return rows[0][1] if len(rows) == 1 and len(rows[0]) == 2 and isinstance(rows[0][1], str) else None


def _verified(event, channel):
    try:
        tags = event.get('tags') if isinstance(event, dict) else None
        if (not isinstance(tags, list) or not all(isinstance(t, list) and t and all(isinstance(v, str) for v in t) for t in tags)
                or any(len([t for t in tags if len(t) >= 4 and t[0] == 'e' and t[3] == marker]) > 1 for marker in ('root', 'reply'))
                or any(t[0] == 'e' and (len(t) < 2 or not gs.HEX64_RE.fullmatch(t[1])) for t in tags)):
            return False
        return (isinstance(event, dict) and type(event.get('kind')) is int and event['kind'] == 9
                and _one(event.get('tags') or [], 'h') == channel and gs._nip01_event_verified(event))
    except Exception:
        return False


def buzz_mapping(event, channel, original_authors, registered_mirrors):
    if not _verified(event, channel) or event['pubkey'] not in set(original_authors) | set(registered_mirrors):
        return None
    mid, root = _one(event['tags'], TAG), _one(event['tags'], ROOT_TAG)
    if not isinstance(mid, str) or not gs.MESSAGE_ID_RE.fullmatch(mid) or not isinstance(root, str) or not gs.MESSAGE_ID_RE.fullmatch(root):
        return None
    parent, buzz_root = gs._buzz_parent(event), gs._buzz_root(event)
    if (root == mid and parent is not None) or (root != mid and (parent is None or buzz_root is None)):
        return None
    return DeliveryMapping(event['id'], mid, root, buzz_root, 'f2b')



_PREVIEW_AUTOLINK = re.compile(r'[<＜](https?://[^\s<>＜]+)>',re.I)
_PREVIEW_URL = re.compile(r"https?://[^\s<>＜`\"']+",re.I)


def _preview_link(text, at, *, literal=False):
    """One complete display link; indices always refer to the original text."""
    if not literal:
        # Inline link labels may contain balanced nested or escaped brackets.
        # Bound nesting to avoid an unclosed-bracket input causing deep scans.
        opening = at + (1 if text.startswith('![',at) else 0)
        if text.startswith('[',opening):
            start = cursor = opening + 1
            balance = 1
            while cursor < len(text) and 0 < balance <= 64:
                if text[cursor] == '\\' and cursor + 1 < len(text):cursor += 2;continue
                if text[cursor] == '[':balance += 1
                elif text[cursor] == ']':balance -= 1
                cursor += 1
            if balance == 0 and text.startswith('(',cursor):
                label = text[start:cursor-1]
                cursor += 1
                balance = 1
                while cursor < len(text) and 0 < balance <= 64:
                    if text[cursor] == '\\' and cursor + 1 < len(text):cursor += 2;continue
                    if text[cursor] == '\n':break
                    if text[cursor] == '(':balance += 1
                    elif text[cursor] == ')':balance -= 1
                    cursor += 1
                if balance == 0:
                    return cursor, len(gs._plain_markdown(label)[0]), None
        match = _PREVIEW_AUTOLINK.match(text,at)
        if match:return match.end(), match.end()-at, match[1]
    match = _PREVIEW_URL.match(text,at)
    if match:
        url = match[0].rstrip('.,;:!?，。；！？')
        for opening, closing in (('(',')'),('[',']'),('{','}')):
            while url.endswith(closing) and url.count(closing) > url.count(opening):url = url[:-1]
        return at + len(url), len(url), url
    return None


def _preview_short_url(url):
    # Labels are display-only; the exact destination remains intact. Userinfo,
    # markdown punctuation and arbitrary path/query text never become a label.
    try:host = urlsplit(url).hostname or ''
    except ValueError:host = ''
    host = re.sub(r'[^A-Za-z0-9.:-]', '', host)[:64]
    return '链接' + ('（' + host + '）' if host else '')


def _preview_cut(text, size):
    """Byte-cap fallback must not leave half a destination or Markdown link."""
    at = 0
    while at < min(size, len(text)):
        token = _preview_link(text, at)
        if token:
            if at < size < token[0]:return at
            at = token[0]
        else:at += 1
    return size


def _preview_plain(text):
    """A destination too large for a card is named, never partially linked."""
    plain = gs._plain_markdown(text)[0]
    at, parts = 0, []
    while at < len(plain):
        token = _preview_link(plain,at,literal=True)
        if token:
            parts.append(_preview_short_url(token[2]));at = token[0]
        else:parts.append(plain[at]);at += 1
    return ''.join(parts)[:120]


def _preview_parts(text, budget=120, *, literal=False, depth=0, atomic_urls=False):
    """Split by visible characters; link destinations and paired markup cost zero.

    Reopen a paired span when the cut crosses it, so both native components are
    independently renderable. Only formatting is repeated, never its content.
    Unknown syntax remains literal, and recursion is bounded for hostile input.
    """
    at, used, parts = 0, 0, []
    masked = gs.CARD_ESCAPE_RE.sub('\0\0', text) if not literal else text
    while at < len(text) and used < budget:
        if atomic_urls:
            token = _preview_link(text, at, literal=literal)
            if token:
                stop, count, url = token
                # The first display item remains visible, even beyond the soft
                # 120-character budget. Otherwise fold before the whole link.
                if count > budget - used and used:
                    return ''.join(parts), text[at:], used
                rendered = text[at:stop]
                if url is not None and count > 512 and len(rendered.encode()) <= gs.MAX_CARD_BYTES // 2 and not literal:
                    short = '[' + _preview_short_url(url) + '](' + url + ')'
                    parsed = _preview_link(short,0)
                    # URL query/path bytes may contain unpaired parentheses.
                    # Keep those URLs raw and whole rather than percent-encode
                    # them or silently generate a prematurely closed href.
                    if '\\' not in url and parsed is not None and parsed[0] == len(short):
                        rendered = short
                        count = len(_preview_short_url(url))
                parts.append(rendered);used += count;at = stop
                continue
        span = None
        if not literal and depth < 8:
            if at == 0 or text[at - 1] == '\n':
                fence = re.match(r'(`{3,}|~{3,})[^\n]*\n', text[at:])
                if fence:
                    closing = re.search(r'(?m)^' + re.escape(fence[1]) + r'[ \t]*(?:\n|$)', text[at + fence.end():])
                    if closing:
                        start = at + fence.end()
                        end = start + closing.start()
                        span = (start, end, start + closing.end(), True)
            if span is None:
                link = gs.CARD_TITLE_LINK_RE.match(masked, at)
                if link:
                    span = (link.start(1), link.end(1), link.end(), False)
            if span is None:
                for pattern in (gs.CARD_TITLE_STRONG_RE, gs.CARD_TITLE_EMPHASIS_RE):
                    match = pattern.match(text, at)
                    if match:
                        span = (match.start(2), match.end(2), match.end(), False)
                        break
            if span is None and text[at] == '`':
                match = re.match(r'(`+)([^\n]*?)\1(?!`)', text[at:])
                if match:
                    span = (at + match.start(2), at + match.end(2), at + match.end(), True)
        if span:
            start, end, stop, code = span
            left, right, count = _preview_parts(text[start:end], budget - used,
                                               literal=code, depth=depth + 1, atomic_urls=atomic_urls)
            opening, closing = text[at:start], text[end:stop]
            if right:
                # A fence closer must start on its own line in each component.
                suffix = ('\n' if code and '\n' in opening and not left.endswith('\n') else '') + closing
                if opening in ('**', '__', '~~', '*', '_'):
                    # Emphasis delimiters touching whitespace stop being markup.
                    tail = left[len(left.rstrip()):]
                    lead = right[:len(right) - len(right.lstrip())]
                    parts.append(opening + left.rstrip() + closing + tail)
                    return ''.join(parts), lead + opening + right.lstrip() + closing + text[stop:], used + count
                parts.append(opening + left + suffix)
                return ''.join(parts), opening + right + closing + text[stop:], used + count
            parts.append(opening + left + closing if atomic_urls else text[at:stop]); used += count; at = stop
        else:
            size = 2 if not literal and text[at] == '\\' and at + 1 < len(text) and text[at + 1] in '\\[]' else 1
            parts.append(text[at:at + size]); at += size; used += 1
    return ''.join(parts), text[at:], used


def _gitlab_card_body(body, *, tag_notifications=True):
    """Display-only producer contract: object title first, event phrase second.

    A parsed header at a message edge is necessary; quoted markers and ordinary
    agent prose are untouched. This never grants routing or sender authority.
    """
    lines = body.split('\n')
    if not (lines[0].startswith(gs.CARD_NOTIFY_HEADER) or lines[-1].startswith(gs.CARD_NOTIFY_HEADER)):
        return body
    from gitlab_buzz_sync import parse_header
    fact = parse_header(body)
    if not fact or fact.get('object') not in ('issue', 'mr', 'tag'):
        return body
    if fact.get('object') == 'tag':
        if not tag_notifications:
            return body
        from gitlab_tag_batches import display_tag
        return display_tag(body, fact)
    # An independently malformed opposite edge is user text, not metadata.
    # The older readable helper checks startswith at both edges, too broadly.
    if parse_header(lines[0]) and len(lines) > 1:
        titled = gs.CARD_LEGACY_TITLE_RE.fullmatch(lines[1])
        if titled:
            lines[1] = titled.group(1)
    readable = '\n'.join(line for index, line in enumerate(lines)
        if not (index in (0, len(lines) - 1) and parse_header(line))
        and not line.startswith(gs.CARD_NOTIFIED_PREFIX)).strip()
    headline, separator, rest = readable.partition('\n')
    masked = gs.CARD_ESCAPE_RE.sub('\0\0', headline)
    marker = '#' if fact['object'] == 'issue' else '!'
    expected = marker + str(fact[fact['object']])
    for link in gs.CARD_TITLE_LINK_RE.finditer(masked):
        label = headline[link.start(1):link.end(1)]
        if label != expected and not label.startswith(expected + ' '):
            continue
        phrase = headline[:link.start()].rstrip(' ·')
        # Require the exact producer event prefix, not an arbitrary prose link.
        if not re.fullmatch(r'[^\n]+ \*\*[^\n]+\*\*', phrase):
            continue
        title = headline[link.start():link.end()]
        details = phrase + headline[link.end():]
        return title + '\n' + details + separator + rest
    return readable


def message_card(event, speaker, text, link_base, channel, *, mentions=(), compact=False):
    """Card1.0 note/lark_md footer survives another bot's raw message read."""
    link = gs.open_link(link_base, event['id'], channel, gs.buzz_thread_root(event))
    if not link or not _verified(event, channel):
        raise failure('消息无法生成可信的 Buzz 对应链接', '检查原消息签名与频道绑定')
    body = gs.card_markdown(text)
    if compact is True:
        body = card1_report_blocks(body)
    modern = compact is True or compact == 'preview_v5'
    tag_preview = False
    if modern or compact == 'preview_v4':
        previous_body = _gitlab_card_body(body, tag_notifications=False)
        body = _gitlab_card_body(body)
        tag_preview = body != previous_body
    elif compact == 'preview_v3':
        body = _gitlab_card_body(body, tag_notifications=False)
    # Prevent a user's embedded navigation label from creating a second receipt.
    body = body.replace(gs.CARD_OPEN_TEXT, 'Buzz 链接')
    name = gs._card_name(speaker) or '?'
    footer = {'tag': 'note', 'elements': [{'tag': 'lark_md', 'content': f'[{gs.CARD_OPEN_TEXT}]({link})'}]}
    full_text = {'tag': 'lark_md', 'content': body}
    elements = [{'tag': 'div', 'text': full_text}]
    remainder = body
    if compact == 'preview_v1' and (len(body) > 240 or len(body.splitlines()) > 6):
        preview = '\n'.join(body.splitlines()[:4])[:120].rstrip() + '…'
        elements = [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': preview}},
                    {'tag': 'collapsible_panel', 'expanded': False,
                     'header': {'title': {'tag': 'plain_text', 'content': '展开全文'}},
                     'elements': elements}]
    elif compact == 'preview_v2' and (len(body) > 240 or len(body.splitlines()) > 6):
        end = min(120, len(''.join(body.splitlines(keepends=True)[:4])))
        # Do not split a Markdown link, emphasis or fenced code block between
        # independently rendered components. No safe prefix means all content
        # remains in the native panel, without manufacturing a duplicate.
        while end and gs._markdown_prefix(body,end)[0] != body[:end].rstrip():
            end -= 1
        preview, remainder = body[:end], body[end:]
        full_text['content'] = remainder
        panel = {'tag': 'collapsible_panel', 'expanded': False,
                 'header': {'title': {'tag': 'plain_text', 'content': '展开剩余内容'}},
                 'elements': elements}
        elements = ([{'tag': 'div', 'text': {'tag': 'lark_md', 'content': preview}}] if preview else []) + [panel]
    elif compact and (len(body) > 240 or len(body.splitlines()) > 6 or tag_preview and len(body) > 120):
        preview, remainder, _ = _preview_parts(body, atomic_urls=modern)
        if remainder:
            preview_tag = 'lark_md'
            if len(preview.encode()) > gs.MAX_CARD_BYTES // 2:
                # A short label can carry an enormous URL. Keep the visible
                # preview as literal text and the canonical Buzz source link;
                # mark omitted markup instead of making the whole card fail.
                plain_source = re.sub(r'(?m)^(?:`{3,}|~{3,})[^\n]*(?:\n|$)', '', preview)
                preview = _preview_plain(plain_source) if modern else gs._plain_markdown(plain_source)[0][:120]
                preview_tag = 'plain_text'
                remainder += '\n' + gs.CARD_TRUNCATED_NOTE
            full_text['content'] = remainder
            elements = [{'tag': 'div', 'text': {'tag': preview_tag, 'content': preview}},
                        {'tag': 'collapsible_panel', 'expanded': False,
                         'header': {'title': {'tag': 'plain_text', 'content': '展开剩余内容'}},
                         'elements': elements}]
        elif modern:
            # A single long link can consume the whole body; do not repeat it
            # in an otherwise empty panel after the display-only substitution.
            full_text['content'] = preview
            remainder = preview
    doc = {'config': {'wide_screen_mode': True, 'update_multi': True},
           'header': {'title': {'tag': 'plain_text', 'content': name}},
           'elements': [*elements, footer]}
    if modern or compact in ('preview_v2', 'preview_v3', 'preview_v4'):
        del doc['header']  # The native message already identifies the bot.
    if mentions:
        doc['elements'].insert(1, {'tag': 'div', 'text': {'tag': 'lark_md',
            'content': ' '.join(gs._mention_token(m) for m in mentions[:gs.CARD_MENTIONS_MAX])}})
    raw = json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
    if len(raw.encode()) >= gs.MAX_CARD_BYTES:
        lower, upper = 0, len(remainder)
        while lower < upper:
            size = (lower + upper + 1) // 2
            full_text['content'] = remainder[:size] + '\n' + gs.CARD_TRUNCATED_NOTE
            candidate = json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
            if len(candidate.encode()) < gs.MAX_CARD_BYTES:
                lower = size
            else:
                upper = size - 1
        if modern:lower = _preview_cut(remainder, lower)
        full_text['content'] = remainder[:lower] + '\n' + gs.CARD_TRUNCATED_NOTE
        raw = json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
        if len(raw.encode()) >= gs.MAX_CARD_BYTES:
            raise failure('消息卡片超出飞书大小限制', '检查对应链接与提及对象；保留原消息，不要改成缺失对应关系的文本')
    return raw


def card_document(raw):
    """Compare native Card 1.0 payloads while retaining all delivery content.

    Feishu user_card_content sorts JSON, omits display-only config switches and
    adds an empty header-title i18n map. Only these observed projections are
    normalized; links, body, sender and thread still require exact proof.
    """
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate card field')
            result[key] = value
        return result
    doc = json.loads(raw, object_pairs_hook=unique) if isinstance(raw, str) else copy.deepcopy(raw)
    if not isinstance(doc, dict):
        raise ValueError('invalid card document')
    config = doc.get('config')
    if isinstance(config, dict):
        for key in ('wide_screen_mode', 'update_multi'):
            if key in config:
                if type(config[key]) is not bool:
                    raise ValueError('invalid card display flag')
                # The native projection omits true defaults. An explicitly
                # false value is different and must remain part of the proof.
                if config[key] is True:
                    del config[key]
    title = (doc.get('header') or {}).get('title')
    if isinstance(title, dict) and title.get('i18n') == {}:
        del title['i18n']
    return doc


def _receipt_link(message):
    raw = (message.get('body') or {}).get('content') if isinstance(message.get('body'), dict) else message.get('content')
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(doc, dict):
            return None
        if message.get('msg_type') == 'interactive':
            if doc.get('schema') == '2.0' or not isinstance(doc.get('elements'), list) or not doc['elements']:
                return None
            footer = doc['elements'][-1]
            if isinstance(footer, list):
                if len(footer) != 1:
                    return None
                footer = footer[0]
            if footer.get('tag') != 'note' or len(footer.get('elements') or []) != 1:
                return None
            element = footer['elements'][0]
            if element.get('tag') == 'a' and element.get('text') == gs.CARD_OPEN_TEXT:
                return element.get('href')
            if element.get('tag') != 'lark_md':
                return None
            match = re.fullmatch(r'\[在 Buzz 中打开\]\((https://[^\s()]+)\)', element.get('content') or '')
            return match.group(1) if match else None
        if message.get('msg_type') == 'post':
            locale = doc.get('zh_cn') or doc.get('en_us') or doc
            rows = locale.get('content')
            if not isinstance(rows, list) or not rows or len(rows[-1]) != 1:
                return None
            element = rows[-1][0]
            return element.get('href') if element.get('tag') == 'a' and element.get('text') == gs.CARD_OPEN_TEXT else None
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
    return None


def feishu_mapping(message, event, channel, chat, link_base, human_authors, agent_apps, sync_app,
                   *, root_mapping=None, context_authors=frozenset()):
    if not _verified(event, channel) or message.get('chat_id') != chat or message.get('deleted'):
        return None
    mid = message.get('message_id')
    if not isinstance(mid, str) or not gs.MESSAGE_ID_RE.fullmatch(mid):
        return None
    # Context is a separate display/source policy, never a human or bot grant.
    expected_app = sync_app if event['pubkey'] in human_authors or event['pubkey'] in context_authors else agent_apps.get(event['pubkey'])
    sender = message.get('sender')
    if (not expected_app or not isinstance(sender, dict) or sender.get('sender_type') != 'app'
            or sender.get('id_type') != 'app_id' or sender.get('id') != expected_app):
        return None
    link = _receipt_link(message)
    if not isinstance(link, str):
        return None
    try:
        parts, origin = urlsplit(link), urlsplit(link_base)
        pairs = parse_qsl(parts.query, strict_parsing=True)
        query = dict(pairs)
        if (parts.scheme != 'https' or parts.netloc != origin.netloc or origin.scheme != 'https'
                or parts.path != gs.CARD_OPEN_PATH or parts.fragment or parts.username is not None
                or len(query) != len(pairs) or set(query) not in ({'e', 'c'}, {'e', 'c', 't'})
                or query.get('e') != event['id'] or query.get('c') != channel):
            return None
        buzz_root = gs.buzz_thread_root(event)
        actual_root = message.get('root_id') or mid
        if query.get('t') != buzz_root:
            return None
        # Stock Buzz emits a direct reply with only an e/reply marker. It
        # still names a verifiable parent even though bridge link t is absent.
        reply_parent = gs._buzz_parent(event)
        if buzz_root is None and reply_parent is not None:
            buzz_root = reply_parent
        if actual_root != mid:
            if (root_mapping is None or root_mapping.message_id != actual_root
                    or root_mapping.event_id != buzz_root):
                return None
        elif buzz_root is not None:
            return None
        return DeliveryMapping(event['id'], mid, actual_root, buzz_root, 'b2f', expected_app)
    except (ValueError, TypeError, AttributeError):
        return None


class MappedHostdRound(HostdRound):
    """Opt-in hostd path. Legacy Round never emits or consumes these mappings."""
    def __init__(self, *args, store, binding_id, media_http=_media_put, **kwargs):
        super().__init__(*args, **kwargs)
        self.mapping_store, self.binding_id = store, binding_id
        self.media_http = media_http
        self.cfg['message_format'] = 'card'
        self._resolving = set()
        self._history = {}

    def _adopt(self, mapping):
        s = self.state
        for field, key, target in ((s.f2b, mapping.message_id, mapping.event_id), (s.b2f, mapping.event_id, mapping.message_id)):
            old = field.get(key)
            if old and gs._settled(old) and old != target:
                raise failure('公开对应关系与本机账本冲突', '核对原消息与绑定，不要覆盖已有投递')
        s.f2b[mapping.message_id] = mapping.event_id
        s.b2f[mapping.event_id] = mapping.message_id
        s.f_unresolved.pop(mapping.message_id, None)
        s.unresolved.pop(mapping.event_id, None)
        if mapping.sender_app_id:
            s.b2f_senders[mapping.event_id] = mapping.sender_app_id
            s.b2f_modes[mapping.event_id] = gs.SEND_CARD
        s.threads[mapping.feishu_root] = self.now_ts
        s.polled.setdefault(mapping.feishu_root, s.floor)
        self._watch(mapping.message_id, mapping.event_id)
        self.persist()
        return mapping

    def _event_by_id(self, event_id):
        found = [ev for ev in self._relay_read([{'kinds': [9], '#h': [self.cfg['channel_id']], 'ids': [event_id], 'limit': 2}])
                 if _verified(ev, self.cfg['channel_id']) and ev['id'] == event_id]
        if len(found) != 1:
            return None
        return found[0]

    def _directory_apps(self):
        # directory comes only from signed latest kind30177 policies; configured
        # apps are also looked up, never treated as published merely by config.
        agents = self.agents_in_channel()
        answer = gs.fetch_agent_directory(self.cfg, self.clients.relay_url, agents, set(), self.clients.http,
                                         self.auth_clock() if self.auth_clock else self.now)
        return dict(answer.apps)

    def resolve_feishu(self, message_id, *, observed_message=None):
        if message_id in self._resolving or len(self._resolving) >= 8:
            raise failure('话题对应关系出现循环或过深', '核对实际根消息与回复关系')
        self._resolving.add(message_id)
        try:
            # Only a directly read, same-round/same-reader open_id snapshot
            # may be supplied by event recovery. No negative lookup is cached:
            # UNKNOWN delivery recovery and later rounds still read the relay.
            message = observed_message if observed_message is not None else self.clients.owner.message_view(message_id, 'open_id')
            if message is not None and (not isinstance(message, dict) or message.get('message_id') != message_id):
                raise failure('对应关系的实际消息目标不一致', '重新读取原消息与话题关系')
            if message is None or message.get('chat_id') != self.cfg['chat_id']:
                return None
            root_id = message.get('root_id') or message_id
            root_mapping = self.resolve_feishu(root_id) if root_id != message_id else None
            link = _receipt_link(message)
            if link:
                query = dict(parse_qsl(urlsplit(link).query))
                eid = query.get('e')
                event = self._event_by_id(eid) if isinstance(eid, str) and gs.HEX64_RE.fullmatch(eid) else None
                mapping = feishu_mapping(message, event, self.cfg['channel_id'], self.cfg['chat_id'],
                        self.cfg['people_api']['base_url'], self.humans(), self._directory_apps(), self.desk_app_id,
                        root_mapping=root_mapping,
                        context_authors={event['pubkey']} if self._context_buzz_source(event) else frozenset())
                return self._adopt(mapping) if mapping else None
            # NIP-01 relays need not index multi-letter tags such as feishu.
            # The settled ledger is only an exact-ID query hint: the result
            # still passes all signed content and actual sender checks below.
            hint = self.state.f2b.get(message_id)
            if hint is not None and not isinstance(hint, str):
                return None
            if hint and gs._settled(hint):
                if not isinstance(hint, str) or not gs.HEX64_RE.fullmatch(hint):
                    return None
                event = self._event_by_id(hint)
                events = [event] if event else []
            else:
                # Restrict the supported authors index to signers that the
                # existing actual-sender checks below could accept. A history
                # scan across unrelated agent replies is neither necessary nor
                # an atomic absence proof.
                if self.agent_lookup_failed:
                    raise failure('镜像目录未完整验证', '重新读取已签名的镜像目录，保留原投递状态')
                sender = message.get('sender') or {}
                if sender.get('sender_type') == 'user':
                    projected = self.clients.owner.message_view(message_id, 'union_id')
                    projected_sender = (projected or {}).get('sender') or {}
                    if ((projected or {}).get('chat_id') != self.cfg['chat_id']
                            or projected_sender.get('sender_type') != 'user'
                            or projected_sender.get('id_type') != 'union_id'):
                        return None
                    author = self.id_to_pubkey.get(projected_sender.get('id'))
                    authors = self.other_mirrors | {self.cfg['mirror_pubkey']}
                    if author:
                        authors = authors | {author}
                elif sender.get('sender_type') == 'app' and sender.get('id_type') == 'app_id':
                    authors = {pk for pk, app in self._directory_apps().items() if app == sender.get('id')}
                else:
                    return None
                if not authors:
                    return None
                # Keep the same owner-signed read identity as _relay_read,
                # but validate every raw page instead of dropping bad rows.
                events = signed_history({'kinds': [9], '#h': [self.cfg['channel_id']], 'authors': sorted(authors)},
                    relay=self.clients.relay_url,
                    key=gs.load_signer_key(Path(self.cfg['people_api']['signer_env_file'])),
                    http=self.clients.http, clock=self.auth_clock or (lambda: self.now), require_single_response=True, page_size=RELAY_LOOKUP_LIMIT)
            candidates = [(m, ev) for ev in events if (m := buzz_mapping(ev, self.cfg['channel_id'], self.humans() | self.agents_in_channel(),
                                                                  self.other_mirrors | {self.cfg['mirror_pubkey']})) is not None
                          and m.message_id == message_id and m.feishu_root == root_id]
            if len(candidates) != 1:
                return None
            mapping, source_event = candidates[0]
            if root_id != message_id and (root_mapping is None or mapping.buzz_root != root_mapping.event_id):
                return None
            sender = message.get('sender') or {}
            signer = source_event['pubkey']
            mirror = signer in self.other_mirrors | {self.cfg['mirror_pubkey']}
            author_tags = [t for t in source_event['tags'] if t[:1] == [gs.FEISHU_AUTHOR_TAG]]
            if len(author_tags) > 1:
                return None
            claimed_author = _one(source_event['tags'], gs.FEISHU_AUTHOR_TAG)
            if author_tags and (claimed_author is None or not gs.HEX64_RE.fullmatch(claimed_author)):
                return None
            if sender.get('sender_type') == 'user':
                # Recheck after the relay read: discovery's earlier projection
                # must not hide an intervening native-author change.
                projected = self.clients.owner.message_view(message_id, 'union_id')
                projected_sender = (projected or {}).get('sender') or {}
                if ((projected or {}).get('chat_id') != self.cfg['chat_id']
                        or projected_sender.get('sender_type') != 'user' or projected_sender.get('id_type') != 'union_id'):
                    return None
                author = self.id_to_pubkey.get(projected_sender.get('id'))
                if (not mirror and author != signer) or (claimed_author and claimed_author != author):
                    return None
            elif not mirror:
                expected = self._directory_apps().get(signer)
                if not expected or sender.get('sender_type') != 'app' or sender.get('id_type') != 'app_id' or sender.get('id') != expected:
                    return None
            else:
                return None
            return self._adopt(mapping)
        finally:
            self._resolving.remove(message_id)

    def resolve_buzz(self, event):
        embedded = buzz_mapping(event, self.cfg['channel_id'], set(self.roles), self.other_mirrors | {self.cfg['mirror_pubkey']})
        if embedded:
            return self.resolve_feishu(embedded.message_id)
        if not _verified(event, self.cfg['channel_id']):
            return None
        since = max(0, event['created_at'] - 900)
        if since not in self._history:
            rows, more = self.clients.owner.messages(self.cfg['chat_id'], datetime.fromtimestamp(since, timezone.utc),
                                                    order='desc', page_limit=LOOKUP_LIMIT)
            if more:
                raise failure('历史消息未读完整，暂不能确认旧投递', '缩小目标或直接读取实际消息，不能猜测对应关系后重发')
            self._history[since] = rows
        found = []
        for row in self._history[since]:
            mid = row.get('message_id')
            if not isinstance(mid, str):
                continue
            raw = self.clients.owner.message_view(mid, 'open_id')
            link = _receipt_link(raw or {})
            if link and dict(parse_qsl(urlsplit(link).query)).get('e') == event['id']:
                # Exact same-reader open_id GET; only local footer parsing
                # intervened. Reuse this call's snapshot, never the list row
                # or a prior attempt's result (including UNKNOWN recovery).
                mapping = self.resolve_feishu(mid, observed_message=raw)
                if mapping:
                    found.append(mapping)
        if len(found) > 1:
            raise failure('同一 Buzz 消息存在冲突的投递对应', '核对重复消息与原始发送者，保留账本')
        return found[0] if found else None

    def _recover_buzz_mapping(self, event):
        if gs.feishu_id_for_buzz(self.state, event.get('id')) is None:
            self.resolve_buzz(event)

    def _recover_buzz_target(self, event_id):
        if gs.HEX64_RE.fullmatch(event_id) and gs.feishu_id_for_buzz(self.state, event_id) is None:
            original = self._event_by_id(event_id)
            if original:
                self.resolve_buzz(original)

    def _recover_feishu_mapping(self, msg):
        mid = msg['message_id']
        if gs.buzz_id_for_feishu(self.state, mid):
            return False
        return self.resolve_feishu(mid) is not None

    def _feishu_pending_retry(self, message_id):
        return True

    def _feishu_source_root(self, msg):
        # Chat history also contains inline replies. Selecting their native
        # root still goes through the existing actual mapping proof below;
        # treating them as top level fails the immutable root-tag check.
        root = msg.get('root_id')
        if root in (None, '', msg.get('message_id')):
            return None
        if not isinstance(root, str) or not root.startswith('om_') or any(c.isspace() for c in root):
            raise failure('回复的真实根消息标识无法验证', '保留原消息并核对实际话题，不能改发到顶层')
        return root

    def _feishu_reply_mapping(self, root):
        if root is None:
            return None
        known = gs.buzz_id_for_feishu(self.state, root)
        if not known:
            mapping = self.resolve_feishu(root)
            known = mapping.event_id if mapping else None
        if not known:
            raise failure('原话题根消息的对应关系尚未确认', '先恢复根消息的实际投递；回复将等待，不会发到顶层')
        return known

    def _feishu_tags(self, msg, root, inbound, reply_to, files):
        mid = inbound.message_id
        actual_root = msg.get('root_id') or root or mid
        if actual_root != (root or mid):
            raise failure('回复的真实根消息与读取话题不一致', '核对话题 API 的实际回复关系')
        tags = [[TAG, mid], [ROOT_TAG, actual_root]]
        if inbound.sender_pubkey:
            tags.append([gs.FEISHU_AUTHOR_TAG, inbound.sender_pubkey])
        if reply_to:
            tags.append(['e', reply_to, '', 'root'])
        tags.extend(upload_image(path, self.clients.relay_url, self.clients.mirror_key,
                                 self.auth_clock() if self.auth_clock else self.now, self.media_http,
                                 auth_tag=self.clients.mirror_auth_tag) for path in files)
        return tags

    def _counts_approval(self):
        return False

    def _delivery_settled(self, direction, source, target):
        self.persist()

    def _mirror_publish(self, kind, tags, content, created_at=None):
        mid = _one(tags, TAG)
        if kind != 9 or mid is None:
            return super()._mirror_publish(kind, tags, content, created_at)
        key = gs.secret_hex(self.clients.mirror_key, 'mirror env file')
        auth = json.loads(self.clients.mirror_auth_tag) if self.clients.mirror_auth_tag else None
        if isinstance(auth, list) and len(auth) >= 2 and auth[0] == 'auth' and all(isinstance(v, str) for v in auth):
            tags = [*tags, auth]
        pubkey = gs._signer_pubkey(key)
        serial = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(',', ':'), ensure_ascii=False)
        event_id = hashlib.sha256(serial.encode()).hexdigest()
        signature = gs.sync.nk.schnorr_sign(bytes.fromhex(event_id), bytes.fromhex(key), bytes(32)).hex()
        event = dict(id=event_id, pubkey=pubkey, created_at=created_at, kind=kind, tags=tags, content=content, sig=signature)
        delivery = self.mapping_store.reserve_delivery(self.binding_id, mid, 'f2b', now=self.now_ts)
        sealed = self.mapping_store.delivery_record(delivery.id)
        if sealed['content_hash'] and sealed['content_hash'] != event_id:
            raise gs.MirrorContentConflict('消息重试内容与原投递不一致，已保留原记录等待核实；复制给 AI：核查原消息投递结果，不要清除原内容校验或重发未知结果。')
        self.mapping_store.seal_delivery_content(delivery.id, event_id, now=self.now_ts)
        trace = getattr(self, 'latency_trace', None)
        if trace is not None: trace.record(self.binding_id, 'mirror_publish_started', source=mid, target=event_id)
        result = gs.publish_signed_event(self.clients.relay_url, key, event, self.clients.http,
                                       self.auth_clock() if self.auth_clock else self.now,
                                       auth_tag=self.clients.mirror_auth_tag or None)
        if trace is not None: trace.record(self.binding_id, 'mirror_publish_finished', source=mid, target=event_id)
        return result

    def _prepare_outbound(self, event, out, *, content=None):
        if out.relayed:
            raise failure('这条 agent 消息正在等待它自己的发送器', '为这个有 bot 的 agent 接通本机发送器；不要用同步 bot 代发。没有 bot 的 agent 路线仍待决定')
        is_context = self._context_buzz_source(event)
        if (getattr(self, 'own_outlets_active', False)
                and event.get('pubkey') not in self.humans() and not is_context):
            raise failure('上下文来源的投递策略已变化', '重新核对当前频道、来源与上下文配置；保留原消息')
        apps = self._agent_apps()
        context = self._card_context(apps)
        if is_context:
            # Keep the existing context label, forged-markup scrubbing and
            # agent-only mentions. Generic mapping cards would otherwise
            # render this source like a human and resurrect human mentions.
            targets = {pk: (self.bot_members[app], self.names.get(pk) or pk[:12])
                       for pk, app in apps.items() if app in self.bot_members
                       and self.bot_admission_allowed(pk, app)}
            shown = event if content is None else dict(event, content=content)
            routed = gs.route_buzz_event(shown, mirror_pubkey=self.cfg['mirror_pubkey'],
                agent_apps=apps, human_pubkeys=self.humans(), agent_pubkeys=self.agents_in_channel(),
                names=self.names, mention_targets={}, unmapped_senders='context',
                agent_mention_targets={}, managed_agents=set(self.cfg['agents']),
                other_mirrors=self.other_mirrors)
            if not isinstance(routed, gs.Outbound):
                raise failure('上下文来源的投递策略无法确认', '核对当前频道、来源与上下文配置；保留原消息')
            mentioned = {t[1] for t in event['tags'] if t[:1] == ['p'] and len(t) >= 2}
            mentions = tuple(gs.CardMention(name, None, member)
                             for pk, (member, name) in targets.items() if pk in mentioned)
            text = routed.text
            if any(t[:2] == ['buzz:workflow', 'true'] for t in event['tags']):
                text = gs.BUZZ_CONTEXT_LABEL + ' · ' + text
            return replace(out, text=text, card=message_card(event, gs.BUZZ_CONTEXT_LABEL, text,
                self.cfg['people_api']['base_url'], self.cfg['channel_id'],
                mentions=mentions, compact=True))
        open_ids = self._card_open_ids(apps) if (out.via_app_id or self.desk_app_id) == self.desk_app_id else {}
        mentions, seen = [], {event['pubkey']}
        for tag in event['tags']:
            if tag[:1] != ['p'] or len(tag) < 2 or tag[1] in seen or not gs.HEX64_RE.fullmatch(tag[1]):
                continue
            target = tag[1]
            seen.add(target)
            mentions.append(gs.CardMention(self.names.get(target) or target[:12], context.emails.get(target),
                                           open_ids.get(target)))
        return replace(out, card=message_card(event, self.names.get(event['pubkey']) or event['pubkey'][:12],
                                             event['content'] if content is None else content,
                                             self.cfg['people_api']['base_url'], self.cfg['channel_id'], mentions=tuple(mentions)))

    def _buzz_edit(self, event, *args, **kwargs):
        if (event.get('kind') != gs.EDIT_KIND or _one(event.get('tags') or [], 'h') != self.cfg['channel_id']
                or not gs._nip01_event_verified(event)):
            raise failure('编辑事件无法验证', '核对原作者签名与频道，保留原消息的对应链接')
        target = gs.edit_target(event)
        if target:
            self._recover_buzz_target(target)
        return super()._buzz_edit(event, *args, **kwargs)

    def _post(self, client, chat_id, parent, body, key, *, card):
        if not card:
            raise BotCliError('mapping send', -1, 'mapping_required', definite=True, error_type='permission')
        try:
            return super()._post(client, chat_id, parent, body, key, card=True)
        except gs.CliError as exc:
            if gs.card_refused(exc):
                raise BotCliError('mapping send', exc.code, 'mapping_required', definite=True, error_type='permission') from None
            raise

    def _thread_root_parent(self, event, parent_id, queue, by_id):
        original = self._event_by_id(gs._buzz_root(event) or parent_id)
        if original:
            mapping = self.resolve_buzz(original)
            if mapping:
                return mapping.message_id
        # No send has been attempted. Preserve the source's real discovery
        # boundary before an independent topic can advance the phase cursor.
        # Incomplete reads and conflicting proofs above still fail closed.
        self.state.unresolved[event['id']] = event['created_at']
        self.persist()
        self.report['thread_roots_deferred'] += 1
        return gs._HELD

    def _feishu_waiting_context(self):
        return {r['message_id']: r['source_ms'] // 1000 for r in self.mapping_store.conn.execute(
            'SELECT message_id,source_ms FROM feishu_ingest WHERE binding_id=? AND done=0 AND waiting_context=1',
            (self.binding_id,))}

    def _feishu_defer_context(self, message_id):
        with self.mapping_store.transaction():
            self.mapping_store.conn.execute(
                'UPDATE feishu_ingest SET waiting_context=1 WHERE binding_id=? AND message_id=? AND done=0',
                (self.binding_id, message_id))

    def _feishu_root_rows(self, start, window_start, floor):
        try:
            from . import feishu_ingest
        except ImportError:
            import feishu_ingest
        return feishu_ingest.discover(self, start), window_start, floor

    def _feishu_process_root(self, mirror, msg, window_start):
        try:
            from . import feishu_ingest
        except ImportError:
            import feishu_ingest
        feishu_ingest.process(self, mirror, msg, window_start)

    def _feishu_finish_roots(self):
        try:
            from . import feishu_ingest
        except ImportError:
            import feishu_ingest
        feishu_ingest.finish(self)

    def _give_up_stale(self, ledger, unresolved):
        if ledger is not self.state.b2f:
            return super()._give_up_stale(ledger, unresolved)
        # An unattempted dependency hold has no idempotency window to expire.
        # Keep its original timestamp so later reads can still find the source;
        # actual PENDING/RETRY/terminal entries retain the legacy close rules.
        for item, created in list(unresolved.items()):
            if item in ledger and self.now_ts - created > gs.THREAD_DISCOVERY_SECONDS:
                self._close(ledger, unresolved, item)

    def recover_feishu_event(self, event, *, observed_message=None):
        root = super().recover_feishu_event(event, observed_message=observed_message)
        if root:
            self.resolve_feishu(root['message_id'], observed_message=root)
            target = event.get('message_id')
            if target and target != root['message_id']:
                self.resolve_feishu(target, observed_message=observed_message)
        return root
