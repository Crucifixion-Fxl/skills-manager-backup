"""Protected, real OnboardingRuntime inputs for root-assembly tests.

Only signed relay transport and the existing test-only bot CLI runner are local
I/O seams. RuntimeConfig, Store, catalog, native profile decryption, signed
source parsing, scheduler/pool pointers, and service construction stay real.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import test_hostd_agent_catalog as catalog_fixture
import test_hostd_onboarding_runtime as onboarding_fixture
import buzz_feishu_group_sync as gs
from hostd.onboarding_runtime import OnboardingRuntime, RuntimeConfig


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())
    path.chmod(0o600)
    return path


class RealOnboardingInput:
    """Own one protected schema-v1 row; never return a fake runtime/service."""

    def __init__(self, testcase, *, private_key=None, owner_key=None, app_id='cli_agent',
                 env_file=None, prompt_file=None, responsible_file=None, unit=None,
                 state_dir=None, profile_config_dir=None, profile_data_dir=None,
                 relay_key=None):
        # Reuse the canonical fixture's genuine encrypted app-secret/profile
        # input and cryptographic package availability; only metadata is adapted.
        if catalog_fixture.AESGCM is None:
            testcase.skipTest('protected app secret fixture requires cryptography; no synthetic crypto substitute')
        self.catalog_case = catalog_fixture.CatalogTests('test_ownbot_metadata_and_protected_hashes_only')
        self.catalog_case.setUp()
        testcase.addCleanup(self.catalog_case.doCleanups)
        f = self.catalog_case
        private_key = private_key or f.key
        owner_key = owner_key or f.owner_key
        self.pub = gs._signer_pubkey(private_key)
        self.owner = gs._signer_pubkey(owner_key)
        sig = gs.sync.nk.schnorr_sign(hashlib.sha256(f'nostr:agent-auth:{self.pub}:'.encode()).digest(),
                                      bytes.fromhex(owner_key), bytes(32)).hex()
        auth = json.dumps(['auth', self.owner, '', sig], separators=(',', ':'))
        # Match the trusted process fixture's actual protected files when a
        # caller supplies them; this supports genuine restart-scope validation.
        if env_file:
            env_path = Path(env_file)
            env_lines = [line for line in env_path.read_text().splitlines()
                         if not line.startswith('BUZZ_AUTH_TAG=')]
            env_lines.append('BUZZ_AUTH_TAG=' + json.dumps(['auth', self.owner, '', sig], separators=(',', ':')))
            _write(env_path, '\n'.join(env_lines) + '\n')
            self.env = env_path
            env_text = env_path.read_text()
        else:
            self.env = f.env
            env_text = (f'BUZZ_PRIVATE_KEY={private_key}\nBUZZ_ACP_AGENT_OWNER={self.owner}\n'
                        f'BUZZ_ACP_CHANNELS=\nBUZZ_ACP_SYSTEM_PROMPT_FILE={f.prompt}\n'
                        f'BUZZ_RESPONSIBLE_CONFIG={f.responsible}\nBUZZ_AUTH_TAG={auth}\n')
            _write(self.env, env_text)
        self.prompt = Path(prompt_file) if prompt_file else f.prompt
        self.responsible = Path(responsible_file) if responsible_file else f.responsible
        self.config_dir = Path(profile_config_dir) if profile_config_dir else f.cfg
        self.data_dir = Path(profile_data_dir) if profile_data_dir else f.data
        self.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        data_cli = self.data_dir / 'lark-cli'
        data_cli.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(self.config_dir / 'config.json', json.dumps({'apps': [{'appId': app_id, 'name': 'local'}]}))
        # Copy the canonical fixture's encrypted synthetic secret and key into
        # the exact protected profile paths consumed by the real runtime AND
        # publisher CLI fixture. Bytes remain fixture-owned, never output.
        source_cli = f.data / 'lark-cli'
        _write(data_cli / 'master.key', (source_cli / 'master.key').read_bytes())
        source_secret = source_cli / 'appsecret_cli_agent.enc'
        _write(data_cli / f'appsecret_{app_id}.enc', source_secret.read_bytes())
        f.key, f.pub, f.owner_key, f.owner, f.env_text = private_key, self.pub, owner_key, self.owner, env_text
        f.agent.update(name='local', env_file=str(self.env), unit=unit or 'buzz-local-local.service',
                       feishu={'app_id': app_id, 'lark_config_dir': str(self.config_dir), 'lark_data_dir': str(self.data_dir)})
        f.doc.update(owner_pubkey=self.owner, state_dir=str(state_dir or f.root / 'state'))
        f.save()
        self.owner_env = _write(f.root / 'owner.env', f'BUZZ_PRIVATE_KEY={owner_key}\n')
        self.template = _write(f.root / 'template.json', '{}')
        self.bindings = f.root / 'bindings'
        self.bindings.mkdir(mode=0o700, exist_ok=True)
        self.catalog = f.path
        self.legacy = f.legacy
        self.world = onboarding_fixture.World(f)
        if relay_key is not None:
            self.world.relay_key = relay_key
            self.world.pin = gs._signer_pubkey(relay_key)
        self.world.policy_app = app_id
        self.config = RuntimeConfig(version=1, owner_env_file=str(self.owner_env),
            relay_url='https://relay.test', relay_pubkey=self.world.pin,
            template_config=str(self.template), binding_dir=str(self.bindings),
            legacy_join_path=str(self.legacy), catalog_path=str(self.catalog),
            trusted_relays=('https://relay.test',))

    def factory_patch(self):
        """Patch only native lower I/O defaults and delegate to the real factory."""
        original = OnboardingRuntime.create.__func__
        world = self.world
        self.service = None

        async def actual_create(cls, config, store, **kwargs):
            kwargs.update(http=world.http, runner=world.runner, clock=lambda: world.now)
            self.service = await original(cls, config, store, **kwargs)
            return self.service

        return mock.patch.object(OnboardingRuntime, 'create', classmethod(actual_create))


def empty_runtime_config(root, relay_pubkey):
    """Valid no-agent catalog for tests whose assertion is root assembly only."""
    root = Path(root) / 'real-onboarding-input'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    owner_key = '2' * 64
    owner = gs._signer_pubkey(owner_key)
    owner_env = _write(root / 'owner.env', f'BUZZ_PRIVATE_KEY={owner_key}\n')
    template = _write(root / 'template.json', '{}')
    bindings = root / 'bindings'
    bindings.mkdir(mode=0o700, exist_ok=True)
    doc = {'version': 1, 'owner_pubkey': owner,
           'buzz': {'cli_path': '/synthetic/buzz', 'cli_sha256': 'a' * 64},
           'state_dir': str(root / 'state'), 'lark_cli': '/synthetic/lark-cli', 'agents': []}
    catalog = _write(root / 'catalog.json', json.dumps(doc))
    legacy = _write(root / 'legacy.json', json.dumps(doc))
    return RuntimeConfig(version=1, owner_env_file=str(owner_env), relay_url='https://relay.test',
        relay_pubkey=relay_pubkey, template_config=str(template), binding_dir=str(bindings),
        legacy_join_path=str(legacy), catalog_path=str(catalog), trusted_relays=('https://relay.test',))
