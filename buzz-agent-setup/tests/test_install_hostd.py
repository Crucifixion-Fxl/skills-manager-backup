"""Fixed-commit packaging and fail-closed candidate publication; no live services."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/install_hostd.py'
sys.path.insert(0,str(SCRIPT.parent))

@unittest.skipUnless(shutil.which('git'), 'fixed-commit archive tests require git; dependency environment executes every installer case')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('install_hostd', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        p=self.repo/'skills/agent-harness/buzz-agent-setup/scripts/hostd';p.mkdir(parents=True)
        (p/'__main__.py').write_text('print("fixed")\n')
        (p/'cli_runtime.py').write_bytes((SCRIPT.parent/'hostd/cli_runtime.py').read_bytes())
        (p/'requirements.lock').write_bytes((SCRIPT.parent/'hostd/requirements.lock').read_bytes())
        references=self.repo/'skills/agent-harness/buzz-agent-setup/references/scripts';references.mkdir(parents=True)
        (references/'fixture.py').write_text('# fixed runtime reference fixture\n')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','fixed'],check=True)
        self.rev=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        self.inventory=[]
        for i in range(15):
            state=self.base/f'state{i}.json';state.write_text('{}');state.chmod(0o600)
            self.inventory.append(dict(name=f'agent-{i}',channel_id=f'channel-{i}',chat_id_hash=hashlib.sha256(str(i).encode()).hexdigest(),app_id='cli_test',service=f'buzz-sync-{i}.service',timer=f'buzz-sync-{i}.timer',legacy_state=str(state)))
        self.calls=[];self.busy=False;self.pip_fail=False
        real=self.m.Runner()
        def runner(args,**kw):
            self.calls.append(args)
            if args[0]=='/usr/bin/systemctl':
                unit=args[3]
                raw=('Id='+unit+'\nLoadState=loaded\nActiveState='+('active' if unit.endswith('.timer') or self.busy else 'inactive')+'\nSubState='+('running' if self.busy else 'dead')+'\nUnitFileState='+('enabled' if unit.endswith('.timer') else 'static')+'\nMainPID='+('12' if self.busy else '0')+'\nControlPID=0\nTasksCurrent='+('2' if self.busy else '0')+'\nControlGroup=\nFragmentPath=\nDropInPaths=\nNeedDaemonReload=no\n')
                selected=set(args[-1].split('=',1)[1].split(','))
                return ('\n'.join(line for line in raw.splitlines() if line.split('=',1)[0] in selected)+'\n').encode()
            if '-m' in args and 'venv' in args:
                p=Path(args[-1])/'bin';p.mkdir(parents=True);(p/'python').write_text('fake');return b''
            if 'pip' in args:
                if self.pip_fail:raise self.m.InstallError('command_failed')
                return b''
            if '-c' in args and 'RuntimePaths' in args[args.index('-c')+1]:
                result=subprocess.run([sys.executable,*args[1:]],cwd=kw.get('cwd'),
                                      env=dict(PATH='/usr/bin:/bin',HOME=str(self.base),PYTHONDONTWRITEBYTECODE='1'),
                                      capture_output=True,check=True)
                return result.stdout
            if '-c' in args:
                return json.dumps(self.m.lock_versions((self.repo/'skills/agent-harness/buzz-agent-setup/scripts/hostd/requirements.lock').read_bytes())).encode()
            if '-m' in args and 'hostd' in args:return b'help'
            return real(args,**kw)
        self.runner=runner
    def plan(self,**kw):
        return self.m.check(self.repo,self.rev,self.inventory,self.base/'releases',self.base/'hostd.sqlite3',self.base/'status.json',runner=self.runner,**kw)
    def test_fixed_archive_excludes_wip_and_candidate_only(self):
        (self.repo/'skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py').write_text('WIP')
        plan=self.plan();result=self.m.prepare(plan,plan['digest'],runner=self.runner)
        release=Path(result['release'])
        self.assertEqual((release/'source/skills/agent-harness/buzz-agent-setup/scripts/hostd/__main__.py').read_text(),'print("fixed")\n')
        self.assertEqual(result['status'],'prepared')
        self.assertFalse(result['installed'])
        self.assertEqual((release/'candidate.service').stat().st_mode & 0o777,0o600)
        self.assertIn('"--only" "'+','.join(x['name'] for x in self.inventory)+'"',(release/'candidate.service').read_text())
        self.assertFalse(any('start' in x or 'stop' in x or 'daemon-reload' in x for x in self.calls))
        self.assertEqual((release/'rollback/state0.json').stat().st_mode & 0o777,0o600)
        self.assertEqual((release/'source').stat().st_mode & 0o777,0o555)
    def test_full_revision_required(self):
        self.rev='HEAD'
        with self.assertRaises(self.m.InstallError):self.plan()
    def test_generated_working_directory_passes_real_systemd_user_verify(self):
        release=self.base/'systemd-release'
        working=release/'source'/self.m.BASE;working.mkdir(parents=True)
        binary=release/'venv/bin/python';binary.parent.mkdir(parents=True)
        binary.symlink_to(Path(sys.executable).resolve())
        text=self.m.unit(release,self.inventory,self.base/'hostd.sqlite3',self.base/'status.json')
        candidate=self.base/'installer-directory-check.service';candidate.write_text(text)
        # Parse a standalone unit using the real systemd parser. No service is
        # published, started or loaded into the user manager by verify.
        verified=subprocess.run(['/usr/bin/systemd-analyze','--user','verify',str(candidate)],
            env=dict(PATH='/usr/bin:/bin',HOME=str(self.base),LANG='C.UTF-8',
                     XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}'),
            capture_output=True,text=True,timeout=15)
        self.assertEqual(verified.returncode,0,verified.stderr)
        self.assertIn('\nWorkingDirectory='+str(working)+'\n',text)
        self.assertIn('\nExecStart="'+str(binary)+'" "-m" "hostd" "run" ',text)
    def test_exact_fifteen_and_unique_identity(self):
        for inventory in [self.inventory[:-1],self.inventory+[self.inventory[0]],self.inventory[:-1]+[self.inventory[0]]]:
            with self.assertRaises(self.m.InstallError):self.m.check(self.repo,self.rev,inventory,self.base/'releases',self.base/'db',self.base/'status',runner=self.runner)
    def test_busy_is_deferred_and_no_release(self):
        self.busy=True;plan=self.plan()
        self.assertEqual(plan['status'],'busy')
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertFalse((self.base/'releases').exists())
    def test_tampered_plan_rejected(self):
        plan=self.plan();plan['targets'][0]['name']='injected'
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
    def test_path_and_name_injection(self):
        self.inventory[0]['name']='bad%\nExecStart=/bin/false'
        with self.assertRaises(self.m.InstallError):self.plan()
    def test_symlink_ancestor_rejected(self):
        (self.base/'alias').symlink_to(self.base,target_is_directory=True)
        with self.assertRaises(self.m.InstallError):self.m.check(self.repo,self.rev,self.inventory,self.base/'alias/releases',self.base/'db',self.base/'status',runner=self.runner)
    def test_worktree_destination_rejected(self):
        with self.assertRaises(self.m.InstallError):self.m.check(self.repo,self.rev,self.inventory,self.repo/'releases',self.base/'db',self.base/'status',runner=self.runner)
    def test_manager_changes_before_apply_rejected(self):
        plan=self.plan();self.busy=True
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertFalse((self.base/'releases').exists())
    def test_failed_dependency_install_retains_failed_artifact(self):
        plan=self.plan();self.pip_fail=True
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
        failures=list((self.base/'releases').glob('.prepare-*/receipt.json'))
        self.assertEqual(len(failures),1)
        self.assertEqual(json.loads(failures[0].read_text())['status'],'failed')
    def test_lock_must_pin_required_runtime_and_reject_empty(self):
        for content in [b'',b'pkg==1.0 --hash=sha256:'+b'a'*64+b'\n',b'--index-url https://evil.invalid\n']:
            with self.assertRaises(self.m.InstallError):self.m.lock_versions(content)
    def test_archive_symlink_and_traversal_rejected(self):
        import io,tarfile
        for name,kind in [('alias',tarfile.SYMTYPE),('../escape',tarfile.REGTYPE)]:
            out=io.BytesIO()
            with tarfile.open(fileobj=out,mode='w') as tar:
                info=tarfile.TarInfo(name);info.type=kind;info.linkname='/etc/passwd';tar.addfile(info)
            with self.assertRaises(self.m.InstallError):self.m.archive_files(out.getvalue())
    def test_snapshot_hardlink_rejected(self):
        state=Path(self.inventory[0]['legacy_state']);os.link(state,self.base/'hardlink')
        with self.assertRaises(self.m.InstallError):self.plan()
    def test_manager_ambiguous_output_rejected(self):
        with self.assertRaises(self.m.InstallError):self.m.states(self.inventory,lambda *a,**kw:b'Id=x\nId=x\n')
    def test_runner_bounds_and_redacts_stderr(self):
        with self.assertRaises(self.m.InstallError) as found:self.m.Runner()(['/usr/bin/python3','-c','import sys; print("SECRET",file=sys.stderr);sys.exit(2)'])
        self.assertNotIn('SECRET',str(found.exception))
        with self.assertRaises(self.m.InstallError):self.m.Runner()(['/usr/bin/python3','-c','import time;time.sleep(2)'],timeout=.05)
    def test_rollback_export_requires_stopped_hostd_and_private_destination(self):
        self.busy=True
        with self.assertRaises(self.m.InstallError):self.m.export_rollback(self.base/'release',self.inventory,self.base/'db',self.base/'rollback-current',runner=self.runner)
        self.assertFalse((self.base/'rollback-current').exists())
    def test_actual_timer_shape_and_inactive_service_tasks_not_set(self):
        original=self.runner
        def native(args,**kw):
            raw=original(args,**kw)
            if args[0]=='/usr/bin/systemctl' and args[3].endswith('.service'):
                raw=raw.replace(b'TasksCurrent=0',b'TasksCurrent=[not set]')
            return raw
        self.assertEqual(self.m.check(self.repo,self.rev,self.inventory,self.base/'release',self.base/'db',self.base/'status',runner=native)['status'],'reviewable')
    def test_database_and_status_must_differ(self):
        with self.assertRaises(self.m.InstallError):self.m.check(self.repo,self.rev,self.inventory,self.base/'release',self.base/'same',self.base/'same',runner=self.runner)
    def test_sqlite_snapshot_includes_committed_wal_and_is_private(self):
        import sqlite3
        source=self.base/'source.sqlite3'
        with sqlite3.connect(source) as db:
            db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE example(value)');db.execute('INSERT INTO example VALUES (42)');db.commit();source.chmod(0o600)
            target=self.base/'copy.sqlite3';self.m.snapshot_database(source,target)
            with sqlite3.connect(target) as copied:self.assertEqual(copied.execute('SELECT value FROM example').fetchone(),(42,))
            self.assertEqual(target.stat().st_mode & 0o777,0o600)
    def test_dependency_readback_mismatch_retains_failed_evidence(self):
        original=self.runner
        def mismatch(args,**kw):return b'{}' if '-c' in args else original(args,**kw)
        plan=self.plan()
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=mismatch)
        self.assertFalse(Path(plan['release']).exists())
    def test_token_environment_not_forwarded(self):
        os.environ['LARK_APP_SECRET']='SECRET'
        try:self.assertEqual(self.m.Runner()(['/usr/bin/python3','-c','import os;print(os.getenv("LARK_APP_SECRET","absent"))']).strip(),b'absent')
        finally:os.environ.pop('LARK_APP_SECRET',None)
    def test_final_relocated_python_readback_failure_is_failed(self):
        original=self.runner;calls=0
        def final_failure(args,**kw):
            nonlocal calls
            if '-c' in args:
                calls+=1
                if calls==2:raise self.m.InstallError('final_readback_failed')
            return original(args,**kw)
        plan=self.plan()
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=final_failure)
        receipt=json.loads((Path(plan['release'])/'receipt.json').read_text())
        self.assertEqual(receipt['status'],'failed')
    def test_unrelated_repository_symlinks_excluded_from_runtime_archive(self):
        (self.repo/'unrelated-link').symlink_to('/outside')
        subprocess.run(['git','-C',str(self.repo),'add','unrelated-link'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','unrelated link'],check=True)
        self.rev=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        self.assertEqual(self.plan()['status'],'reviewable')
    def test_fixed_runtime_references_import_in_published_source_without_wip_or_siblings(self):
        scripts=self.repo/'skills/agent-harness/buzz-agent-setup/scripts'
        references=self.repo/'skills/agent-harness/buzz-agent-setup/references/scripts'
        originals={scripts/'gitlab_buzz_sync.py':(SCRIPT.parent/'gitlab_buzz_sync.py').read_bytes(),
                   scripts/'buzz_responsible_mentions.py':(SCRIPT.parent/'buzz_responsible_mentions.py').read_bytes(),
                   references/'nostrkit.py':(SCRIPT.parent.parent/'references/scripts/nostrkit.py').read_bytes()}
        for destination,raw in originals.items():destination.write_bytes(raw)
        sibling=self.repo/'skills/agent-harness/buzz-agent-setup/references/not-runtime.txt';sibling.write_text('excluded sibling')
        (self.repo/'unrelated-link').symlink_to('/outside')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','runtime references'],check=True)
        self.rev=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        for destination in originals:destination.write_text('raise RuntimeError("uncommitted WIP")\n')
        (references/'untracked.py').write_text('raise RuntimeError("untracked WIP")\n')
        plan=self.plan();result=self.m.prepare(plan,plan['digest'],runner=self.runner)
        source=Path(result['release'])/'source';working=source/'skills/agent-harness/buzz-agent-setup/scripts'
        # Real ordinary Python import from published files, never the fake venv
        # runner or this test process's installed/module-cache search path.
        code=('from pathlib import Path; import gitlab_buzz_sync as sync; '
              'assert Path(sync.nk.__file__).resolve()==Path.cwd().parent/"references/scripts/nostrkit.py"; '
              'assert Path(sync.responsible.__file__).resolve()==Path.cwd()/"buzz_responsible_mentions.py"; '
              'print("runtime references imported")')
        imported=subprocess.run([sys.executable,'-E','-s','-B','-c',code],cwd=working,
            env=dict(PATH='/usr/bin:/bin',HOME=str(self.base),LANG='C.UTF-8'),
            capture_output=True,text=True,timeout=15)
        self.assertEqual(imported.returncode,0,imported.stderr)
        self.assertEqual(imported.stdout.strip(),'runtime references imported')
        manifest=json.loads((Path(result['release'])/'source-manifest.json').read_text())
        for original,raw in originals.items():
            relative=original.relative_to(self.repo)
            self.assertEqual((source/relative).read_bytes(),raw)
            self.assertEqual(manifest[str(relative)],hashlib.sha256(raw).hexdigest())
            self.assertEqual((source/relative).stat().st_mode & 0o777,0o444)
        for relative in (sibling.relative_to(self.repo),Path('unrelated-link'),
                         Path('skills/agent-harness/buzz-agent-setup/references/scripts/untracked.py')):
            self.assertFalse((source/relative).exists())
    def test_runtime_reference_symlink_is_rejected_by_real_fixed_archive(self):
        link=self.repo/'skills/agent-harness/buzz-agent-setup/references/scripts/unsafe-link'
        link.symlink_to('/outside')
        subprocess.run(['git','-C',str(self.repo),'add',str(link)],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=Test','-c','user.email=test@invalid','commit','-qm','unsafe runtime reference'],check=True)
        self.rev=subprocess.check_output(['git','-C',str(self.repo),'rev-parse','HEAD'],text=True).strip()
        with self.assertRaises(self.m.InstallError) as error:self.plan()
        self.assertEqual(error.exception.code,'archive_invalid')
        self.assertFalse((self.base/'releases').exists())
    def test_absent_legacy_ledger_is_preserved_as_absence(self):
        state=Path(self.inventory[0]['legacy_state']);state.unlink()
        self.inventory[0]['legacy_state']=str(self.base/'no-prior-state'/'state.json')
        plan=self.plan()
        self.assertIsNone(plan['legacy_sha256']['agent-0'])
        result=self.m.prepare(plan,plan['digest'],runner=self.runner)
        release=Path(result['release'])
        self.assertFalse((release/'rollback/state0.json').exists())
        saved=json.loads((release/'rollback/manifest.json').read_text())
        self.assertIsNone(saved['legacy_sha256']['agent-0'])
        self.assertFalse(Path(self.inventory[0]['legacy_state']).exists())
    def test_absence_to_present_or_present_to_absent_conflict_blocks_prepare(self):
        state=Path(self.inventory[0]['legacy_state']);state.unlink()
        plan=self.plan();state.write_text('{}');state.chmod(0o600)
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
        plan=self.plan();state.unlink()
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
    def test_absent_ledger_does_not_accept_symlink_or_fifo(self):
        state=Path(self.inventory[0]['legacy_state']);state.unlink()
        state.symlink_to(self.base/'not-present')
        with self.assertRaises(self.m.InstallError):self.plan()
        state.unlink();os.mkfifo(state,0o600)
        with self.assertRaises(self.m.InstallError):self.plan()
    def test_absent_ledger_appearing_during_dependency_preparation_blocks_publish(self):
        state=Path(self.inventory[0]['legacy_state']);state.unlink();plan=self.plan()
        original=self.runner
        def appears(args,**kw):
            if 'pip' in args and 'install' in args:state.write_text('{}');state.chmod(0o600)
            return original(args,**kw)
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=appears)
        self.assertFalse(Path(plan['release']).exists())
    def test_error_human_remedy(self):
        public=self.m.InstallError('invalid').public()
        self.assertFalse(public['installed']);self.assertIn('怎么解决',public['remediation']);self.assertIn('复制给 AI',public['remediation'])

    def startup(self):
        config=self.base/'PRIVATE-startup-config.json'
        value=dict(version=1,owner_env_file=str(self.base/'PRIVATE-owner.env'),relay_url='wss://relay.test',relay_pubkey='a'*64,
                   template_config=str(self.base/'template.json'),binding_dir=str(self.base/'bindings'),
                   legacy_join_path=str(self.base/'joins.json'),catalog_path=str(self.base/'catalog.json'),trusted_relays=['wss://relay.test'])
        raw=json.dumps(value).encode();config.write_bytes(raw);config.chmod(0o600)
        return config,hashlib.sha256(raw).hexdigest(),value
    def onboarding_plan(self):
        config,pin,_=self.startup()
        return self.plan(onboarding_config=config,onboarding_config_sha256=pin,migrate_bot_readers=True),config,pin
    def test_explicit_pinned_startup_and_migration_are_candidate_flags_only(self):
        plan,config,pin=self.onboarding_plan()
        self.assertEqual(plan['onboarding']['status'],'pinned')
        self.assertEqual(plan['onboarding']['sha256'],pin)
        self.assertTrue(plan['migrate_bot_readers'])
        self.assertIn('"--onboarding-config" "'+plan['release']+'/onboarding-config.json"',plan['desired_unit'])
        self.assertIn('"--migrate-bot-readers"',plan['desired_unit'])
        self.assertNotIn(str(config),plan['desired_unit'])
        self.assertNotIn('PRIVATE-owner.env',plan['desired_unit'])
        self.assertFalse((self.base/'releases').exists())
        self.assertFalse(any('pip' in call or 'start' in call for call in self.calls))
    def test_default_startup_pending_and_no_implicit_migration(self):
        plan=self.plan()
        self.assertEqual(plan['onboarding']['status'],'pending')
        self.assertFalse(plan['migrate_bot_readers'])
        self.assertNotIn('--onboarding-config',plan['desired_unit']);self.assertNotIn('--migrate-bot-readers',plan['desired_unit'])
    def test_startup_explicit_sha_pair_and_exact_hash_required(self):
        config,pin,_=self.startup()
        for opts in ({'onboarding_config':config},{'onboarding_config_sha256':pin},{'onboarding_config':config,'onboarding_config_sha256':'0'*64},
                     {'onboarding_config':config,'onboarding_config_sha256':'HEAD'},{'migrate_bot_readers':'yes'}):
            with self.subTest(opts=list(opts)):
                with self.assertRaises(self.m.InstallError):self.plan(**opts)
        self.assertFalse((self.base/'releases').exists())
    def test_startup_permissions_symlink_duplicates_and_unknown_secret_fields_rejected(self):
        config,pin,value=self.startup()
        config.chmod(0o644)
        with self.assertRaises(self.m.InstallError):self.plan(onboarding_config=config,onboarding_config_sha256=pin)
        config.chmod(0o600);link=self.base/'startup-link';link.symlink_to(config)
        with self.assertRaises(self.m.InstallError):self.plan(onboarding_config=link,onboarding_config_sha256=pin)
        for raw in (json.dumps(dict(value,token='PRIVATE_SECRET')),json.dumps(dict(value,version=True)),json.dumps(dict(value,catalog_path='relative')),
                    json.dumps(dict(value,relay_pubkey='invalid')),json.dumps(dict(value,trusted_relays='wss://relay.test')),
                    '{"version":1,"version":1}'):
            config.write_text(raw)
            with self.assertRaises(self.m.InstallError) as caught:
                self.plan(onboarding_config=config,onboarding_config_sha256=hashlib.sha256(raw.encode()).hexdigest())
            self.assertNotIn('PRIVATE',str(caught.exception));self.assertNotIn('PRIVATE',json.dumps(caught.exception.public()))
    def test_startup_change_or_disappearance_before_prepare_blocks_all_writes(self):
        plan,config,_=self.onboarding_plan();config.write_bytes(config.read_bytes()+b' ')
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertFalse((self.base/'releases').exists())
        plan=self.plan(onboarding_config=config,onboarding_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
        config.unlink()
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertFalse((self.base/'releases').exists())
    def test_prepare_copies_exact_private_startup_and_readbacks_fixed_runtime_schema(self):
        plan,config,pin=self.onboarding_plan();original=self.runner
        def verified(args,**kw):
            if '-c' in args and 'RuntimeConfig' in args[args.index('-c')+1]:
                self.calls.append(args)
                copied=Path(args[-1]);self.assertEqual(copied.read_bytes(),config.read_bytes())
                return b'validated\n'
            return original(args,**kw)
        result=self.m.prepare(plan,plan['digest'],runner=verified)
        release=Path(result['release']);copied=release/'onboarding-config.json'
        self.assertEqual(copied.read_bytes(),config.read_bytes());self.assertEqual(copied.stat().st_mode&0o777,0o600)
        self.assertEqual(result['onboarding_sha256'],pin);self.assertTrue(result['migrate_bot_readers'])
        self.assertFalse(result['installed'])
        self.assertIn(str(copied),(release/'candidate.service').read_text())
        self.assertTrue(any('RuntimeConfig' in arg for call in self.calls for arg in call))
    def test_startup_change_during_dependency_prepare_blocks_publication(self):
        plan,config,_=self.onboarding_plan();original=self.runner
        def changed(args,**kw):
            if 'pip' in args and 'install' in args:config.write_bytes(config.read_bytes()+b' ')
            if '-c' in args and 'RuntimeConfig' in args[args.index('-c')+1]:return b'validated\n'
            return original(args,**kw)
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=changed)
        self.assertFalse(Path(plan['release']).exists())
        self.assertTrue(list((self.base/'releases').glob('.prepare-*/receipt.json')))
    def test_invalid_relocated_startup_runtime_readback_is_failed_not_prepared(self):
        plan,config,_=self.onboarding_plan();original=self.runner;validations=0
        def changed(args,**kw):
            nonlocal validations
            if '-c' in args and 'RuntimeConfig' in args[args.index('-c')+1]:
                validations+=1
                if validations==2:raise self.m.InstallError('startup_validation_failed')
                return b'validated\n'
            return original(args,**kw)
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=changed)
        self.assertEqual(json.loads((Path(plan['release'])/'receipt.json').read_text())['status'],'failed')
    def test_cli_explicit_flags_redact_private_source_path_from_public_output(self):
        import contextlib,io
        from unittest.mock import patch
        config,pin,_=self.startup();inventory=self.base/'inventory.json';inventory.write_text(json.dumps(self.inventory));inventory.chmod(0o600)
        argv=['install_hostd.py','--check','--repo',str(self.repo),'--revision',self.rev,'--inventory',str(inventory),
              '--release-root',str(self.base/'releases'),'--state-db',str(self.base/'db'),'--status-file',str(self.base/'status'),
              '--onboarding-config',str(config),'--onboarding-config-sha256',pin,'--migrate-bot-readers']
        output=io.StringIO()
        with patch.object(self.m.sys,'argv',argv),patch.object(self.m,'Runner',return_value=self.runner),contextlib.redirect_stdout(output):
            self.assertEqual(self.m.main(),0)
        public=json.loads(output.getvalue());self.assertTrue(public['migrate_bot_readers']);self.assertEqual(public['onboarding']['sha256'],pin)
        self.assertNotIn(str(config),output.getvalue());self.assertNotIn('PRIVATE-owner.env',output.getvalue())

    def test_unit_startup_sha_guard_executes_before_main_and_rejects_modified_content(self):
        import shlex,sys
        plan,config,pin=self.onboarding_plan()
        lines=plan['desired_unit'].splitlines()
        guards=[line.split('=',1)[1] for line in lines if line.startswith('ExecStartPre=')]
        self.assertEqual(len(guards),1)
        argv=shlex.split(guards[0]);self.assertEqual(argv[0],str(Path(plan['release'])/'venv/bin/python'))
        self.assertEqual(argv[-2],str(Path(plan['release'])/'onboarding-config.json'));self.assertEqual(argv[-1],pin)
        self.assertLess(lines.index('ExecStartPre='+guards[0]),next(i for i,line in enumerate(lines) if line.startswith('ExecStart=')))
        actual=[sys.executable,*argv[1:-2],str(config),pin]
        env=dict(PATH='/usr/bin:/bin',HOME=str(self.base),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
        self.assertEqual(subprocess.run(actual,cwd=str(SCRIPT.parent),env=env,capture_output=True).returncode,0)
        config.write_bytes(config.read_bytes()+b' ')
        result=subprocess.run(actual,cwd=str(SCRIPT.parent),env=env,capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertNotIn(b'PRIVATE',result.stdout+result.stderr)

    def test_default_prepared_receipt_explicitly_reports_startup_pending(self):
        plan=self.plan();result=self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertEqual(result['onboarding_status'],'pending');self.assertIsNone(result['onboarding_sha256'])
        self.assertIn('startup configuration pending',result['verification']);self.assertFalse(result['installed'])

    def cli_paths(self):
        # The runtime validator refuses writable /tmp ancestors. This is a new
        # synthetic private tree only; no personal profile or installed CLI read.
        temp=tempfile.TemporaryDirectory(prefix='hostd-cli-test-',dir=Path.home())
        self.addCleanup(temp.cleanup)
        root=Path(temp.name);root.chmod(0o700)
        node=root/'portable node';node.write_text('#!/bin/sh\nexit 99\n');node.chmod(0o700)
        entry=root/'cli entry.js';entry.write_text('throw new Error("must not execute");\n');entry.chmod(0o600)
        return node,entry

    def test_cli_default_native_http_candidate_does_not_discover_inherited_paths(self):
        from unittest.mock import patch
        with patch.dict(os.environ,{'HOSTD_NODE_BINARY':'/unreviewed/node','HOSTD_LARK_CLI_ENTRY':'/unreviewed/cli.js','PATH':'/usr/bin:/bin'}):
            plan=self.plan()
        self.assertEqual(plan['cli_runtime']['status'],'pending')
        self.assertNotIn('HOSTD_NODE_BINARY=',plan['desired_unit'])
        self.assertNotIn('HOSTD_LARK_CLI_ENTRY=',plan['desired_unit'])
        result=self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertEqual(result['cli_runtime_status'],'pending')
        self.assertIn('media CLI paths pending',result['verification'])

    def test_explicit_cli_paths_are_validated_offline_and_exact_unit_environment(self):
        from unittest.mock import patch
        node,entry=self.cli_paths()
        with patch.dict(os.environ,{'PATH':'/usr/bin:/bin','HOSTD_NODE_BINARY':'/ignored/node','HOSTD_LARK_CLI_ENTRY':'/ignored/cli.js'}):
            plan=self.plan(node_binary=str(node),lark_cli_entry=str(entry))
        self.assertEqual(plan['cli_runtime']['status'],'configured')
        self.assertEqual(plan['cli_runtime']['node_binary'],str(node))
        self.assertEqual(plan['cli_runtime']['lark_cli_entry'],str(entry))
        self.assertIn('Environment="HOSTD_NODE_BINARY='+str(node)+'"',plan['desired_unit'])
        self.assertIn('Environment="HOSTD_LARK_CLI_ENTRY='+str(entry)+'"',plan['desired_unit'])
        self.assertFalse(any(call[0] in (str(node),str(entry)) for call in self.calls))
        self.assertFalse((self.base/'releases').exists())
        self.assertNotIn('sha256',json.dumps(plan['cli_runtime']))

    def test_cli_paired_absolute_inputs_required_and_rule16_failure(self):
        node,entry=self.cli_paths()
        for opts in ({'node_binary':str(node)},{'lark_cli_entry':str(entry)},
                     {'node_binary':'relative','lark_cli_entry':str(entry)},
                     {'node_binary':str(node),'lark_cli_entry':'relative'},
                     {'node_binary':'','lark_cli_entry':str(entry)}):
            with self.subTest(opts=list(opts)):
                with self.assertRaises(self.m.InstallError) as caught:self.plan(**opts)
                text=json.dumps(caught.exception.public(),ensure_ascii=False)
                self.assertIn('怎么解决',text);self.assertIn('复制给 AI',text)
                self.assertNotIn(str(node),text);self.assertNotIn(str(entry),text)
        self.assertFalse((self.base/'releases').exists())

    def test_cli_unsafe_nonexecutables_writable_entries_links_and_missing_rejected(self):
        node,entry=self.cli_paths()
        bad=node.parent/'bad';bad.write_text('not executable');bad.chmod(0o600)
        link=node.parent/'node-link';link.symlink_to(node)
        entry_link=node.parent/'entry-link';entry_link.symlink_to(entry)
        fifo=node.parent/'fifo';os.mkfifo(fifo,0o600)
        for n,e in ((bad,entry),(link,entry),(node,entry_link),(node,fifo),(node,node.parent),(node,node.parent/'missing')):
            with self.subTest(node=n.name,entry=e.name):
                with self.assertRaises(self.m.InstallError):self.plan(node_binary=str(n),lark_cli_entry=str(e))
        entry.chmod(0o666)
        with self.assertRaises(self.m.InstallError):self.plan(node_binary=str(node),lark_cli_entry=str(entry))
        entry.chmod(0o600);node.parent.chmod(0o777)
        try:
            with self.assertRaises(self.m.InstallError):self.plan(node_binary=str(node),lark_cli_entry=str(entry))
        finally:node.parent.chmod(0o700)

    def test_cli_prepare_candidate_and_private_receipt_keep_exact_reviewed_paths(self):
        node,entry=self.cli_paths();plan=self.plan(node_binary=str(node),lark_cli_entry=str(entry))
        result=self.m.prepare(plan,plan['digest'],runner=self.runner)
        self.assertEqual(result['cli_runtime_status'],'configured');self.assertFalse(result['installed'])
        unit=(Path(result['release'])/'candidate.service').read_text()
        self.assertIn('Environment="HOSTD_NODE_BINARY='+str(node)+'"',unit)
        self.assertIn('Environment="HOSTD_LARK_CLI_ENTRY='+str(entry)+'"',unit)
        saved=json.loads((Path(result['release'])/'rollback/manifest.json').read_text())
        self.assertEqual(saved['cli_runtime'],plan['cli_runtime'])
        self.assertTrue(any('-c' in call and 'RuntimePaths' in call[call.index('-c')+1] for call in self.calls))
        self.assertTrue((Path(result['release'])/'source/skills/agent-harness/buzz-agent-setup/scripts/hostd/cli_runtime.py').is_file())
        self.assertFalse(any(call[0] in (str(node),str(entry)) for call in self.calls))

    def test_cli_reviewed_file_identity_or_permissions_change_blocks_prepare(self):
        for change in ('replace','permission','edit'):
            with self.subTest(change=change):
                node,entry=self.cli_paths();plan=self.plan(node_binary=str(node),lark_cli_entry=str(entry))
                if change=='replace':
                    replacement=entry.parent/'replacement';replacement.write_bytes(entry.read_bytes());replacement.chmod(0o600);replacement.replace(entry)
                elif change=='permission':node.chmod(0o755)
                else:entry.write_text('review changed\n')
                with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=self.runner)
                self.assertFalse((self.base/'releases').exists())

    def test_cli_change_during_dependency_prepare_blocks_candidate_publication(self):
        node,entry=self.cli_paths();plan=self.plan(node_binary=str(node),lark_cli_entry=str(entry));original=self.runner
        def changed(args,**kw):
            if 'pip' in args and 'install' in args:entry.write_text('changed during prepare\n')
            return original(args,**kw)
        with self.assertRaises(self.m.InstallError):self.m.prepare(plan,plan['digest'],runner=changed)
        self.assertFalse(Path(plan['release']).exists())
        self.assertTrue(list((self.base/'releases').glob('.prepare-*/receipt.json')))

    def test_installer_cli_accepts_explicit_runtime_paths_without_executing_them(self):
        import contextlib,io
        from unittest.mock import patch
        node,entry=self.cli_paths();inventory=self.base/'inventory.json';inventory.write_text(json.dumps(self.inventory));inventory.chmod(0o600)
        argv=['install_hostd.py','--check','--repo',str(self.repo),'--revision',self.rev,'--inventory',str(inventory),
              '--release-root',str(self.base/'releases'),'--state-db',str(self.base/'db'),'--status-file',str(self.base/'status'),
              '--node-binary',str(node),'--lark-cli-entry',str(entry)]
        output=io.StringIO()
        with patch.object(self.m.sys,'argv',argv),patch.object(self.m,'Runner',return_value=self.runner),contextlib.redirect_stdout(output):
            self.assertEqual(self.m.main(),0)
        self.assertEqual(json.loads(output.getvalue())['cli_runtime']['status'],'configured')
        self.assertFalse(any(call[0] in (str(node),str(entry)) for call in self.calls))

if __name__=='__main__':unittest.main()
