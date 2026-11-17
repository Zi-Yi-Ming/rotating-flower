import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
import package_windows as package


class WindowsPackageTests(unittest.TestCase):
    def test_export_provenance_and_no_checkout_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            commit = 'a' * 40
            with patch.object(package, '__file__', str(source / 'package_windows.py')):
                archive = package.build(root / 'out', commit)
                with zipfile.ZipFile(archive) as zipped:
                    manifest = json.loads(zipped.read(package.NAME + '/manifest.json'))
                    self.assertEqual(manifest['source_revision'], {'commit': commit, 'dirty': False})
                with self.assertRaises(ValueError):
                    package.build(root / 'out', 'not-a-commit')
                (root / '.git').mkdir()
                with self.assertRaisesRegex(ValueError, 'without .git'):
                    package.build(root / 'out', commit)

    def fixture(self, root):
        source = root / 'm1'
        (source / 'integrated').mkdir(parents=True)
        (source / 'windows').mkdir()
        (root / 'LICENSE').write_text('fixture license')
        (root / 'docs').mkdir()
        for name in ('windows.md', 'windows-contributions.md', 'qodercn.md'):
            (root / 'docs' / name).write_text('fixture documentation')
        for name in package.BRIDGE_FILES:
            (source / 'integrated' / name).write_text('# fixture\n')
        for name in package.SCRIPTS:
            (source / 'windows' / name).write_text('@echo off\n')
        (source / 'windows_setup.py').write_text('# fixture setup\n')
        (source / 'dsh_setup.py').write_text('# fixture dsh helper\n')
        (source / 'dsh').mkdir()
        (source / 'dsh/flower-observer.mjs').write_text('// fixture observer\n')
        (source / 'dsh/user-config.json').write_text('PRIVATE DSH CONFIG')
        (source / 'dsh/sessions.json').write_text('PRIVATE DSH SESSIONS')
        (source / 'windows/requirements.txt').write_text('pyserial==3.5\n')
        (source / 'integrated/attention-cache.json').write_text('PRIVATE CACHE')
        (source / 'integrated/status.json').write_text('PRIVATE STATUS')
        (source / 'integrated/test_private.py').write_text('PRIVATE TEST')
        return source

    def test_zip_allowlist_checksums_and_repeatable_output(self):
        with tempfile.TemporaryDirectory(prefix='小花 windows ') as temp:
            root = Path(temp)
            source = self.fixture(root)
            with patch.object(package, '__file__', str(source / 'package_windows.py')):
                archive = package.build(root / 'out')
                first = archive.read_bytes()
                self.assertEqual(package.build(root / 'out').read_bytes(), first)
            with zipfile.ZipFile(archive) as zipped:
                names = zipped.namelist()
                manifest = json.loads(zipped.read(package.NAME + '/manifest.json'))
                self.assertFalse(manifest['runtime_bundled'])
                self.assertFalse(manifest['windows_runtime_verified'])
                self.assertTrue(manifest['default_dry_run'])
                self.assertTrue(manifest['dsh_windows_integrated'])
                self.assertFalse(manifest['dsh_windows_runtime_verified'])
                self.assertEqual(manifest['dsh_profile'], 'desktop')
                for required in ('app/dsh_setup.py', 'app/dsh/flower-observer.mjs',
                                 'app/integrated/source_config.py', 'app/integrated/qodercn_adapter.py',
                                 'app/integrated/platform_support.py', 'find-python.ps1'):
                    self.assertIn(package.NAME + '/' + required, names)
                self.assertIn('workbuddy,dsh', manifest['source_choices'])
                self.assertIn('qodercn', manifest['supported_sources'])
                self.assertIn('codex,workbuddy,dsh,qodercn', manifest['source_choices'])
                self.assertFalse(manifest['qodercn_windows_runtime_verified'])
                for name in names:
                    self.assertNotIn(b'PRIVATE', zipped.read(name))
                for name, digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(zipped.read(package.NAME + '/' + name)).hexdigest(), digest)
                self.assertIn(b'\r\n', zipped.read(package.NAME + '/install.cmd'))
            self.assertTrue(archive.with_suffix('.zip.sha256').read_text().startswith(hashlib.sha256(first).hexdigest()))

    def test_missing_module_rejects_build_without_replacing_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            with patch.object(package, '__file__', str(source / 'package_windows.py')):
                archive = package.build(root / 'out')
                previous = archive.read_bytes()
                (source / 'integrated/windows_ipc.py').unlink()
                with self.assertRaisesRegex(ValueError, 'windows_ipc.py'):
                    package.build(root / 'out')
                self.assertEqual(archive.read_bytes(), previous)

    def test_missing_dsh_cannot_produce_an_incomplete_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            with patch.object(package, '__file__', str(source / 'package_windows.py')):
                archive = package.build(root / 'out')
                previous = archive.read_bytes()
                (source / 'dsh/flower-observer.mjs').unlink()
                with self.assertRaisesRegex(ValueError, 'flower-observer.mjs'):
                    package.build(root / 'out')
                self.assertEqual(archive.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
