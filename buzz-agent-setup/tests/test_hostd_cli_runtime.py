"""Configurable product CLI paths; subprocess fixtures never contact Feishu."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd.bot_clients import BotLarkCli

class ProductRuntime(unittest.TestCase):
    def setUp(self):
        # A private real owner home avoids trusting the world-writable /tmp
        # ancestor. These are per-test files, never a real installed profile.
        self.temp=tempfile.TemporaryDirectory(prefix='hostd-cli-offline-',dir=Path.home());self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'other-home';self.home.mkdir(mode=0o700)
        self.config=self.home/'profile';self.config.mkdir(mode=0o700);self.data=self.home/'data';self.data.mkdir(mode=0o700)
        path=self.config/'config.json';path.write_text(json.dumps({'apps':[{'appId':'cli_offline','name':'selected'}]}));path.chmod(0o600)
        self.node=self.home/'node';self.entry=self.home/'run.js'
        self.node.write_text('#!'+str(Path(sys.executable).resolve())+'\nimport json,os,sys\nprint(json.dumps({"ok":True,"identity":"bot","data":{"argv":sys.argv[1:],"home":os.environ.get("HOME"),"profile_dir":os.environ.get("LARKSUITE_CLI_CONFIG_DIR")}}))\n');self.node.chmod(0o700)
        self.entry.write_text('// offline CLI fixture only\n');self.entry.chmod(0o600)
        self.env={'HOME':str(self.home),'PATH':'/usr/bin','HOSTD_NODE_BINARY':str(self.node),'HOSTD_LARK_CLI_ENTRY':str(self.entry)}
    def client(self,**kwargs):return BotLarkCli('cli_offline',self.config,self.data,base_env=self.env,**kwargs)
    def test_actual_subprocess_uses_explicit_paths_nondefault_home_and_exact_bot_profile(self):
        import subprocess
        def only_offline(argv,**kwargs):
            self.assertEqual(argv[0],str(self.node),'refuse executing any installed CLI in this offline fixture')
            return subprocess.run(argv,**kwargs)
        result=self.client(runner=only_offline).call('offline runtime',['api','GET','/open-apis/im/v1/chats'])
        self.assertEqual(result['argv'][0],str(self.entry));self.assertEqual(result['home'],str(self.home))
        args=result['argv'];self.assertEqual(args[args.index('--as')+1],'bot');self.assertEqual(args[args.index('--profile')+1],'selected')
        self.assertEqual(result['profile_dir'],str(self.config))
    def test_native_http_does_not_require_any_node_or_cli_installation(self):
        self.env.update(HOSTD_NODE_BINARY='/missing/node',HOSTD_LARK_CLI_ENTRY='/missing/run.js')
        class Pool:
            def request(self,*args,**kwargs):return {'ok':True,'identity':'bot','data':{'read':True}}
        self.assertEqual(self.client(http_pool=Pool()).chat('oc_offline'),{'read':True})
    def test_unicode_owner_installation_paths_remain_valid_local_paths(self):
        from hostd.cli_runtime import RuntimePaths
        owner=self.home/'用户';owner.mkdir(mode=0o700)
        node=owner/'node';node.write_bytes(self.node.read_bytes());node.chmod(0o700)
        entry=owner/'入口.js';entry.write_bytes(self.entry.read_bytes());entry.chmod(0o600)
        paths=RuntimePaths.resolve(dict(self.env,HOSTD_NODE_BINARY=str(node),HOSTD_LARK_CLI_ENTRY=str(entry)))
        self.assertEqual(paths.validate(),RuntimePaths(str(node),str(entry)))
    def test_explicit_relative_or_missing_runtime_fails_closed_fixed_notice(self):
        from hostd.cli_runtime import RuntimePaths,RuntimePathError
        for value in ('node','../node','/missing/node'):
            with self.subTest(value=value):
                with self.assertRaises(RuntimePathError) as caught:RuntimePaths(value,str(self.entry)).validate()
                self.assertIn('怎么解决',str(caught.exception));self.assertIn('复制给 AI',str(caught.exception))
                self.assertNotIn(value,str(caught.exception))
    def test_node_nonexecutable_or_worldwritable_and_entry_otherwriter_rejected(self):
        from hostd.cli_runtime import RuntimePaths,RuntimePathError
        for path,mode in ((self.node,0o600),(self.node,0o702),(self.entry,0o602),(self.entry,0o620)):
            with self.subTest(mode=mode):
                original=stat.S_IMODE(path.stat().st_mode);path.chmod(mode)
                try:
                    with self.assertRaises(RuntimePathError):RuntimePaths(str(self.node),str(self.entry)).validate()
                finally:path.chmod(original)
    def test_leaf_and_ancestor_symlinks_or_unsafe_ancestor_rejected(self):
        from hostd.cli_runtime import RuntimePaths,RuntimePathError
        leaf=self.home/'linked-node';leaf.symlink_to(self.node)
        linked=self.root/'linked-home';linked.symlink_to(self.home,target_is_directory=True)
        for node,entry in ((leaf,self.entry),(linked/'node',linked/'run.js')):
            with self.assertRaises(RuntimePathError):RuntimePaths(str(node),str(entry)).validate()
        self.home.chmod(0o702)
        try:
            with self.assertRaises(RuntimePathError):RuntimePaths(str(self.node),str(self.entry)).validate()
        finally:self.home.chmod(0o700)
    def test_foreign_owner_rejected_while_system_root_owned_node_allowed(self):
        from hostd.cli_runtime import RuntimePaths,RuntimePathError
        actual_fstat=os.fstat
        def foreign_stat(fd):
            from types import SimpleNamespace
            value=actual_fstat(fd)
            return SimpleNamespace(st_uid=os.geteuid()+10000,st_mode=value.st_mode) if stat.S_ISREG(value.st_mode) else value
        with mock.patch('hostd.cli_runtime.os.fstat',side_effect=foreign_stat):
            with self.assertRaises(RuntimePathError):RuntimePaths(str(self.node),str(self.entry)).validate()
        system=Path(sys.executable).resolve()
        self.assertEqual(RuntimePaths(str(system),str(self.entry)).validate().node_binary,str(system))
    def test_resolver_uses_current_owner_home_prefix_without_literal_owner_or_version_pin(self):
        from hostd.cli_runtime import RuntimePaths
        installed=self.home/'.npm-global/lib/node_modules/@larksuite/cli/scripts/run.js';installed.parent.mkdir(parents=True);installed.write_text('offline fixture');installed.chmod(0o600)
        # This scenario exercises HOME defaults, independent of installed overrides.
        with mock.patch.dict(os.environ):
            for name in ('HOSTD_NODE_BINARY','HOSTD_LARK_CLI_ENTRY','NPM_CONFIG_PREFIX','npm_config_prefix'):
                os.environ.pop(name,None)
            resolved=RuntimePaths.resolve({'HOME':str(self.home),'PATH':str(self.home)})
        self.assertEqual(resolved.node_binary,str(self.node));self.assertEqual(resolved.lark_cli_entry,str(installed))
        self.assertNotIn('/home/jchen',Path(__file__).parents[1].joinpath('scripts/hostd/cli_runtime.py').read_text())
    def test_injected_offline_runtime_paths_keep_fake_transport_testable(self):
        from hostd.cli_runtime import RuntimePaths
        calls=[]
        def runner(argv,**kwargs):
            import subprocess
            calls.append(argv);return subprocess.CompletedProcess(argv,0,json.dumps({'ok':True,'identity':'bot','data':{}}),'')
        self.client(runner=runner,runtime_paths=RuntimePaths(str(self.node),str(self.entry))).chat('oc_offline')
        self.assertEqual(calls[0][:2],[str(self.node),str(self.entry)])

if __name__=='__main__':unittest.main()
