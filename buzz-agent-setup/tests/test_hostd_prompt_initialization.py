"""Approved JOIN can initialize a missing managed block without rewriting prose."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).resolve().parents[1] / 'scripts' / 'hostd')]
import join_effects as effects
import store


class PromptInitialization(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.root.chmod(0o700)
        self.channel = '11111111-1111-4111-8111-111111111111'
        self.owner, self.agent = map(effects.gs._signer_pubkey, ('2' * 64, '3' * 64))
        self.original = b'Existing instructions.\n\nPreserve trailing spaces.  \n\n'
        self.prompt = self.file('prompt', self.original)
        self.responsible = self.file('responsible', b'{"channels":[]}')
        self.env = self.file('agent.env', (f'BUZZ_PRIVATE_KEY={"3" * 64}\nBUZZ_ACP_AGENT_OWNER={self.owner}\nBUZZ_ACP_CHANNELS=\nBUZZ_ACP_SYSTEM_PROMPT_FILE={self.prompt}\nBUZZ_RESPONSIBLE_CONFIG={self.responsible}\n').encode())
        timer = self.file('timer', json.dumps({'version': 1, 'owner_pubkey': self.owner, 'agents': []}).encode())
        config = self.file('config', json.dumps({'channel_id': self.channel, 'chat_id': 'oc_group', 'sync_app_id': 'cli_reader'}).encode())
        self.db = store.Store(self.root / 'sql' / 'hostd.db'); self.addCleanup(self.db.close)
        self.db.reconcile_bindings([store.BindingRecord('alpha', self.channel, 'oc_group', 'cli_reader', str(config), '/reader', '/readerdata')], now=10)
        self.db.register_agent(self.agent, owner_pubkey=self.owner, app_id='cli_agent', now=10)
        self.db.create_join('JOIN-12345678', self.agent, self.owner, 'cli_agent', 'oc_group', kind='channel', binding_id='alpha', now=10)
        generation = self.db.rotate_card('JOIN-12345678', 'om_owner_card', now=11)
        self.assertTrue(self.db.decide_join('JOIN-12345678', 'evt_owner_approve', self.owner, 'cli_agent', 'om_owner_card', generation, approved=True, now=12))
        self.spec = effects.AgentSpec(self.agent, self.owner, 'cli_agent', str(self.env), str(self.prompt), str(self.responsible), 'agent.service', str(timer), str(self.root))
        owner, agent = self.owner, self.agent
        class Relay:
            def owner_of(self, pub): return owner if pub == agent else None
            def members(self, channel): return {owner: 'owner', agent: 'bot'}
            def event(self, kind, tags, content, created_at):
                return effects.gs.sign_event('2' * 64, kind, tags, content, created_at)
            def publish(self, event): raise AssertionError('Membership is already verified')
        relay = Relay(); relay.owner = self.owner
        class Runtime:
            attempts = 0
            def verify(self, *args): return False
            def activate_receipt(self, *args): self.attempts += 1; return None
            activate = activate_receipt
        self.runtime = Runtime()
        client = SimpleNamespace(app_id='cli_agent', member_listing=lambda *a: effects.gs.MemberListing({}, {'cli_agent': 'ou_bot'}, True))
        self.adapter = effects.JoinEffects(self.db, {self.agent: self.spec}, relay, self.runtime, clients={'cli_agent': client}, clock=lambda:100)
        self.row = self.db.join_request('JOIN-12345678')

    def file(self, name, data):
        path = self.root / name; path.write_bytes(data); path.chmod(0o600); return path

    def apply(self): asyncio.run(self.adapter.apply(self.db.join_request('JOIN-12345678')))

    def test_first_approved_channel_initializes_block_and_preserves_all_original_bytes(self):
        self.apply()
        after = self.prompt.read_bytes()
        self.assertTrue(after.startswith(self.original))
        self.assertEqual(after.count(effects.legacy.PROMPT_BEGIN.encode()), 1)
        self.assertEqual(after.count(effects.legacy.PROMPT_END.encode()), 1)
        self.assertIn(self.channel.encode(), after[len(self.original):])
        self.assertEqual(self.db.card_approval_decision('JOIN-12345678').agent_owner_pubkey, self.owner)
        self.assertEqual(next(s for s in self.db.effect_steps('JOIN-12345678') if s['step']=='agent_prompt')['status'], 'verified')
        self.apply(); self.assertEqual(self.prompt.read_bytes(), after)

    def test_no_final_newline_is_preserved_before_new_section(self):
        self.original = b'Exact preexisting prose without newline'
        self.prompt.write_bytes(self.original)
        self.apply(); self.assertTrue(self.prompt.read_bytes().startswith(self.original + b'\n'))

    def test_partial_duplicate_or_reversed_markers_are_not_repaired(self):
        begin, end = effects.legacy.PROMPT_BEGIN, effects.legacy.PROMPT_END
        for text in (begin, end, begin+begin+end, begin+end+end, end+begin):
            with self.subTest(text=text):
                self.prompt.write_text(text)
                before = [p.read_bytes() for p in (self.prompt, self.env, self.responsible)]
                with self.assertRaises(effects.EffectError): self.apply()
                self.assertEqual([p.read_bytes() for p in (self.prompt, self.env, self.responsible)], before)
        self.assertEqual(self.runtime.attempts, 0)

    def test_concurrent_prompt_edit_fails_compare_exchange_and_preserves_editor(self):
        path = self.prompt
        class Concurrent(effects.ProtectedFiles):
            def replace(self, target, expected, value):
                if Path(target) == path: path.write_bytes(b'Concurrent protected edit')
                return super().replace(target, expected, value)
        self.adapter.files = Concurrent()
        with self.assertRaises(effects.EffectError): self.apply()
        self.assertEqual(path.read_bytes(), b'Concurrent protected edit')
        self.assertNotIn(self.channel, self.env.read_text()); self.assertEqual(self.runtime.attempts, 0)

    def test_unapproved_request_does_not_initialize_managed_block(self):
        self.db.conn.execute("UPDATE join_request SET status='requested'")
        with self.assertRaises(effects.EffectError): self.apply()
        self.assertEqual(self.prompt.read_bytes(), self.original)
        self.assertEqual(self.runtime.attempts, 0)

    def test_removed_previously_verified_block_is_not_initialized_again(self):
        self.apply(); self.prompt.write_bytes(b'Changed after verified write')
        count = self.runtime.attempts
        self.apply()
        self.assertEqual(self.prompt.read_bytes(), b'Changed after verified write')
        self.assertEqual(self.runtime.attempts, count)


if __name__ == '__main__': unittest.main()
