"""The default console lives in the verified per-user runtime directory."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import test_hostd_wiring_lifecycle as fixture


class RuntimeDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_owned_private_xdg_directory_is_used(self):
        self.assertTrue(hasattr(fixture.hd,'default_console_dir'))
        with mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.root)}):
            self.assertEqual(fixture.hd.default_console_dir(),self.root/'buzz-hostd')
        self.assertFalse((self.root/'buzz-hostd').exists())

    def test_missing_unsafe_or_shared_xdg_is_refused_with_actionable_notice(self):
        self.assertTrue(hasattr(fixture.hd,'default_console_dir'))
        link=self.root/'link';link.symlink_to(self.root,target_is_directory=True)
        for value in ['', 'relative', str(link), str(self.root/'..'/'runtime'),str(self.root/'missing')]:
            with self.subTest(value=value),mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':value}):
                with self.assertRaises(ValueError) as caught:fixture.hd.default_console_dir()
                self.assertIn('怎么解决',str(caught.exception));self.assertIn('复制给 AI',str(caught.exception))
        self.root.chmod(0o755)
        with mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':str(self.root)}):
            with self.assertRaises(ValueError):fixture.hd.default_console_dir()

    def test_explicit_isolated_console_override_does_not_require_global_xdg(self):
        captured={}
        def host(reg,status,**kwargs):captured.update(kwargs);return object()
        async def stopped(h):pass
        with mock.patch.dict(os.environ,{'XDG_RUNTIME_DIR':''}), \
                mock.patch.object(fixture.hd,'STATE_DIR',self.root/'state'), \
                mock.patch.object(fixture.hd.registry,'load',return_value=fixture.registry.Registry()), \
                mock.patch.object(fixture.hd,'Hostd',host), \
                mock.patch.object(fixture.hd,'run_until_stopped',stopped), \
                mock.patch('sys.argv',['hostd','run','--console-dir',str(self.root/'isolated')]):
            self.assertEqual(fixture.hd.main(),0)
        self.assertEqual(captured['console_dir'],self.root/'isolated')


if __name__=='__main__':unittest.main()
