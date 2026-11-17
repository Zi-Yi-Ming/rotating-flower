"""Native checks for finding an existing runtime without a global Python install."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell required')
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'flower space 中文'
        self.root.mkdir()
        self.finder = Path(__file__).parent / 'windows/find-python.ps1'

    def locate(self, *, env=None, explicit_root=True):
        command = ['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                   '-File', str(self.finder)]
        if explicit_root:
            command += ['-SupportRoot', str(self.root)]
        result = subprocess.run(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=15)
        # The script's pipe encoding is UTF-8, matching launch.cmd's code page.
        return result.stdout.decode('utf-8').strip()

    def configure(self, runtime):
        (self.root / 'config.json').write_text(
            json.dumps({'schema': 1, 'runtime': str(runtime)}, ensure_ascii=False),
            encoding='utf-8')

    def test_locates_installed_interpreter_with_unicode_and_spaces(self):
        runtime = self.root / 'runtimes' / 'release 版本'
        interpreter = runtime / 'venv/Scripts/python.exe'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b'fixture; never executed by finder')
        self.configure(runtime)
        self.assertTrue(interpreter.samefile(self.locate()))

    def test_missing_and_invalid_config_return_no_candidate(self):
        self.assertEqual(self.locate(), '')
        (self.root / 'config.json').write_text('{broken', encoding='utf-8')
        self.assertEqual(self.locate(), '')

    def test_external_runtime_is_not_selected(self):
        external = Path(self.temp.name) / 'external'
        interpreter = external / 'venv/Scripts/python.exe'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b'fixture')
        self.configure(external)
        self.assertEqual(self.locate(), '')

    def test_missing_interpreter_returns_no_candidate(self):
        self.configure(self.root / 'runtimes' / 'missing')
        self.assertEqual(self.locate(), '')

    def test_default_root_uses_redirected_localappdata(self):
        local = Path(self.temp.name) / 'redirected Local 中文 !literal!'
        self.root = local / 'RotatingFlower'
        self.root.mkdir(parents=True)
        runtime = self.root / 'runtimes' / 'release'
        interpreter = runtime / 'venv/Scripts/python.exe'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b'fixture; finder does not execute it')
        self.configure(runtime)
        env = dict(os.environ, LOCALAPPDATA=str(local))
        self.assertTrue(interpreter.samefile(self.locate(env=env, explicit_root=False)))

    def test_launcher_reuses_real_venv_with_unicode_spaces_and_exclamation_marks(self):
        local = Path(self.temp.name) / 'Local 用户 !literal!'
        self.root = local / 'RotatingFlower'
        self.root.mkdir(parents=True)
        runtime = self.root / 'runtimes' / 'installed 版本 !runtime!'
        venv = runtime / 'venv'
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60)
        interpreter = venv / 'Scripts/python.exe'
        self.configure(runtime)

        package = Path(self.temp.name) / 'unpacked 软件 !package!'
        (package / 'app').mkdir(parents=True)
        scripts = Path(__file__).parent / 'windows'
        for name in ('launch.cmd', 'find-python.ps1'):
            shutil.copy2(scripts / name, package / name)
        fixture = package / 'app/windows_setup.py'
        fixture.write_text(
            'import json, sys\n'
            'print("FLOWER_LAUNCH_TEST=" + json.dumps({"executable": sys.executable, '
            '"script": sys.argv[0], "arguments": sys.argv[1:]}, ensure_ascii=False))\n',
            encoding='utf-8')

        windows = Path(os.environ['SystemRoot'])
        cmd = windows / 'System32/cmd.exe'
        env = dict(os.environ)
        for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
            env.pop(name, None)
        env['LOCALAPPDATA'] = str(local)
        env['PATH'] = os.pathsep.join(str(path) for path in (
            windows / 'System32', windows,
            windows / 'System32/WindowsPowerShell/v1.0'))
        arguments = ['install', '--source', 'workbuddy,dsh', '--dry-run',
                     '--probe-path', str(Path(self.temp.name) / '任务 文件')]
        # Invoke the relative name from the package directory. This lets the script
        # disable inherited delayed expansion before its own !-containing path is
        # expanded; quoting a literal ! path in an already-/v:on caller cannot do so.
        command = subprocess.list2cmdline(['launch.cmd', *arguments])
        command_line = subprocess.list2cmdline([str(cmd), '/d', '/v:on', '/s', '/c']) + ' "' + command + '"'
        result = subprocess.run(command_line, executable=str(cmd), cwd=package, env=env,
                                input=b'\r\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30)
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        self.assertEqual(result.returncode, 0, stdout + '\n' + stderr)
        payloads = [line.removeprefix('FLOWER_LAUNCH_TEST=') for line in stdout.splitlines()
                    if line.startswith('FLOWER_LAUNCH_TEST=')]
        self.assertEqual(len(payloads), 1, stdout + '\n' + stderr)
        payload = json.loads(payloads[0])
        self.assertTrue(interpreter.samefile(payload['executable']))
        self.assertTrue(fixture.samefile(payload['script']))
        self.assertEqual(payload['arguments'], arguments)


if __name__ == '__main__':
    unittest.main()
