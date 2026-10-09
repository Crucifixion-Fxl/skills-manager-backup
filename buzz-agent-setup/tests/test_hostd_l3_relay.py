"""Prepared command evidence only: all Docker/port I/O fake, no Docker started."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent/'localstack'))
RUN='a'*32
OWNER='b'*64
SECRET='4'*64
IMAGES={role:repo+'@sha256:'+char*64 for role,repo,char in
        [('postgres','postgres','1'),('redis','redis','2'),('relay','ghcr.io/block/buzz','3')]}


class Docker:
    def __init__(self):
        self.calls=[];self.resources={};self.next_id=1;self.fail=None;self.ready=True
        self.images={image:{'Id':'sha256:'+str(i)*64,'RepoDigests':[image]} for i,image in enumerate(IMAGES.values(),1)}
    def __call__(self,cmd,*,env,timeout,capture_output,text):
        self.calls.append((list(cmd),dict(env),timeout));args=cmd[1:]
        def result(value='',code=0):return subprocess.CompletedProcess(cmd,code,value,'untrusted SECRET diagnostic')
        if self.fail and args[:len(self.fail)]==self.fail: return result(code=1)
        if args[:2]==['image','inspect']:
            row=self.images.get(args[-1]);return result(json.dumps(row) if row else '',0 if row else 1)
        if args[:2] in (['container','ls'],['network','ls']):
            kind=args[0];filter_key,pattern=args[args.index('--filter')+1].split('=',1)
            name=pattern.strip('^$').lstrip('/')
            row=next((r for r in self.resources.values() if r['Id']==pattern),None) if filter_key=='id' else self.resources.get(name)
            if kind=='container' and row and '-a' not in args and row.get('State','running')!='running':row=None
            return result(json.dumps({'ID':row['Id'],'Names':name,'Name':name}) if row and row['kind']==kind else '')
        if args[:2]==['network','create'] or args[0]=='run':
            kind='network' if args[0]=='network' else 'container'
            name=args[-1] if kind=='network' else args[args.index('--name')+1]
            labels={args[i+1].split('=',1)[0]:args[i+1].split('=',1)[1] for i,a in enumerate(args) if a=='--label'}
            identity=f'{self.next_id:064x}';self.next_id+=1
            row={'Id':identity,'Name':('/' if kind=='container' else '')+name,'Labels':labels,'kind':kind,'State':'running'}
            if kind=='container':row['Image']=self.images[args[-1]]['Id']
            self.resources[name]=row;return result(identity+'\n')
        if args[:2] in (['container','inspect'],['network','inspect']):
            row=next((r for n,r in self.resources.items() if args[-1] in (n,r['Id'])),None)
            return result(json.dumps(row) if row else '',0 if row else 1)
        if args[0]=='exec':return result('ready' if self.ready else '',0 if self.ready else 1)
        if args[0]=='stop':
            for row in self.resources.values():
                if row['Id']==args[-1]:row['State']='exited'
            return result(args[-1])
        if args[0]=='rm' or args[:2]==['network','rm']:
            self.resources={n:r for n,r in self.resources.items() if r['Id']!=args[-1]}
            return result(args[-1])
        raise AssertionError(args)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.run=self.root/'relay';self.run.mkdir(mode=0o700)
        self.key=self.run/'relay.key';self.key.write_text(SECRET+'\n');self.key.chmod(0o600)
        self.mod=importlib.import_module('hostd_relay');self.docker=Docker()
    def prepare(self,**kwargs):
        args=dict(run_dir=self.run,run_id=RUN,images=IMAGES,backend_port=32101,health_port=32102,
                  public_url='wss://127.0.0.1:32103',owner_pubkey=OWNER,relay_key_file=self.key)
        args.update(kwargs);return self.mod.RelayPlan.prepare(**args)
    def launcher(self,**kwargs):return self.mod.Launcher(self.prepare(),runner=self.docker,port_available=lambda p:True,sleep=lambda seconds:None,**kwargs)
    def mutations(self):return [c for c,e,t in self.docker.calls if c[1:3]==['network','create'] or c[1] in ('run','stop','rm') or c[1:3]==['network','rm']]

    def test_prepare_check_is_reversible_no_docker_mutation_or_runtime_files(self):
        plan=self.prepare();self.assertEqual(set(self.run.iterdir()),{self.key})
        check=self.mod.Launcher(plan,runner=self.docker,port_available=lambda p:True).check()
        self.assertEqual(check['status'],'prepared');self.assertFalse(self.mutations())
        self.assertEqual(set(self.run.iterdir()),{self.key});self.assertNotIn('live_ready',check)
    def test_start_exact_three_cached_containers_and_front_url_no_secret_argv(self):
        launcher=self.launcher();receipt=launcher.start()
        self.assertEqual(receipt['status'],'started');self.assertEqual(set(receipt['containers']),{'postgres','redis','relay'})
        runs=[(c,e,t) for c,e,t in self.docker.calls if c[1]=='run'];self.assertEqual(len(runs),3)
        relay=next((c,e,t) for c,e,t in runs if c[-1]==IMAGES['relay'])
        self.assertIn('RELAY_URL=wss://127.0.0.1:32103',relay[0]);self.assertIn('127.0.0.1:32101:3000',relay[0])
        self.assertIn('127.0.0.1:32102:8080',relay[0]);self.assertIn('BUZZ_RELAY_PRIVATE_KEY',relay[0])
        self.assertEqual(relay[1]['BUZZ_RELAY_PRIVATE_KEY'],SECRET)
        for cmd,env,timeout in self.docker.calls:
            self.assertNotIn(SECRET,' '.join(cmd));self.assertNotIn('pull',cmd);self.assertNotIn('compose',cmd)
            self.assertNotIn('DOCKER_HOST',env);self.assertNotIn('DOCKER_CONTEXT',env);self.assertNotIn('HOME',env)
            self.assertTrue(Path(env['DOCKER_CONFIG']).is_relative_to(self.run));self.assertLessEqual(timeout,120)
            if 'inspect' in cmd:
                self.assertIn('--format',cmd);form=cmd[cmd.index('--format')+1]
                self.assertNotEqual(form,'{{json .}}');self.assertNotIn('.Env',form)
        self.assertNotIn(SECRET,json.dumps(receipt));self.assertEqual((self.run/'receipt.json').stat().st_mode&0o777,0o600)
    def test_invalid_scope_urls_ports_images_or_path_injection_fail_before_runner(self):
        for args in [{'run_id':'bad/name'},{'public_url':'ws://127.0.0.1:32103'},{'public_url':'wss://example.com:32103'},
                     {'public_url':'wss://127.0.0.1:32103/path'},{'backend_port':32103},{'health_port':32101},
                     {'backend_port':True},{'owner_pubkey':'wrong'},{'images':dict(IMAGES,relay='ghcr.io/block/buzz:latest')}]:
            with self.subTest(args=args),self.assertRaises(ValueError):self.prepare(**args)
        self.assertFalse(self.docker.calls)
    def test_paths_symlinks_permissions_and_nonfresh_receipt_rejected(self):
        linked=self.root/'link';linked.symlink_to(self.run,target_is_directory=True)
        with self.assertRaises(ValueError):self.prepare(run_dir=linked)
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):self.prepare()
        self.key.chmod(0o600);self.run.chmod(0o755)
        with self.assertRaises(ValueError):self.prepare()
        self.run.chmod(0o700);(self.run/'receipt.json').write_text('{}')
        with self.assertRaises(ValueError):self.prepare()
    def test_missing_cache_name_collision_and_busy_port_never_create_resources(self):
        for kind in ('image','name','port','docker'):
            with self.subTest(kind=kind):
                docker=Docker();plan=self.prepare()
                if kind=='image':docker.images.pop(IMAGES['relay'])
                if kind=='name':docker.resources[plan.names['relay']]={'Id':'f'*64,'kind':'container'}
                if kind=='docker':docker.fail=['container','ls']
                launcher=self.mod.Launcher(plan,runner=docker,port_available=lambda p:kind!='port')
                with self.assertRaises(ValueError):launcher.start()
                self.assertFalse(any(c[1]=='run' or c[1:3]==['network','create'] for c,e,t in docker.calls))
    def test_stop_only_saved_ids_exact_labels_names_images_and_repeated_stop_safe(self):
        launcher=self.launcher();receipt=launcher.start();ids={r['id'] for r in receipt['containers'].values()}|{receipt['network']['id']}
        stopped=launcher.stop();self.assertEqual(stopped['status'],'stopped');self.assertFalse(self.docker.resources)
        calls=[c for c,e,t in self.docker.calls if c[1] in ('stop','rm') or c[1:3]==['network','rm']]
        self.assertTrue(calls);self.assertTrue(all(c[-1] in ids for c in calls));self.assertTrue(all('-f' not in c and 'prune' not in c for c in calls))
        count=len(calls);launcher.stop();self.assertEqual(len([c for c,e,t in self.docker.calls if c[1] in ('stop','rm') or c[1:3]==['network','rm']]),count)
    def test_cleanup_identity_or_label_replacement_blocks_before_any_stop(self):
        for field,value in [('Id','f'*64),('Labels',{}),('Name','/unrelated'),('Image','sha256:'+'f'*64)]:
            with self.subTest(field=field):
                folder=self.root/field;folder.mkdir(mode=0o700);key=folder/'relay.key';key.write_text(SECRET);key.chmod(0o600)
                plan=self.prepare(run_dir=folder,relay_key_file=key);docker=Docker();launcher=self.mod.Launcher(plan,runner=docker,port_available=lambda p:True)
                launcher.start();docker.resources[plan.names['relay']][field]=value
                before=len(docker.calls)
                with self.assertRaises(ValueError):launcher.stop()
                self.assertFalse(any(c[1] in ('stop','rm') or c[1:3]==['network','rm'] for c,e,t in docker.calls[before:]))
    def test_failed_start_retains_partial_private_receipt_and_safe_explicit_stop(self):
        launcher=self.launcher();self.docker.fail=['run']
        with self.assertRaises(ValueError) as error:launcher.start()
        self.assertIn('怎么解决',str(error.exception));self.assertNotIn('SECRET',str(error.exception))
        saved=json.loads((self.run/'receipt.json').read_text());self.assertEqual(saved['status'],'failed')
        self.assertIn('network',saved);self.assertNotIn(SECRET,json.dumps(saved));self.assertFalse(any(c[1:3]==['network','rm'] for c,e,t in self.docker.calls))
        self.docker.fail=None;self.assertEqual(launcher.stop()['status'],'stopped')
    def test_unready_postgres_blocks_relay_without_pretend_readiness(self):
        launcher=self.launcher();self.docker.ready=False
        with self.assertRaises(ValueError):launcher.start()
        self.assertFalse(any(c[1]=='run' and c[-1]==IMAGES['relay'] for c,e,t in self.docker.calls))
        self.assertEqual(json.loads((self.run/'receipt.json').read_text())['status'],'failed')
    def test_image_oci_labels_do_not_replace_exact_run_labels(self):
        class Labelled(Docker):
            def __call__(self,cmd,**kwargs):
                response=super().__call__(cmd,**kwargs)
                if cmd[1]=='run':
                    self.resources[cmd[cmd.index('--name')+1]]['Labels']['org.opencontainers.image.title']='fixture'
                return response
        docker=Labelled();launcher=self.mod.Launcher(self.prepare(),runner=docker,port_available=lambda p:True)
        self.assertEqual(launcher.start()['status'],'started');self.assertEqual(launcher.stop()['status'],'stopped')
    def test_unknown_receipt_status_blocks_all_cleanup(self):
        launcher=self.launcher();launcher.start();path=self.run/'receipt.json'
        saved=json.loads(path.read_text());saved['status']='unrecognized';path.write_text(json.dumps(saved))
        before=len(self.docker.calls)
        with self.assertRaises(ValueError):launcher.stop()
        self.assertFalse(any(c[1] in ('stop','rm') or c[1:3]==['network','rm'] for c,e,t in self.docker.calls[before:]))
    def test_stopped_receipt_cannot_hide_replacement_resource_on_second_stop(self):
        launcher=self.launcher();receipt=launcher.start();row=dict(self.docker.resources[launcher.plan.names['relay']])
        launcher.stop();row['Id']='f'*64;self.docker.resources[launcher.plan.names['relay']]=row
        with self.assertRaises(ValueError):launcher.stop()
    def test_renamed_saved_id_cannot_be_claimed_absent_or_cleaned(self):
        launcher=self.launcher();launcher.start();name=launcher.plan.names['relay']
        row=self.docker.resources.pop(name);row['Name']='/renamed';self.docker.resources['renamed']=row
        before=len(self.docker.calls)
        with self.assertRaises(ValueError):launcher.stop()
        self.assertFalse(any(c[1] in ('stop','rm') for c,e,t in self.docker.calls[before:]))
    def test_replaced_run_directory_blocks_before_any_cleanup(self):
        launcher=self.launcher();launcher.start();receipt=(self.run/'receipt.json').read_bytes()
        self.run.rename(self.root/'old-run');self.run.mkdir(mode=0o700)
        path=self.run/'receipt.json';path.write_bytes(receipt);path.chmod(0o600)
        before=len(self.docker.calls)
        with self.assertRaises(ValueError):launcher.stop()
        self.assertFalse(any(c[1] in ('stop','rm') for c,e,t in self.docker.calls[before:]))
    def test_stop_ack_without_stopped_state_never_attempts_remove(self):
        class IgnoredStop(Docker):
            def __call__(self,cmd,**kwargs):
                if cmd[1]=='stop':
                    self.calls.append((cmd,kwargs['env'],kwargs['timeout']))
                    return subprocess.CompletedProcess(cmd,0,cmd[-1],'')
                return super().__call__(cmd,**kwargs)
        docker=IgnoredStop();launcher=self.mod.Launcher(self.prepare(),runner=docker,port_available=lambda p:True)
        launcher.start()
        with self.assertRaises(ValueError):launcher.stop()
        self.assertFalse(any(c[1]=='rm' for c,e,t in docker.calls))
    def test_missing_created_resource_in_receipt_blocks_before_all_cleanup(self):
        launcher=self.launcher();launcher.start();path=self.run/'receipt.json'
        saved=json.loads(path.read_text());saved.pop('network');path.write_text(json.dumps(saved))
        before=len(self.docker.calls)
        with self.assertRaises(ValueError):launcher.stop()
        self.assertFalse(any(c[1] in ('stop','rm') for c,e,t in self.docker.calls[before:]))
    def test_exited_created_container_identity_is_retained_for_explicit_stop(self):
        class Exited(Docker):
            def __call__(self,cmd,**kwargs):
                response=super().__call__(cmd,**kwargs)
                if cmd[1]=='run' and cmd[-1]==IMAGES['relay']:
                    self.resources[cmd[cmd.index('--name')+1]]['State']='exited'
                return response
        docker=Exited();launcher=self.mod.Launcher(self.prepare(),runner=docker,port_available=lambda p:True)
        with self.assertRaises(ValueError):launcher.start()
        self.assertIn('relay',json.loads((self.run/'receipt.json').read_text())['containers'])
        self.assertEqual(launcher.stop()['status'],'stopped')
        self.assertFalse(docker.resources)
    def test_fresh_directory_race_has_fixed_notice_and_no_resource_creation(self):
        folder=self.run
        class Collision(Docker):
            def __call__(self,cmd,**kwargs):
                response=super().__call__(cmd,**kwargs)
                if cmd[1:3]==['network','ls']:
                    (folder/'docker-config').mkdir(mode=0o700)
                return response
        docker=Collision();launcher=self.mod.Launcher(self.prepare(),runner=docker,port_available=lambda p:True)
        with self.assertRaises(ValueError) as error:launcher.start()
        self.assertIn('怎么解决',str(error.exception))
        self.assertFalse(any(c[1]=='run' or c[1:3]==['network','create'] for c,e,t in docker.calls))


if __name__=='__main__':unittest.main()
