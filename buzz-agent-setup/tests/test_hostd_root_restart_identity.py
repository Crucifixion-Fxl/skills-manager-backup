"""Root-created real restart driver/DB must share canonical typed process DTOs.

Only the unrelated onboarding factory response is stubbed. No process operation,
credential lookup, systemd call, network transport or restart itself is invoked.
"""
import sys
import unittest

import test_hostd_wiring_lifecycle as fixture
import buzz_feishu_group_sync as gs
from hostd import agent_operations
from hostd.store import RestartProcess
from hostd_real_onboarding_fixture import empty_runtime_config

AGENT = 'a' * 64
SCOPE = 'c' * 64
FILES = 'd' * 64
OPERATION = 'restart_identity_fixture'


class RootRestartIdentity(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.Wiring.setUp

    async def asyncSetUp(self):
        # A genuine protected empty catalog is sufficient here: these cases
        # exercise Store/driver DTO identity, not agent admission or restart.
        self.h.onboarding_config = empty_runtime_config(self.tmp, gs._signer_pubkey('7' * 64))
        await self.h.start_onboarding()
        self.addAsyncCleanup(self.h.close_onboarding)
        self.driver = self.h.restart_agent
        self.assertIs(type(self.driver), agent_operations.AgentRestartDriver)
        self.assertIs(self.driver.store, self.h.runtime_store)
        self.db = self.driver.store
        self.db.register_agent(AGENT, owner_pubkey='b'*64, app_id='cli_fixture',
                               config_path=str(self.tmp/'agent.env'), now=100)

    def seed_unknown(self):
        # Seed only earlier phases in the DB's native DTO namespace, so the
        # particular pin/ACK boundary is exercised even while reserve is broken.
        native = sys.modules[type(self.db).__module__].RestartProcess
        old = native(101, 1001, '1'*32)
        reservation = self.db.reserve_restart(OPERATION, AGENT, SCOPE, FILES, old, now=100)
        self.assertTrue(reservation.created)
        self.assertTrue(self.db.mark_restart_unknown(OPERATION, AGENT, SCOPE, FILES, now=101))
        return native

    async def test_root_store_accepts_actual_driver_restart_dto_at_reservation(self):
        self.assertIs(agent_operations.RestartProcess, RestartProcess)
        result = self.db.reserve_restart(OPERATION, AGENT, SCOPE, FILES,
                                         agent_operations.RestartProcess(101, 1001, '1'*32), now=100)
        self.assertTrue(result.created)
        self.assertEqual(result.record.old_process, RestartProcess(101, 1001, '1'*32))

    async def test_root_store_accepts_actual_driver_restart_dto_at_pin(self):
        self.seed_unknown()
        self.assertTrue(self.db.pin_restart(OPERATION, AGENT, SCOPE, FILES,
                                             agent_operations.RestartProcess(102, 1002, '2'*32), now=102))
        self.assertEqual(self.db.restart_record(OPERATION).new_process, RestartProcess(102, 1002, '2'*32))

    async def test_root_store_accepts_actual_driver_restart_dto_at_ack(self):
        native = self.seed_unknown()
        self.assertTrue(self.db.pin_restart(OPERATION, AGENT, SCOPE, FILES,
                                             native(102, 1002, '2'*32), now=102))
        self.assertTrue(self.db.ack_restart(OPERATION, AGENT, SCOPE, FILES,
                                             agent_operations.RestartProcess(102, 1002, '2'*32), now=103))
        self.assertEqual(self.db.restart_record(OPERATION).state, 'acked')
