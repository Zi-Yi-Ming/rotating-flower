"""Windows URI/config migration checks alongside main's DSH transaction tests."""
import json
from pathlib import Path, PureWindowsPath, PurePosixPath
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import dsh_setup as dsh


class DshWindowsSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='flower DSH 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / '.dsh'
        self.support = self.root / 'support/dsh'
        self.profile = self.home / 'profiles/desktop/cordis.patch.yml'
        self.profile.parent.mkdir(parents=True)
        self.original = '\ufeff# 用户设置\r\n[]\r\n'.encode('utf-8')
        self.profile.write_bytes(self.original)
        self.source = self.root / 'plugin 中文 #100%.mjs'
        self.source.write_text('export default "fixture loaded";', encoding='utf-8')

    def change(self, source=True):
        return dsh.DshPluginChange(self.home, self.support, self.source if source else None)

    def test_file_uris_escape_unicode_spaces_and_reserved_characters(self):
        win = PureWindowsPath(r'C:\flower 用户\#100%\observer.mjs')
        self.assertEqual(dsh.plugin_file_uri(win),
            'file:///C:/flower%20%E7%94%A8%E6%88%B7/%23100%25/observer.mjs')
        posix = PurePosixPath('/home/flower 用户/#100%/observer.mjs')
        self.assertEqual(dsh.plugin_file_uri(posix),
            'file:///home/flower%20%E7%94%A8%E6%88%B7/%23100%25/observer.mjs')

    def test_bom_crlf_install_reinstall_and_uninstall_preserve_exact_user_bytes(self):
        self.change().apply()
        installed = self.profile.read_bytes()
        self.assertTrue(installed.startswith('\ufeff# 用户设置\r\n'.encode()))
        self.assertNotIn(b'\n', installed.replace(b'\r\n', b''))
        self.assertIn(b'name: "file:///', installed)
        self.change().apply()
        self.assertEqual(self.profile.read_bytes(), installed)
        self.change(False).apply()
        self.assertEqual(self.profile.read_bytes(), self.original)

    def test_legacy_preview_upgrade_and_rollback_preserve_old_plugin_and_patch(self):
        self.support.mkdir(parents=True)
        observer = self.support / dsh.OBSERVER_NAME
        observer.write_bytes(b'old preview plugin')
        legacy_block = '\r\n'.join([dsh.BEGIN, '- insert:',
            '    - id: rotating-flower-observer', '      name: ' + json.dumps(str(observer)), dsh.END])
        legacy = ('\ufeff# 用户设置\r\n' + legacy_block + '\r\n').encode()
        self.profile.write_bytes(legacy)
        change = self.change().apply()
        self.assertIn(b'name: "file:///', self.profile.read_bytes())
        self.assertEqual(observer.read_bytes(), self.source.read_bytes())
        change.rollback()
        self.assertEqual(self.profile.read_bytes(), legacy)
        self.assertEqual(observer.read_bytes(), b'old preview plugin')

    def test_legacy_preview_upgrade_then_uninstall_keeps_bom_crlf_and_user_entries(self):
        user = '\ufeff# 用户配置\r\n- id: mine\r\n'
        block = dsh.managed_block(PureWindowsPath(r'C:\old\flower-observer.mjs')).replace('\n','\r\n')
        self.profile.write_bytes((user + block + '\r\n').encode())
        self.change().apply()
        self.change(False).apply()
        self.assertEqual(self.profile.read_bytes(), user.encode())

    @unittest.skipUnless(shutil.which('node'), 'Node required for actual ESM import')
    def test_installed_uri_loads_actual_module_with_node(self):
        self.change().apply()
        value = next(line.split('name: ', 1)[1] for line in
                     self.profile.read_text(encoding='utf-8').splitlines() if 'name: ' in line)
        child = subprocess.run([shutil.which('node'), '--input-type=module', '-e',
            'const m=await import(process.argv[1]); console.log(JSON.stringify(m.default));',
            json.loads(value)], capture_output=True, check=True, timeout=15)
        self.assertEqual(json.loads(child.stdout), 'fixture loaded')

    def test_windows_support_directory_uses_local_app_data(self):
        with patch.object(dsh.sys, 'platform', 'win32'), patch.dict(dsh.os.environ, {'LOCALAPPDATA':str(self.root)}):
            self.assertEqual(dsh.support_root(), self.root / 'RotatingFlower/dsh')

    def test_duplicate_or_reversed_bom_markers_are_refused_without_writing(self):
        for text in ['\ufeff'+dsh.BEGIN+'\r\n'+dsh.BEGIN+'\r\n'+dsh.END,
                     '\ufeff'+dsh.END+'\r\n'+dsh.BEGIN, '\ufeff'+dsh.BEGIN]:
            original=text.encode();self.profile.write_bytes(original)
            with self.assertRaises(dsh.SetupError):self.change().apply()
            self.assertEqual(self.profile.read_bytes(),original)

    def test_user_edits_after_install_survive_removal(self):
        self.change().apply()
        with self.profile.open('ab') as handle:handle.write('- id: later-user-entry\r\n'.encode())
        self.change(False).apply()
        remaining=self.profile.read_bytes()
        self.assertTrue(remaining.startswith('\ufeff# 用户设置\r\n'.encode()))
        self.assertIn(b'- id: later-user-entry\r\n',remaining)
        self.assertNotIn(dsh.PLUGIN_ID.encode(),remaining)


if __name__ == '__main__':
    unittest.main()
