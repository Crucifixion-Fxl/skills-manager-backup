"""Tests-first acceptance for signed own-agent image delivery.

Uses the current real proof/runtime/reader/bot/HTTP/Store adapters. Synthetic
boundaries are limited to the pinned relay media response and subprocess runner
for binary CLI calls. A
missing image contract fails by explicit assertion before any absent method is
called; no test imports a proposed product module or fabricates a stub.
"""
import asyncio
import base64
import binascii
import copy
import hashlib
import inspect
import json
import os
import stat
import struct
import sys
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path
from urllib.parse import urlsplit
import zlib

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path[:0] = [str(TESTS), str(SCRIPTS)]
import test_hostd_remote_reactions as reactions_fixture
from hostd import store
from hostd.remote_runtime import RemoteRuntime
import buzz_feishu_group_sync as gs

CHANNEL = reactions_fixture.CHANNEL
APP = reactions_fixture.APP
CHAT = reactions_fixture.CHAT
ORIGIN = reactions_fixture.ORIGIN
NOW = reactions_fixture.NOW
KEY = reactions_fixture.signed_fixture.KEY
BODY_CANARY = 'SYNTHETIC_IMAGE_BODY_NEVER_PERSIST'


class MediaReply:
    """Finite synthetic response envelope for one own-reader media GET."""
    __slots__ = ('status', 'content_type', 'content_length', 'body', 'redirected', 'complete')

    def __init__(self, content_type, body, *, status=200, content_length=None,
                 redirected=False, complete=True):
        self.status = status
        self.content_type = content_type
        self.body = body
        self.content_length = len(body) if content_length is None else content_length
        self.redirected = redirected
        self.complete = complete


class ImageWorld(reactions_fixture.World):
    """The reaction suite's protected setup with finite synthetic image IO."""

    def __init__(self, case):
        super().__init__(case)
        self.media = {}
        self.last_images = []
        self.media_reads = []
        self.media_reply_override = None
        self.on_media_read = None
        self.image_posts = []
        self.image_gets = []
        self.image_get_envelopes = []
        self.card_post_count = 0
        self.lose_message_response = False
        self.fail_readback_ordinal = None
        self.lose_image_response = False
        self.image_get_override = None
        self.image_bodies_by_key = {}
        self.block_image_get = False
        self.image_get_entered = threading.Event()
        self.image_get_release = threading.Event()
        self.block_image_create = False
        self.image_create_entered = threading.Event()
        self.image_create_release = threading.Event()
        case.addCleanup(self.image_get_release.set)
        case.addCleanup(self.image_create_release.set)
        self.scopes.add('im:resource:upload')
        self.bot = type(self.bot)(APP, self.cfg, self.data, base_env={'HOME': str(self.root)},
            http_pool=self.pool, chat_id=CHAT, runner=self.bot_media_runner)
        reader = self.reader
        if 'media_http' in inspect.signature(type(reader).__init__).parameters:
            self.reader = type(reader)(self.actual_record, origin=reader.origin,
                relay_pubkey=reader.pin, trusted_relays=reader.trusted_relays,
                clock=reader.clock, http=reader.http, media_http=self.media_http)
            self.media_http_constructor_injected = True
        else:
            # Preserve explicit assertion RED on the baseline without changing
            # the reader instance through a dynamically assigned attribute.
            self.media_http_constructor_injected = False

    def media_http(self, url, headers, timeout, *, body=None):
        parsed = urlsplit(url)
        self.media_reads.append((url, dict(headers), timeout))
        origin = urlsplit(ORIGIN)
        if (parsed.scheme != 'https' or parsed.netloc != origin.netloc
                or parsed.query or parsed.fragment or body is not None):
            raise AssertionError('media read escaped the pinned origin or became a write')
        if parsed.path not in self.media:
            raise AssertionError('unexpected or unpinned media path')
        if self.on_media_read is not None:
            action, self.on_media_read = self.on_media_read, None
            action()
        if self.media_reply_override is not None:
            return (self.media_reply_override(url) if callable(self.media_reply_override)
                    else self.media_reply_override)
        mime, raw = self.media[parsed.path]
        return MediaReply(mime, raw)

    def bot_media_runner(self, argv, *, capture_output, text, timeout, check, env, **kwargs):
        # Lower CLI-process seam only. Keep the real BotLarkCli identity,
        # profile selection, scheduler, and wrapper code in the tested path.
        self.assertTrue(capture_output and text and not check)
        self.assertLessEqual(timeout, 90)
        self.assertIn('--as', argv); self.assertEqual(argv[argv.index('--as') + 1], 'bot')
        self.assertIn('--profile', argv)
        self.assertEqual(argv[argv.index('--profile') + 1], self.bot._profile())
        cli = argv[2:]
        if cli[:3] == ['im', 'images', 'create']:
            data_arg = cli[cli.index('--data') + 1]
            self.assertEqual(json.loads(data_arg), {'image_type': 'message'})
            file_arg = cli[cli.index('--file') + 1]
            self.assertTrue(file_arg.startswith('image='))
            image_path = Path(file_arg.split('=', 1)[1])
            scratch = Path(kwargs['cwd'])
            scratch_stat = scratch.lstat()
            self.assertTrue(stat.S_ISDIR(scratch_stat.st_mode))
            self.assertEqual((scratch_stat.st_uid, stat.S_IMODE(scratch_stat.st_mode)),
                             (os.getuid(), 0o700))
            self.assertNotIn('..', image_path.parts)
            image_path = image_path if image_path.is_absolute() else scratch / image_path
            self.assertEqual(image_path.resolve().parent, scratch.resolve())
            file_stat = image_path.lstat()
            self.assertTrue(stat.S_ISREG(file_stat.st_mode))
            self.assertEqual((file_stat.st_uid, stat.S_IMODE(file_stat.st_mode)),
                             (os.getuid(), 0o600))
            row, in_tx = self.on_loop(lambda: (
                self.db.remote_image_upload_by_source(self.target_id,
                    self.current_event['id'], len(self.image_posts)), self.db.conn.in_transaction))
            self.assertIsNotNone(row)
            self.assertEqual((row.state, row.image_key, in_tx), ('unknown', '', False))
            raw = image_path.read_bytes()
            self.assertEqual(image_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(image_path.stat().st_uid, os.getuid())
            self.image_posts.append(tuple(cli))
            if self.block_image_create:
                self.image_create_entered.set()
                self.assertTrue(self.image_create_release.wait(10))
            if self.lose_image_response:
                raise OSError('SYNTHETIC_LOST_IMAGE_CREATE_RESPONSE')
            key = f'img_own_{len(self.image_posts)}'
            self.image_bodies_by_key[key] = raw
            payload = {'ok': True, 'identity': 'bot', 'data': {'image_key': key}}
        elif cli[:2] == ['api', 'GET'] and '/open-apis/im/v1/images/' in cli[2]:
            key = cli[2].rsplit('/', 1)[-1]
            self.assertEqual(cli[2], f'/open-apis/im/v1/images/{key}')
            self.assertNotIn('--format', cli,
                'binary image GET must bypass only the generic JSON identity contract')
            self.assertIn(key, self.image_bodies_by_key,
                          'GET may target only a key returned by this selected bot upload')
            self.image_gets.append(('GET', key))
            indexed = self.on_loop(lambda: [self.db.remote_image_upload_by_source(
                self.target_id, self.current_event['id'], ordinal) for ordinal in range(4)])
            matching = [row for row in indexed if row is not None and row.image_key == key]
            self.assertEqual(len(matching), 1)
            self.assertIn(matching[0].state, ('unknown', 'acked'))
            self.assertFalse(self.on_loop(lambda: self.db.conn.in_transaction))
            if self.block_image_get:
                self.image_get_entered.set()
                self.assertTrue(self.image_get_release.wait(10))
            if self.image_get_override is not None:
                override = self.image_get_override(key)
                raw = override.get('body', self.image_bodies_by_key[key])
                mime = override.get('content_type', 'image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg')
                saved_path = override.get('saved_path')
                size_bytes = override.get('size_bytes', len(raw))
            else:
                override = {}
                raw = self.image_bodies_by_key[key]
                mime = 'image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg'
                saved_path = None
                size_bytes = len(raw)
            if (self.fail_readback_ordinal is not None
                    and len(self.image_gets) - 1 == self.fail_readback_ordinal):
                raw = b'not-the-uploaded-image'
            output = Path(cli[cli.index('--output') + 1])
            self.assertFalse(output.is_absolute())
            self.assertNotIn('..', output.parts)
            scratch = Path(kwargs['cwd'])
            scratch_stat = scratch.lstat()
            self.assertTrue(stat.S_ISDIR(scratch_stat.st_mode))
            self.assertEqual((scratch_stat.st_uid, stat.S_IMODE(scratch_stat.st_mode)),
                             (os.getuid(), 0o700))
            target = scratch / output
            self.assertEqual(target.parent.resolve(), scratch.resolve())
            self.assertFalse(target.exists() or target.is_symlink())
            if override.get('symlink_output'):
                seed = scratch / 'private-seed.bin'
                seed.write_bytes(raw)
                os.chmod(seed, 0o600)
                target.symlink_to(seed)
            else:
                target.write_bytes(raw)
                os.chmod(target, 0o600)
            output_stat = target.lstat()
            if override.get('symlink_output'):
                self.assertTrue(stat.S_ISLNK(output_stat.st_mode))
            else:
                self.assertTrue(stat.S_ISREG(output_stat.st_mode))
                self.assertEqual((output_stat.st_uid, stat.S_IMODE(output_stat.st_mode)),
                                 (os.getuid(), 0o600))
            payload = {'saved_path': saved_path or str(target.absolute()),
                       'size_bytes': size_bytes, 'content_type': mime}
            self.assertEqual(set(payload), {'saved_path', 'size_bytes', 'content_type'})
            self.image_get_envelopes.append(copy.deepcopy(payload))
        else:
            raise AssertionError('unexpected subprocess command in image adapter test')
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr='')

    def native(self, method, raw_path, body):
        parsed = urlsplit(raw_path)
        if parsed.path == '/open-apis/im/v1/messages' and method == 'POST':
            data = json.loads(body) if body else None
            row, in_tx = self.on_loop(lambda: (
                self.db.remote_delivery_by_source(self.target_id,
                    self.current_event['id'], 'message'), self.db.conn.in_transaction))
            self.assertIsNotNone(row)
            self.assertEqual((row.status, in_tx), ('unknown', False))
            for ordinal in range(len(self.last_images)):
                image_row = self.on_loop(lambda ordinal=ordinal:
                    self.db.remote_image_upload_by_source(self.target_id,
                        self.current_event['id'], ordinal))
                self.assertEqual(image_row.state, 'acked')
            self.api_calls.append((method, parsed.path, {}, copy.deepcopy(data)))
            self.card_post_count += 1
            mid = f'om_image_{self.card_post_count}'
            self.messages[mid] = {'message_id': mid, 'chat_id': CHAT, 'deleted': False,
                'create_time': str(NOW * 1000), 'msg_type': 'interactive',
                'sender': {'sender_type': 'app',
                    'id_type': 'app_id', 'id': APP}, 'root_id': mid,
                'body': {'content': data['content']}}
            response = reactions_fixture.Response(200, {'code': 0, 'data': {'message_id': mid}})
            if self.lose_message_response:
                return OSError('SYNTHETIC_LOST_CARD_RESPONSE')
            return response
        return super().native(method, raw_path, body)

    def signed_image_event(self, images, *, content=BODY_CANARY):
        tags = [['h', CHANNEL]]
        for image in images:
            tags.append(['imeta', 'url ' + image['url'], 'm ' + image['mime'],
                         'x ' + image['sha256'], 'size ' + str(image['size'])])
        event = gs.sign_event(KEY, 9, tags, content, NOW)
        self.events.append(event)
        return event

    def image_bytes(self, kind='png', marker=b'x'):
        if kind == 'png':
            mime, suffix = 'image/png', '.png'
            pixel = bytes((sum(marker) % 256, len(marker) % 256, marker[0] if marker else 0, 255))
            def chunk(name, body):
                return (struct.pack('!I', len(body)) + name + body
                        + struct.pack('!I', binascii.crc32(name + body) & 0xffffffff))
            raw = (b'\x89PNG\r\n\x1a\n'
                + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 6, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(b'\0' + pixel))
                + chunk(b'IEND', b''))
        else:
            mime, suffix = 'image/jpeg', '.jpg'
            base = base64.b64decode(
                '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD6pooooA//2Q==')
            comment = marker or b'fixture'
            segment = b'\xff\xfe' + struct.pack('!H', len(comment) + 2) + comment
            raw = base[:2] + segment + base[2:]
        digest = hashlib.sha256(raw).hexdigest()
        path = f'/media/{digest}{suffix}'
        self.media[path] = (mime, raw)
        return {'url': ORIGIN + path, 'mime': mime, 'sha256': digest,
                'size': len(raw), 'bytes': raw}


class ImageBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = ImageWorld(self)
        self.w.loop = asyncio.get_running_loop()
        self.path = self.w.root / 'metadata' / 'images.db'
        self.db = store.Store(self.path)
        self.addCleanup(lambda: self.db.close())
        self.w.db = self.db
        self.db.register_agent(reactions_fixture.PUB, owner_pubkey=reactions_fixture.OWNER,
                               app_id=APP, config_path=str(self.w.env), now=NOW)
        self.w.target_id = store.Store._remote_digest([reactions_fixture.PUB, CHANNEL])
        self.consumer = self.w.consumer()
        self.root = self.w.own_root()

    def require_image_contract(self):
        reader_parameters = inspect.signature(type(self.w.reader).__init__).parameters
        self.assertIn('media_http', reader_parameters,
            'genuine missing image contract: own-reader media_http must be constructor-only')
        self.assertTrue(self.w.media_http_constructor_injected,
            'genuine missing image contract: media_http seam was not constructor-injected')
        required = (
            (type(self.w.reader), 'read_media'),
            (type(self.w.bot), 'upload_image'),
            (type(self.w.bot), 'own_image'),
            (store.Store, 'remote_image_upload_by_source'),
            (store.Store, 'reserve_remote_image_upload'),
            (store.Store, 'pin_remote_image_key'),
            (store.Store, 'ack_remote_image_upload'),
        )
        missing = [f'{owner.__name__}.{name}' for owner, name in required
                   if not callable(getattr(owner, name, None))]
        self.assertFalse(missing,
            'genuine missing image-delivery feature contract: ' + ', '.join(missing))
        self.assertTrue(callable(getattr(RemoteRuntime, 'deliver', None)),
                        'existing real runtime entrypoint disappeared')

    async def grant(self):
        result = await self.consumer.verify(self.w.target)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.authorization.evidence.capabilities, ('message',),
            'image upload scope must not become an image capability')
        grant = self.db.activate_remote_grant(result.authorization.evidence,
                                               expected_revision=0, now=NOW)
        self.assertTrue(self.db.refresh_remote_proof(grant.target_id,
            result.authorization.evidence, revision=grant.revision,
            scope_hash=grant.scope_hash, now=NOW))
        return grant

    async def deliver(self, event, grant):
        self.w.current_event = event
        runtime = RemoteRuntime(self.db, record=self.w.actual_record, reader=self.w.reader,
            bot=self.w.bot, proofs=self.consumer, link_base=ORIGIN, clock=lambda: self.w.now)
        return await runtime.deliver(self.w.target, event, target_id=grant.target_id,
                                     revision=grant.revision, scope_hash=grant.scope_hash)

    def image_row(self, event, ordinal=0):
        return self.db.remote_image_upload_by_source(self.w.target_id, event['id'], ordinal)

    def no_image_leak(self, value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(BODY_CANARY, raw)
        for image in getattr(self.w, 'last_images', ()):
            self.assertNotIn(image['url'], raw)
            self.assertNotIn(image['bytes'].hex(), raw)
        self.assertNotIn('offline-reaction-token', raw)
        self.assertNotIn('SYNTHETIC_APP_SECRET', raw)
        self.assertNotIn('img_own_', raw)


class RemoteImageDeliveryTests(ImageBase):
    async def test_missing_feature_is_reported_as_explicit_contract_red(self):
        self.require_image_contract()

    async def test_four_images_are_read_back_before_one_card_with_footer_preserved(self):
        self.require_image_contract()
        grant = await self.grant()
        images = [self.w.image_bytes('png', bytes([65 + i])) for i in range(4)]
        self.w.last_images = images
        event = self.w.signed_image_event(images)
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'acked')
        self.assertEqual(len(self.w.image_posts), 4)
        self.assertEqual(self.w.image_gets, [('GET', f'img_own_{i}') for i in range(1, 5)])
        rows = [self.image_row(event, i) for i in range(4)]
        self.assertTrue(all(row.state == 'acked' for row in rows))
        columns = {r[1] for r in self.db.conn.execute('PRAGMA table_info(remote_image_upload)')}
        self.assertTrue({'target_id', 'source_id', 'ordinal', 'revision', 'scope_hash',
                         'source_at', 'content_hash', 'mime_type', 'byte_size', 'state',
                         'image_key'} <= columns)
        self.assertFalse({'url', 'source_url', 'raw_bytes', 'body'} & columns)
        self.assertTrue(all((row.target_id, row.source_id, row.revision, row.scope_hash,
            row.source_at, row.content_hash, row.mime_type, row.byte_size)
            == (grant.target_id, event['id'], grant.revision, grant.scope_hash,
                event['created_at'], images[i]['sha256'], images[i]['mime'], images[i]['size'])
            for i, row in enumerate(rows)))
        self.assertTrue(all(envelope == {'saved_path': envelope['saved_path'],
            'size_bytes': images[i]['size'], 'content_type': images[i]['mime']}
            for i, envelope in enumerate(self.w.image_get_envelopes)))
        message_posts = [call for call in self.w.api_calls
                         if call[0] == 'POST' and call[1] == '/open-apis/im/v1/messages']
        self.assertEqual(len(message_posts), 1)
        card = json.loads(message_posts[0][3]['content'])
        self.assertEqual([e['tag'] for e in card['elements'][-5:-1]], ['img'] * 4)
        self.assertEqual(card['elements'][-1],
                         json.loads(reactions_fixture.message_card(event, self.w.actual_record.name,
                             event['content'], ORIGIN, CHANNEL))['elements'][-1])
        self.assertEqual([e['img_key'] for e in card['elements'][-5:-1]],
                         [f'img_own_{i}' for i in range(1, 5)])
        self.no_image_leak(result.readback())
        self.assertEqual(self.db.bindings(), [])

    async def test_one_image_is_read_back_before_card(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes('jpeg', b'one')
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'acked')
        self.assertEqual(len(self.w.image_posts), 1)
        self.assertEqual(self.w.image_gets, [('GET', 'img_own_1')])
        self.assertEqual(self.image_row(event).state, 'acked')
        post = next(call for call in self.w.api_calls
                    if call[0] == 'POST' and call[1] == '/open-apis/im/v1/messages')
        card = json.loads(post[3]['content'])
        self.assertEqual(card['elements'][-2]['tag'], 'img')
        self.assertEqual(card['elements'][-2]['img_key'], 'img_own_1')
        self.assertEqual(card['elements'][-1],
            json.loads(reactions_fixture.message_card(event, self.w.actual_record.name,
                event['content'], ORIGIN, CHANNEL))['elements'][-1])
        self.assertEqual(grant.evidence.capabilities, ('message',))

    async def test_invalid_metadata_or_media_is_rejected_before_any_upload_or_card(self):
        self.require_image_contract()
        grant = await self.grant()
        valid = self.w.image_bytes()
        malformed = [
            [['imeta', 'url=' + valid['url'], 'm image/png', 'x ' + valid['sha256'],
              'size ' + str(valid['size'])]],  # URL delimiter must be one space.
            [['imeta', 'url ' + valid['url'], 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'] + '?token=x', 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'] + '#fragment', 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url https://user:pass@' + urlsplit(ORIGIN).netloc
              + urlsplit(valid['url']).path, 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url https://relay.example:444' + urlsplit(valid['url']).path,
              'm image/png', 'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url https://foreign.invalid/media/' + valid['sha256'] + '.png',
              'm image/png', 'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'], 'm image/jpeg',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size 10000001']],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size 0']],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + ('f' * 64), 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'].removesuffix('.png') + '.jpg', 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size 0' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size']), 'alt safe text']],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])],
             ['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]],
            [['imeta', 'url ' + valid['url'], 'm image/png',
              'x ' + valid['sha256'], 'size ' + str(valid['size'])]] * 5,
        ]
        for tags in malformed:
            with self.subTest(fields=len(tags)):
                event = gs.sign_event(KEY, 9, [['h', CHANNEL], *tags], BODY_CANARY, NOW)
                self.w.events.append(event)
                result = await self.deliver(event, grant)
                self.assertEqual(result.status, 'pending')
                self.assertFalse(self.w.image_posts)
                self.assertFalse(self.w.media_reads)
                self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                                  c[1] == '/open-apis/im/v1/messages'])
                self.no_image_leak(result.readback())

    async def test_media_status_redirect_mime_size_hash_and_magic_fail_before_upload(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        bad_replies = [
            MediaReply(image['mime'], image['bytes'], status=302, redirected=True),
            MediaReply(image['mime'], image['bytes'], complete=False),
            MediaReply(image['mime'], image['bytes'], content_length=image['size'] + 1),
            MediaReply('image/jpeg', image['bytes']),
            MediaReply(image['mime'], image['bytes'][:-1] + b'X'),
            MediaReply(image['mime'], b'not-a-png' + image['bytes'][9:]),
        ]
        for response in bad_replies:
            with self.subTest(status=response.status, mime=response.content_type,
                              length=response.content_length, complete=response.complete):
                self.w.media_reply_override = response
                event = self.w.signed_image_event([image], content=BODY_CANARY)
                result = await self.deliver(event, grant)
                self.assertEqual(result.status, 'pending')
                self.assertFalse(self.w.image_posts)
                self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                                  c[1] == '/open-apis/im/v1/messages'])
                self.no_image_leak(result.readback())
                self.w.events.remove(event)
        self.assertEqual(len(self.w.media_reads), len(bad_replies),
            'redirect response must not trigger a second media request')
        self.w.media_reply_override = None

    async def test_later_image_invalidity_prevents_any_earlier_upload(self):
        self.require_image_contract()
        grant = await self.grant()
        first = self.w.image_bytes('png', b'first')
        second = self.w.image_bytes('jpeg', b'second')
        def fail_second(url):
            path = urlsplit(url).path
            if path == urlsplit(first['url']).path:
                mime, raw = self.w.media[path]
                return MediaReply(mime, raw)
            return MediaReply(second['mime'], second['bytes'][:-1] + b'X')
        self.w.media_reply_override = fail_second
        images = [first, second]
        self.w.last_images = images
        event = self.w.signed_image_event(images)
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertEqual(len(self.w.media_reads), 2)
        self.assertFalse(self.w.image_posts)
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_unknown_create_without_key_never_reuploads_after_store_reopen(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.lose_image_response = True
        first = await self.deliver(event, grant)
        row = self.image_row(event)
        self.assertEqual((first.status, row.state, row.image_key), ('pending', 'unknown', ''))
        self.assertEqual(len(self.w.image_posts), 1)
        self.db.close(); self.db = store.Store(self.path); self.w.db = self.db
        self.w.lose_image_response = False
        second = await self.deliver(event, grant)
        self.assertEqual(second.status, 'pending')
        self.assertEqual(len(self.w.image_posts), 1)
        self.assertFalse(self.w.image_gets)
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])
        self.no_image_leak(second.readback())

    async def test_cancellation_inside_keyed_get_joins_and_recovers_only_pinned_key(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.block_image_get = True
        task = asyncio.create_task(self.deliver(event, grant))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.image_get_entered.wait, 240),
                'key must be pinned before the exact binary GET starts')
            row = self.image_row(event)
            self.assertEqual((row.state, row.image_key), ('unknown', 'img_own_1'))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done(), 'cancellation must join the in-flight binary GET')
            self.w.image_get_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            self.w.image_get_release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.image_row(event).image_key, 'img_own_1')
        self.db.close(); self.db = store.Store(self.path); self.w.db = self.db
        self.w.block_image_get = False
        second = await self.deliver(event, grant)
        self.assertEqual(second.status, 'acked')
        self.assertEqual(len(self.w.image_posts), 1)
        self.assertEqual(self.w.image_gets, [('GET', 'img_own_1'), ('GET', 'img_own_1')])
        self.no_image_leak(second.readback())

    async def test_cancel_before_upload_result_keeps_keyless_unknown_without_retry(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.block_image_create = True
        task = asyncio.create_task(self.deliver(event, grant))
        try:
            self.assertTrue(await asyncio.to_thread(self.w.image_create_entered.wait, 240))
            row = self.image_row(event)
            self.assertEqual((row.state, row.image_key), ('unknown', ''))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done(), 'cancellation must join the in-flight upload CLI')
            self.w.image_create_release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            self.w.image_create_release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual((self.image_row(event).state, self.image_row(event).image_key),
                         ('unknown', ''))
        self.db.close(); self.db = store.Store(self.path); self.w.db = self.db
        self.w.block_image_create = False
        retry = await self.deliver(event, grant)
        self.assertEqual(retry.status, 'pending')
        self.assertEqual(len(self.w.image_posts), 1)
        self.assertFalse(self.w.image_gets)
        self.assertFalse(self.w.card_post_count)

    async def test_mismatched_binary_get_envelope_or_body_blocks_ack_and_card(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        cases = [
            {'content_type': 'image/jpeg'},
            {'size_bytes': image['size'] + 1},
            {'saved_path': str(self.w.root / 'foreign' / 'file.bin')},
            {'symlink_output': True},
            {'body': image['bytes'][:-1] + b'X'},
            {'body': b'not-an-image' + image['bytes'][12:]},
        ]
        for change in cases:
            with self.subTest(fields=tuple(change)):
                self.w.image_get_override = lambda key, change=change: change
                result = await self.deliver(event, grant)
                row = self.image_row(event)
                self.assertEqual(result.status, 'pending')
                self.assertEqual((row.state, row.image_key), ('unknown', 'img_own_1'))
                self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                                  c[1] == '/open-apis/im/v1/messages'])
                self.no_image_leak(result.readback())
        self.assertEqual(len(self.w.image_posts), 1)

    async def test_second_image_failure_never_sends_partial_card_and_keeps_first_key(self):
        self.require_image_contract()
        grant = await self.grant()
        images = [self.w.image_bytes('png', b'a'), self.w.image_bytes('jpeg', b'b')]
        self.w.last_images = images
        event = self.w.signed_image_event(images)
        self.w.fail_readback_ordinal = 1
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        first, second = self.image_row(event, 0), self.image_row(event, 1)
        self.assertEqual((first.state, first.image_key), ('acked', 'img_own_1'))
        self.assertEqual((second.state, second.image_key), ('unknown', 'img_own_2'))
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_missing_native_image_upload_scope_stops_before_any_image_effect(self):
        self.require_image_contract()
        self.w.scopes.discard('im:resource:upload')
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertEqual(grant.evidence.capabilities, ('message',))
        self.assertFalse(self.w.image_posts)
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])
        self.assertIsNone(self.image_row(event))

    async def test_resource_scope_loss_after_media_read_stops_before_image_intent(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.on_media_read = lambda: self.w.on_loop(
            lambda: self.w.scopes.discard('im:resource:upload'))
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertFalse(self.w.image_posts)
        self.assertIsNone(self.image_row(event))
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_signed_source_disappearance_during_media_read_stops_before_upload(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.on_media_read = lambda: self.w.on_loop(lambda: self.w.events.remove(event))
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertFalse(self.w.image_posts)
        self.assertIsNone(self.image_row(event))
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_signed_source_content_drift_during_media_read_stops_before_upload(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        def change_source():
            changed = copy.deepcopy(event)
            changed['content'] += ' changed after signature'
            self.w.events[self.w.events.index(event)] = changed
        self.w.on_media_read = lambda: self.w.on_loop(change_source)
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertFalse(self.w.image_posts)
        self.assertIsNone(self.image_row(event))
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_current_channel_allowlist_drift_during_media_read_stops_before_upload(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        def remove_channel_allowlist():
            raw = self.w.env.read_bytes()
            current = ('BUZZ_ACP_CHANNELS=' + CHANNEL).encode()
            self.assertIn(current, raw)
            self.w.owned(self.w.env, raw.replace(current, b'BUZZ_ACP_CHANNELS='))
        self.w.on_media_read = lambda: self.w.on_loop(remove_channel_allowlist)
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertFalse(self.w.image_posts)
        self.assertIsNone(self.image_row(event))
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_bot_membership_loss_during_media_read_stops_before_upload(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.on_media_read = lambda: self.w.on_loop(
            lambda: setattr(self.w, 'bot_absent', True))
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'pending')
        self.assertFalse(self.w.image_posts)
        self.assertIsNone(self.image_row(event))
        self.assertFalse([c for c in self.w.api_calls if c[0] == 'POST' and
                          c[1] == '/open-apis/im/v1/messages'])

    async def test_existing_text_message_control_stays_message_only_without_media_scope(self):
        self.w.scopes.discard('im:resource:upload')
        grant = await self.grant()
        self.assertEqual(grant.evidence.capabilities, ('message',))
        event = gs.sign_event(KEY, 9, [['h', CHANNEL]], 'plain text control', NOW)
        self.w.events.append(event)
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'acked')
        self.assertFalse(self.w.image_posts)
        self.assertEqual(self.w.card_post_count, 1)

    async def test_message_unknown_recovery_keeps_original_image_keys_and_never_reuploads(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes()
        self.w.last_images = [image]
        event = self.w.signed_image_event([image])
        self.w.lose_message_response = True
        first = await self.deliver(event, grant)
        self.assertEqual(first.status, 'pending')
        upload_count, card_count = len(self.w.image_posts), self.w.card_post_count
        self.db.close(); self.db = store.Store(self.path); self.w.db = self.db
        self.w.lose_message_response = False
        second = await self.deliver(event, grant)
        self.assertEqual(second.status, 'acked')
        self.assertEqual(len(self.w.image_posts), upload_count)
        self.assertEqual(self.w.card_post_count, card_count)
        self.assertEqual(self.image_row(event).image_key, 'img_own_1')
        self.no_image_leak(second.readback())


class RemoteImageReviewRegressionTests(ImageBase):
    """Only subprocess streams/native IO rendezvous are synthetic."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.envelope_mode = 'stdout'
        self.envelope_seen = []
        self.after_binary_get = None
        self.edit_arrivals = 0
        self.original_runner = self.w.bot_media_runner
        self.w.bot.runner = self.review_runner

    def review_runner(self, argv, **kwargs):
        result = self.original_runner(argv, **kwargs)
        cli = argv[2:]
        if cli[:2] == ['api', 'GET'] and '/open-apis/im/v1/images/' in cli[2]:
            self.envelope_seen.append(json.loads(result.stdout))
            if self.after_binary_get is not None:
                action, self.after_binary_get = self.after_binary_get, None
                self.w.on_loop(action)
            if self.envelope_mode == 'stderr':
                return SimpleNamespace(returncode=0, stdout='', stderr=result.stdout)
            if self.envelope_mode.startswith('duplicate:'):
                key = self.envelope_mode.split(':', 1)[1]
                wrong = {'saved_path': '/SYNTHETIC_WRONG_PATH', 'size_bytes': 0,
                         'content_type': 'application/octet-stream'}[key]
                raw = '{' + json.dumps(key) + ':' + json.dumps(wrong) + ',' + result.stdout[1:]
                return SimpleNamespace(returncode=0, stdout=raw, stderr='')
        return result

    async def image_case(self):
        self.require_image_contract()
        grant = await self.grant()
        image = self.w.image_bytes('png', b'review')
        self.w.last_images = [image]
        return grant, image, self.w.signed_image_event([image])

    def original_tuple(self, row):
        return (row.target_id, row.source_id, row.ordinal, row.revision, row.scope_hash,
                row.source_at, row.content_hash, row.mime_type, row.byte_size, row.image_key)

    async def malformed_envelope(self, mode):
        grant, image, event = await self.image_case()
        self.envelope_mode = mode
        result = await self.deliver(event, grant)
        # Preconditions prove actual _run/native-file/committed-key GET path.
        self.assertEqual(len(self.w.image_posts), 1)
        self.assertEqual(self.w.image_gets, [('GET', 'img_own_1')])
        self.assertEqual(len(self.envelope_seen), 1)
        self.assertEqual(result.status, 'pending',
            'binary receipt must be one exact duplicate-free stdout JSON object')
        row = self.image_row(event)
        self.assertEqual((row.state, row.image_key), ('unknown', 'img_own_1'))
        pinned = self.original_tuple(row)
        self.assertIsNone(self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message'))
        self.assertEqual(self.w.card_post_count, 0)
        again = await self.deliver(event, grant)
        self.assertEqual(again.status, 'pending')
        self.assertEqual(len(self.w.image_posts), 1, 'UNKNOWN cannot upload again')
        self.assertEqual(self.original_tuple(self.image_row(event)), pinned)
        self.assertEqual(self.image_row(event).state, 'unknown')
        self.assertEqual(self.w.card_post_count, 0)
        self.no_image_leak(result.readback())

    async def test_exact_stdout_binary_envelope_control_uses_real_run_and_receipt(self):
        grant, image, event = await self.image_case()
        result = await self.deliver(event, grant)
        self.assertEqual(result.status, 'acked')
        self.assertEqual(len(self.envelope_seen), 1)
        self.assertEqual(set(self.envelope_seen[0]), {'saved_path', 'size_bytes', 'content_type'})
        self.assertEqual((self.envelope_seen[0]['size_bytes'], self.envelope_seen[0]['content_type']),
                         (image['size'], image['mime']))
        self.assertEqual(self.image_row(event).state, 'acked')
        self.assertEqual(self.w.card_post_count, 1)
        self.assertEqual(self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message').status, 'acked')
        self.no_image_leak(result.readback())

    async def test_stderr_only_binary_envelope_is_pending(self):
        await self.malformed_envelope('stderr')

    async def test_duplicate_saved_path_binary_envelope_is_pending(self):
        await self.malformed_envelope('duplicate:saved_path')

    async def test_duplicate_size_bytes_binary_envelope_is_pending(self):
        await self.malformed_envelope('duplicate:size_bytes')

    async def test_duplicate_content_type_binary_envelope_is_pending(self):
        await self.malformed_envelope('duplicate:content_type')

    async def signed_edit_case(self, stage):
        grant, image, event = await self.image_case()
        edit = gs.sign_event(KEY, 40003, [['h', CHANNEL], ['e', event['id']]],
                             'SYNTHETIC_IMAGE_EDIT_UNSUPPORTED', NOW)
        self.assertTrue(gs._nip01_event_verified(edit))
        def arrive():
            self.assertNotIn(edit, self.w.events)
            self.w.events.append(edit)
            self.edit_arrivals += 1
        if stage == 'media':
            self.w.on_media_read = lambda: self.w.on_loop(arrive)
        elif stage == 'binary':
            self.after_binary_get = arrive
        else:
            def native_read(method, path):
                if method == 'GET' and path == '/open-apis/im/v1/messages/om_image_1':
                    self.w.on_native = None
                    self.w.on_loop(arrive)
            self.w.on_native = native_read
        result = await self.deliver(event, grant)
        self.assertEqual(self.edit_arrivals, 1, 'actual selected IO rendezvous must be reached')
        self.assertIn(edit, self.w.events)
        self.assertIn(event, self.w.events, 'original kind9 remains unchanged')
        self.assertEqual(result.status, 'pending',
            'signed image overlay appearing during IO must prevent subsequent ACK/effects')
        row = self.image_row(event)
        message = self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message')
        if stage == 'media':
            self.assertIsNone(row); self.assertIsNone(message)
            self.assertFalse(self.w.image_posts); self.assertEqual(self.w.card_post_count, 0)
        elif stage == 'binary':
            self.assertEqual((row.state, row.image_key), ('unknown', 'img_own_1'))
            self.assertIsNone(message); self.assertEqual(self.w.card_post_count, 0)
        else:
            # Earlier image ACK remains durable; never pretend to roll it back.
            self.assertEqual((row.state, row.image_key), ('acked', 'img_own_1'))
            self.assertEqual(message.status, 'unknown'); self.assertEqual(self.w.card_post_count, 1)
        pinned = self.original_tuple(row) if row is not None else None
        counts = len(self.w.image_posts), self.w.card_post_count
        again = await self.deliver(event, grant)
        self.assertEqual(again.status, 'pending')
        self.assertEqual((len(self.w.image_posts), self.w.card_post_count), counts)
        current = self.image_row(event)
        self.assertEqual(self.original_tuple(current) if current is not None else None, pinned)
        if message is not None:
            self.assertEqual(self.db.remote_delivery_by_source(grant.target_id, event['id'], 'message').status, 'unknown')
        self.assertEqual(grant.evidence.capabilities, ('message',))
        self.no_image_leak(result.readback())

    async def test_signed_image_edit_during_media_prevents_any_upload(self):
        await self.signed_edit_case('media')

    async def test_signed_image_edit_during_binary_get_preserves_original_unknown_key(self):
        await self.signed_edit_case('binary')

    async def test_signed_image_edit_during_native_card_receipt_holds_original_message_unknown(self):
        await self.signed_edit_case('card')


if __name__ == '__main__':
    unittest.main()
