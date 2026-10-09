import importlib.util
import os
from pathlib import Path
import subprocess
import hashlib
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/scan-payload.py'
spec = importlib.util.spec_from_file_location('scan_payload', SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)
CONFIG = "import { Posts } from './collections/Posts'\n  collections: [Posts\n  ],\n"


class PayloadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'repo'
        (self.root / 'src/collections').mkdir(parents=True)
        self.config = self.root / 'src/payload.config.ts'
        self.config.write_text(CONFIG)
        (self.root / 'src/collections/Posts.ts').write_text("  slug: 'posts',\n")

    def inspect(self):
        with patch.object(scanner.subprocess, 'check_output', return_value='fixture-sha\n'):
            return scanner.inspect(self.root)

    def test_normal_fixture(self):
        result = self.inspect()
        self.assertEqual(result['collections'][0]['slug'], 'posts')
        self.assertEqual(set(result['sourceFiles']), {'src/payload.config.ts', 'src/collections/Posts.ts'})

    def test_config_symlink_rejected_before_read(self):
        outside = self.base / 'outside.ts'
        outside.write_text(CONFIG)
        self.config.unlink()
        self.config.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'source|symlink'):
            self.inspect()

    def test_config_parent_symlink_rejected(self):
        src = self.root / 'src'
        src.rename(self.base / 'outside-src')
        src.symlink_to(self.base / 'outside-src', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'source|symlink'):
            self.inspect()

    def test_config_fifo_does_not_block(self):
        self.config.unlink()
        os.mkfifo(self.config)
        try:
            result = subprocess.run([sys.executable, str(SCRIPT), '--source-root', str(self.root),
                                     '--output', str(self.base / 'result.json')],
                                    capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail('scanner blocked while reading a FIFO')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source', result.stderr)

    def test_config_directory_rejected(self):
        self.config.unlink()
        self.config.mkdir()
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_optional_fifo_rejected(self):
        path = self.root / 'src/lib/feishuAuthStrategy.ts'
        path.parent.mkdir()
        os.mkfifo(path)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_collection_directory_symlink_rejected(self):
        directory = self.root / 'src/collections/Posts'
        (self.root / 'src/collections/Posts.ts').unlink()
        outside = self.base / 'outside-collection'
        outside.mkdir()
        (outside / 'index.ts').write_text("  slug: 'posts',\n")
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_collection_fifo_rejected(self):
        file = self.root / 'src/collections/Posts.ts'
        file.unlink()
        os.mkfifo(file)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_endpoint_parent_symlink_rejected(self):
        outside = self.base / 'outside-endpoints'
        outside.mkdir()
        (outside / 'one.ts').write_text("path: '/one', method: 'get'")
        (self.root / 'src/endpoints').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_endpoint_fifo_does_not_block(self):
        directory = self.root / 'src/endpoints'
        directory.mkdir()
        os.mkfifo(directory / 'one.ts')
        try:
            result = subprocess.run([sys.executable, str(SCRIPT), '--source-root', str(self.root),
                                     '--output', str(self.base / 'result.json')],
                                    capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            self.fail('scanner blocked while reading endpoint FIFO')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source', result.stderr)

    def test_config_socket_rejected(self):
        self.config.unlink()
        connection = socket.socket(socket.AF_UNIX)
        self.addCleanup(connection.close)
        connection.bind(str(self.config))
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_internal_config_symlink_rejected(self):
        target = self.root / 'src/other.ts'
        self.config.rename(target)
        self.config.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_optional_parent_symlink_rejected(self):
        outside = self.base / 'outside-lib'
        outside.mkdir()
        (outside / 'feishuAuthStrategy.ts').write_text('auth evidence')
        (self.root / 'src/lib').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_endpoint_leaf_symlink_rejected(self):
        (self.root / 'src/endpoints').mkdir()
        outside = self.base / 'outside.ts'
        outside.write_text("path: '/one', method: 'get'")
        (self.root / 'src/endpoints/one.ts').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'source'):
            self.inspect()

    def test_regular_config_replaced_by_symlink_before_open_rejected(self):
        outside = self.base / 'outside.ts'
        outside.write_text(CONFIG)
        original_open = scanner.os.open

        def replace_then_open(path, flags, **kwargs):
            if path == 'payload.config.ts':
                self.config.unlink()
                self.config.symlink_to(outside)
            return original_open(path, flags, **kwargs)

        with patch.object(scanner.os, 'open', side_effect=replace_then_open):
            with self.assertRaisesRegex(ValueError, 'source'):
                self.inspect()

    def test_read_rejects_outside_and_parent_traversal(self):
        reader = scanner.SourceReader(self.root)
        self.addCleanup(reader.close)
        for path in [self.base / 'outside.ts', self.root / 'src/../../outside.ts']:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'source'):
                reader.read(path)

    def test_directory_collection_endpoint_and_hashes(self):
        file = self.root / 'src/collections/Posts.ts'
        file.unlink()
        file = self.root / 'src/collections/Posts/index.ts'
        file.parent.mkdir()
        file.write_text("  slug: 'posts',\n")
        (self.root / 'src/endpoints').mkdir()
        endpoint = self.root / 'src/endpoints/one.ts'
        endpoint.write_text("path: '/one', method: 'get'")
        auth = self.root / 'src/lib/feishuAuthStrategy.ts'
        auth.parent.mkdir()
        auth.write_text('auth evidence')
        result = self.inspect()
        collection = result['collections'][0]
        self.assertNotIn('actions', collection)
        self.assertEqual(collection['actionCandidates'], ['list', 'get', 'create', 'update', 'delete'])
        self.assertEqual(collection['runtimeVerification'], 'unknown')
        self.assertEqual(collection['permissionVerification'], 'unknown')
        self.assertEqual(result['endpoints'][0]['path'], '/api/one')
        for path in [self.config, file, endpoint, auth]:
            self.assertEqual(result['sourceFiles'][str(path.relative_to(self.root))],
                             hashlib.sha256(path.read_bytes()).hexdigest())

    def test_explicit_root_alias_uses_canonical_boundary(self):
        alias = self.base / 'selected-root'
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(scanner.subprocess, 'check_output', return_value='fixture-sha\n'):
            result = scanner.inspect(alias)
        self.assertEqual(result['collections'][0]['slug'], 'posts')

    def test_hashes_use_inspected_bytes_without_reopening(self):
        expected = hashlib.sha256(CONFIG.encode()).hexdigest()

        def change_source_after_inspection(*args, **kwargs):
            self.config.write_text('changed after inspection')
            return 'fixture-sha\n'

        with patch.object(scanner.subprocess, 'check_output', side_effect=change_source_after_inspection):
            result = scanner.inspect(self.root)
        self.assertEqual(result['sourceFiles']['src/payload.config.ts'], expected)


if __name__ == '__main__':
    unittest.main()
