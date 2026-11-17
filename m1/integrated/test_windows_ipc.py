"""Windows transport tests; no Codex app, task data, or USB is accessed."""
import json
from pathlib import Path
import queue
import socket
import struct
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from runtime_feed import DesktopRuntimeFeed, default_endpoint, open_transport
from windows_ipc import DEFAULT_PIPE, WindowsPipe, validate_pipe_path


class Operation:
    event = 42

    def __init__(self, data=b"test", count=None, error=0):
        self.data = data
        self.count = len(data) if count is None else count
        self.error = error
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def GetOverlappedResult(self, wait):
        return self.count, self.error

    def getbuffer(self):
        return self.data


def api_for(operation=None, pending=False):
    api = Mock()
    for name, value in dict(GENERIC_READ=0x80000000, GENERIC_WRITE=0x40000000,
                            OPEN_EXISTING=3, FILE_FLAG_OVERLAPPED=0x40000000,
                            ERROR_IO_PENDING=997, ERROR_BROKEN_PIPE=109,
                            WAIT_OBJECT_0=0, WAIT_TIMEOUT=258).items():
        setattr(api, name, value)
    api.CreateFile.return_value = 17
    operation = operation or Operation()
    api.ReadFile.return_value = operation, 997 if pending else 0
    api.WriteFile.return_value = operation, 997 if pending else 0
    api.WaitForSingleObject.return_value = 0
    return api


class PipeTests(unittest.TestCase):
    def test_only_local_named_pipes_are_accepted(self):
        for path in ["C:\\file.txt", "\\\\host\\pipe\\codex-ipc", "tcp://localhost", "", "\\\\.\\pipe\\", DEFAULT_PIPE + "\x00"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_pipe_path(path)
        self.assertEqual(validate_pipe_path(DEFAULT_PIPE), DEFAULT_PIPE)

    def test_owner_is_checked_before_any_io_and_failure_closes_handle(self):
        api = api_for()
        check = Mock(side_effect=PermissionError("wrong user"))
        with self.assertRaises(PermissionError):
            WindowsPipe(DEFAULT_PIPE, api=api, owner_check=check)
        check.assert_called_once_with(17)
        api.CloseHandle.assert_called_once_with(17)
        api.WriteFile.assert_not_called()

    def test_overlapped_byte_stream_does_not_add_an_extra_header(self):
        api = api_for(Operation(b"raw"))
        pipe = WindowsPipe(DEFAULT_PIPE, api=api, owner_check=Mock())
        pipe.sendall(b"raw")
        api.WriteFile.assert_called_once_with(17, b"raw", overlapped=True)
        self.assertEqual(pipe.recv(65536), b"raw")
        self.assertTrue(api.CreateFile.call_args.args[5] & 0x00100000)
        pipe.close()
        pipe.close()
        api.CloseHandle.assert_called_once_with(17)

    def test_short_writes_force_reconnect(self):
        pipe = WindowsPipe(DEFAULT_PIPE, api=api_for(Operation(count=1)), owner_check=Mock())
        with self.assertRaisesRegex(OSError, "Short"):
            pipe.sendall(b"frame")
        pipe.close()

    def test_pending_read_timeout_cancels_operation(self):
        operation = Operation(error=995)
        pipe = WindowsPipe(DEFAULT_PIPE, timeout=0, api=api_for(operation, pending=True), owner_check=Mock())
        with self.assertRaises(socket.timeout):
            pipe.recv(65536)
        self.assertTrue(operation.cancelled)
        pipe.close()

    def test_timeout_race_preserves_completed_input_bytes(self):
        operation = Operation(b"partial-frame")
        pipe = WindowsPipe(DEFAULT_PIPE, timeout=0, api=api_for(operation, pending=True), owner_check=Mock())
        self.assertEqual(pipe.recv(65536), b"partial-frame")
        self.assertTrue(operation.cancelled)
        pipe.close()

    def test_shutdown_cancels_inflight_read_promptly(self):
        operation = Operation(error=995)
        api = api_for(operation, pending=True)
        pipe = WindowsPipe(DEFAULT_PIPE, api=api, owner_check=Mock())
        def wait(*args):
            pipe.shutdown()
            return api.WAIT_TIMEOUT
        api.WaitForSingleObject.side_effect = wait
        with self.assertRaises(OSError):
            pipe.recv(65536)
        self.assertTrue(operation.cancelled)
        pipe.close()

    def test_broken_pipe_reports_disconnect(self):
        pipe = WindowsPipe(DEFAULT_PIPE, api=api_for(Operation(error=109)), owner_check=Mock())
        with self.assertRaises(EOFError):
            pipe.recv(65536)
        pipe.close()

    def test_windows_default_does_not_depend_on_mac_home(self):
        with patch("runtime_feed.sys.platform", "win32"):
            self.assertEqual(default_endpoint(), DEFAULT_PIPE)
            with patch("windows_ipc.WindowsPipe") as constructor:
                open_transport(DEFAULT_PIPE)
                constructor.assert_called_once_with(DEFAULT_PIPE)

    def test_posix_home_override(self):
        with patch("runtime_feed.sys.platform", "darwin"), patch.dict("os.environ", {"CODEX_HOME": "/tmp/custom-codex"}):
            self.assertEqual(default_endpoint(), str(Path('/tmp/custom-codex') / 'ipc/ipc.sock'))


class ScriptedTransport:
    """A framed fake desktop peer that splits every header and message."""
    def __init__(self):
        self.incoming = queue.Queue()
        self.sent = []
        self.closed = False

    def frame(self, message):
        raw = json.dumps(message).encode()
        frame = struct.pack("<I", len(raw)) + raw
        for start, end in [(0, 2), (2, 4), (4, 11), (11, len(frame))]:
            self.incoming.put(frame[start:end])

    def sendall(self, raw):
        length = struct.unpack("<I", raw[:4])[0]
        if length != len(raw) - 4:
            raise AssertionError("wrong byte framing")
        message = json.loads(raw[4:])
        self.sent.append(message)
        method = message.get("method")
        if method == "initialize":
            self.frame({"type": "response", "requestId": message["requestId"],
                        "resultType": "success", "result": {"clientId": "test-observer"}})
        elif method == "thread-owner-discovery":
            self.frame({"type": "response", "requestId": message["requestId"],
                        "resultType": "success", "handledByClientId": "test-owner"})
        elif method == "thread-stream-following-changed" and message["params"]["following"]:
            tid = message["params"]["conversationId"]
            self.frame({"type": "broadcast", "method": "thread-stream-state-changed",
                        "sourceClientId": "test-owner", "params": {"hostId": "local", "conversationId": tid,
                        "change": {"type": "snapshot", "revision": 1, "conversationState": {
                            "id": tid, "hostId": "local", "parentThreadId": None,
                            "threadRuntimeStatus": {"type": "active", "activeFlags": ["waitingOnUserInput"]},
                            "requests": [], "turns": ["PRIVATE CONTENT"]}}}})

    def recv(self, _size):
        try:
            return self.incoming.get(timeout=0.01)
        except queue.Empty:
            raise socket.timeout()

    def shutdown(self, *_args):
        self.incoming.put(b"")

    def close(self):
        self.closed = True


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class FeedTransportTests(unittest.TestCase):
    def test_fragmented_stream_projects_metadata_and_clears_on_disconnect(self):
        transport = ScriptedTransport()
        tid = str(uuid.uuid4())
        with patch("runtime_feed.open_transport", return_value=transport):
            feed = DesktopRuntimeFeed(socket_path=DEFAULT_PIPE, reconnect_seconds=2)
            feed.set_targets([tid])
            try:
                self.assertTrue(eventually(lambda: tid in feed.snapshot()["threads"]))
                state = feed.snapshot()
                self.assertEqual(state["threads"][tid]["runtime_status"]["activeFlags"], ["waitingOnUserInput"])
                self.assertNotIn("PRIVATE", repr(state))
                self.assertLessEqual(set(m.get("method") for m in transport.sent),
                                     {"initialize", "thread-owner-discovery", "thread-stream-following-changed"})
                transport.incoming.put(b"")
                self.assertTrue(eventually(lambda: not feed.snapshot()["connected"]))
                self.assertEqual(feed.snapshot()["threads"], {})
            finally:
                feed.close()
            self.assertFalse(feed._worker.is_alive())
            self.assertTrue(transport.closed)

    def test_invalid_frame_disconnects_without_killing_retry_worker(self):
        for frame in [struct.pack("<I", 0), struct.pack("<I", 64 * 1024 * 1024 + 1),
                      struct.pack("<I", 2) + b"[]"]:
            with self.subTest(frame=frame):
                transport = ScriptedTransport()
                transport.incoming.put(frame)
                with patch("runtime_feed.open_transport", return_value=transport):
                    feed = DesktopRuntimeFeed(socket_path=DEFAULT_PIPE)
                    try:
                        self.assertTrue(eventually(lambda: feed.snapshot()["error"] == "ValueError"))
                        self.assertTrue(feed._worker.is_alive())
                        self.assertFalse(feed.snapshot()["connected"])
                    finally:
                        feed.close()


@unittest.skipUnless(sys.platform == "win32", "requires Windows named pipes")
class NativePipeTests(unittest.TestCase):
    def setUp(self):
        import _winapi
        self.api = _winapi
        self.path = "\\\\.\\pipe\\rotating-flower-test-" + uuid.uuid4().hex
        self.server = _winapi.CreateNamedPipe(
            self.path, _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED,
            0, 1, 65536, 65536, 1000, 0)
        self.connected = _winapi.ConnectNamedPipe(self.server, overlapped=True)
        self.client = WindowsPipe(self.path, timeout=0.05)
        self.connected.GetOverlappedResult(True)

    def tearDown(self):
        self.client.close()
        self.api.CloseHandle(self.server)

    def test_native_byte_pipe_roundtrip_and_idle_timeout(self):
        payload = b"\x05\x00\x00\x00hello"
        self.client.sendall(payload)
        operation, error = self.api.ReadFile(self.server, 1024, overlapped=True)
        count, error = operation.GetOverlappedResult(True)
        self.assertEqual(error, 0)
        self.assertEqual(bytes(operation.getbuffer())[:count], payload)
        operation, error = self.api.WriteFile(self.server, b"reply", overlapped=True)
        operation.GetOverlappedResult(True)
        self.assertEqual(self.client.recv(1024), b"reply")
        with self.assertRaises(socket.timeout):
            self.client.recv(1024)
        # Cancellation must not poison a later read or lose bytes.
        operation, error = self.api.WriteFile(self.server, b"after-timeout", overlapped=True)
        operation.GetOverlappedResult(True)
        self.assertEqual(self.client.recv(1024), b"after-timeout")

    def test_native_shutdown_interrupts_pending_read(self):
        self.client._timeout = 10
        errors = []
        def read():
            try:
                self.client.recv(1024)
            except OSError as exc:
                errors.append(exc)
        thread = threading.Thread(target=read)
        thread.start()
        self.client.shutdown()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
