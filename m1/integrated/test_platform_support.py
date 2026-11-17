import ctypes
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import platform_support as platform


class PlatformTests(unittest.TestCase):
    def test_windows_support_path_and_lock_are_per_user(self):
        with patch.object(platform, 'is_windows', return_value=True), \
                patch.dict(os.environ, {'LOCALAPPDATA': '/test/local'}):
            self.assertEqual(platform.support_dir(), Path('/test/local/RotatingFlower'))
            self.assertEqual(platform.bridge_lock_path(), Path('/test/local/RotatingFlower/bridge.lock'))

    @unittest.skipIf(sys.platform == 'win32', 'Mac/POSIX compatibility')
    def test_posix_keeps_historical_lock_location(self):
        self.assertEqual(platform.bridge_lock_path(),
                         Path(tempfile.gettempdir()) / f'codex-motor-bridge-{os.getuid()}.lock')

    def test_windows_alive_uses_read_only_handle_and_closes_it(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0x100000001

        def read_exit(handle, pointer):
            pointer._obj.value = 259
            return 1

        kernel.GetExitCodeProcess.side_effect = read_exit
        with patch.object(platform, 'is_windows', return_value=True), \
                patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch('platform_support.os.kill') as kill:
            self.assertTrue(platform.process_alive(1234))
        kill.assert_not_called()
        kernel.OpenProcess.assert_called_once_with(0x1000, False, 1234)
        kernel.CloseHandle.assert_called_once_with(0x100000001)

    def test_windows_exited_and_missing_processes_are_not_alive(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 12

        def exited(handle, pointer):
            pointer._obj.value = 0
            return 1

        kernel.GetExitCodeProcess.side_effect = exited
        with patch.object(platform, 'is_windows', return_value=True), \
                patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch.object(ctypes, 'get_last_error', return_value=87, create=True):
            self.assertFalse(platform.process_alive(1234))
            kernel.OpenProcess.return_value = None
            self.assertFalse(platform.process_alive(1234))
        kernel.CloseHandle.assert_called_once_with(12)

    def test_windows_permission_failure_is_not_liveness(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = None
        with patch.object(platform, 'is_windows', return_value=True), \
                patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch.object(ctypes, 'get_last_error', return_value=5, create=True):
            with self.assertRaises(PermissionError):
                platform.process_alive(1234)
        kernel.CloseHandle.assert_not_called()

    def test_windows_query_failure_closes_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 12
        kernel.GetExitCodeProcess.return_value = 0
        with patch.object(platform, 'is_windows', return_value=True), \
                patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch.object(ctypes, 'get_last_error', return_value=6, create=True):
            with self.assertRaises(OSError):
                platform.process_alive(1234)
        kernel.CloseHandle.assert_called_once_with(12)

    def test_invalid_pids_never_probe(self):
        with patch('platform_support.os.kill') as kill:
            for pid in [0, -1, True, '12', None]:
                self.assertFalse(platform.process_alive(pid))
        kill.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'Native Win32 process handles')
    def test_native_process_probe_does_not_terminate_and_detects_exit(self):
        self.assertTrue(platform.process_alive(os.getpid()))
        child = subprocess.Popen([sys.executable, '-c',
                                  'import time; print("ready", flush=True); time.sleep(1)'],
                                 stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.assertTrue(platform.process_alive(child.pid))
            self.assertIsNone(child.poll(), 'A liveness probe must not terminate its target')
            self.assertEqual(child.wait(timeout=10), 0)
            self.assertFalse(platform.process_alive(child.pid))
        finally:
            child.wait(timeout=10)
            child.stdout.close()

    def test_lock_excludes_second_process_and_releases(self):
        # This runs the actual host lock implementation; Windows CI exercises
        # msvcrt, and macOS CI exercises flock without locking a real service.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.lock'
            script = ('from pathlib import Path; from platform_support import acquire_lock; '
                      'import sys;\ntry:\n lock=acquire_lock(Path(sys.argv[1]))\n'
                      'except BlockingIOError:\n sys.exit(23)\n'
                      'else:\n lock.close()\n')
            lock = platform.acquire_lock(path)
            try:
                result = subprocess.run([sys.executable, '-c', script, str(path)],
                                        cwd=Path(__file__).parent, timeout=10, capture_output=True)
                self.assertEqual(result.returncode, 23, result.stderr)
            finally:
                lock.close()
            with platform.acquire_lock(path):
                pass

    def test_windows_lock_is_nonblocking_and_uses_same_byte_when_released(self):
        calls = []

        def locking(fd, mode, size):
            calls.append((os.lseek(fd, 0, os.SEEK_CUR), mode, size))

        fake = SimpleNamespace(locking=locking, LK_NBLCK=2, LK_UNLCK=0)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(platform, 'is_windows', return_value=True), \
                patch.dict(sys.modules, {'msvcrt': fake}):
            lock = platform.acquire_lock(Path(directory) / 'test.lock')
            lock.close()
            lock.close()
        self.assertEqual(calls, [(0, 2, 1), (0, 0, 1)])

    def test_windows_lock_contention_reports_blocking_error(self):
        fake = SimpleNamespace(locking=Mock(side_effect=OSError(errno.EACCES, 'locked')),
                               LK_NBLCK=2)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(platform, 'is_windows', return_value=True), \
                patch.dict(sys.modules, {'msvcrt': fake}):
            with self.assertRaises(BlockingIOError):
                platform.acquire_lock(Path(directory) / 'test.lock')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows share-delete semantics')
    def test_shared_reader_allows_writer_rename_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / '任务.jsonl'
            renamed = Path(directory) / 'old.jsonl'
            original.write_bytes(b'one\n')
            with platform.open_read_shared(original) as reader:
                with original.open('ab') as writer:
                    writer.write(b'two\n')
                original.rename(renamed)
                renamed.unlink()
                self.assertEqual(reader.read(), b'one\ntwo\n')


if __name__ == '__main__':
    unittest.main()
