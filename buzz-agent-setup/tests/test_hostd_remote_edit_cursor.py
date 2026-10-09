"""Finite EDIT cursor/UNKNOWN/window acceptance through real protected adapters."""
import sys
from pathlib import Path
from unittest import mock
TESTS = Path(__file__).resolve().parent
sys.path[:0] = [str(TESTS), str(TESTS.parent / 'scripts')]
import test_hostd_remote_actions as actions
reactions = actions.reactions

class EditCursorTests(reactions.Base):
    async def asyncSetUp(self):
        patch = mock.patch.object(reactions, 'World', actions.EditWorld)
        patch.start(); self.addCleanup(patch.stop)
        await super().asyncSetUp()
        self.addCleanup(self.w.patch_release.set)
    edit_grant = actions.Actions.edit_grant
    edit = actions.Actions.edit
    async def prepared_message(self, grant):
        self.w.messages.pop('om_root')
        self.assertEqual((await self.deliver(self.root, grant)).status, 'acked')
        self.assertEqual(self.row(self.root, 'message').status, 'acked')
    async def drain(self, grant, latest):
        self.w.current_event = latest
        return await self.runtime().drain(self.w.target, target_id=grant.target_id,
            revision=grant.revision, scope_hash=grant.scope_hash)
    async def test_terminal_superseded_edit_allows_complete_cursor_without_fake_ack(self):
        grant = await self.edit_grant()
        await self.prepared_message(grant)
        older, latest = sorted((self.edit(text='cursor old'),self.edit(text='cursor latest')),
            key=lambda row:(row['created_at'],row['id']))
        result = await self.drain(grant, latest)
        self.assertEqual(self.row(latest, 'edit').status, 'acked')
        self.assertIsNone(self.row(older, 'edit'))
        self.assertEqual(len(self.w.patch_calls),1)
        self.assertEqual((result.status,result.acked_count,result.pending_count),('complete',2,0))
        self.assertEqual(self.db.conn.execute('SELECT position FROM remote_cursor WHERE target_id=?',
            (grant.target_id,)).fetchone()[0], self.w.now)
    async def test_older_unknown_blocks_cursor_after_latest_progress_and_never_repatches(self):
        grant = await self.edit_grant()
        await self.prepared_message(grant)
        older = self.edit(text='cursor uncertain old')
        self.w.lose_patch_response = True
        self.pending(await self.deliver(older,grant))
        old = self.row(older,'edit')
        self.assertEqual(old.status,'unknown')
        self.w.lose_patch_response = False
        latest = self.w.event(kind=40003,tags=[['e',self.root['id']]],text='cursor new',at=reactions.NOW+1)
        self.w.now += 2
        result = await self.drain(grant,latest)
        self.assertEqual(result.status,'pending')
        self.assertEqual(self.row(latest,'edit').status,'acked')
        self.assertEqual(self.row(older,'edit'),old)
        self.assertEqual(len(self.w.patch_calls),2)
        self.assertIsNone(self.db.conn.execute('SELECT position FROM remote_cursor WHERE target_id=?',
            (grant.target_id,)).fetchone())
    async def test_expired_native_card_is_not_patched_or_reserved(self):
        grant = await self.edit_grant(); event = self.edit()
        self.w.messages['om_root']['create_time'] = str((self.w.now-14*86400)*1000)
        self.pending(await self.deliver(event,grant))
        self.assertIsNone(self.row(event,'edit')); self.assertFalse(self.w.patch_calls)
    async def test_unknown_exact_get_recovers_after_patch_window_without_second_patch(self):
        grant = await self.edit_grant(); event = self.edit()
        self.w.lose_patch_response=True
        self.pending(await self.deliver(event,grant)); self.assertEqual(self.row(event,'edit').status,'unknown')
        self.w.lose_patch_response=False; self.reopen()
        self.w.messages['om_root']['create_time'] = str((self.w.now-14*86400)*1000)
        self.assertEqual((await self.deliver(event,grant)).status,'acked')
        self.assertEqual(len(self.w.patch_calls),1)
