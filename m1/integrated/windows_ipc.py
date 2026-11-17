"""Local Windows byte-pipe transport for the desktop metadata observer.

Uses CPython's Win32 wrapper, also used by multiprocessing, but deliberately
does not use multiprocessing.Connection's incompatible message framing.
ReadFile/WriteFile use overlapped I/O so an idle app cannot block shutdown.
No optional modules are imported until a Windows connection is requested.
"""
from __future__ import annotations

import socket
import threading
import time


PIPE_PREFIX = "\\\\.\\pipe\\"
DEFAULT_PIPE = PIPE_PREFIX + "codex-ipc"


def validate_pipe_path(path):
    if (not isinstance(path, str) or not path.lower().startswith(PIPE_PREFIX)
            or not path[len(PIPE_PREFIX):] or any(c in path for c in "\x00\r\n/")):
        raise ValueError("Windows Codex endpoint must be a local \\\\.\\pipe\\NAME path")
    return path


def verify_pipe_owner(handle):
    """Reject a pipe served by another Windows account, before sending data."""
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetNamedPipeServerProcessId.argtypes = [w.HANDLE, ctypes.POINTER(w.ULONG)]
    kernel.GetNamedPipeServerProcessId.restype = w.BOOL
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    security.OpenProcessToken.restype = w.BOOL
    security.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID,
                                            w.DWORD, ctypes.POINTER(w.DWORD)]
    security.GetTokenInformation.restype = w.BOOL
    security.EqualSid.argtypes = [w.LPVOID, w.LPVOID]
    security.EqualSid.restype = w.BOOL

    def token_user(process):
        token = w.HANDLE()
        if not security.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            length = w.DWORD()
            security.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
            if not 0 < length.value <= 65536:
                raise PermissionError("Cannot inspect Codex pipe server identity")
            data = ctypes.create_string_buffer(length.value)
            if not security.GetTokenInformation(token, 1, data, length, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            return data
        finally:
            kernel.CloseHandle(token)

    pid = w.ULONG()
    if not kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    process = kernel.OpenProcess(0x1000, False, pid.value)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ours = token_user(kernel.GetCurrentProcess())
        theirs = token_user(process)
        # TOKEN_USER starts with SID_AND_ATTRIBUTES; its first field is PSID.
        our_sid = ctypes.cast(ours, ctypes.POINTER(w.LPVOID))[0]
        their_sid = ctypes.cast(theirs, ctypes.POINTER(w.LPVOID))[0]
        if not security.EqualSid(our_sid, their_sid):
            raise PermissionError("Codex IPC pipe server belongs to another user")
    finally:
        kernel.CloseHandle(process)


class WindowsPipe:
    """Socket-like byte stream, owned by one reader/writer worker thread.

    shutdown() may be called from the controller thread; close() is called by
    the worker after its cancelled I/O completes, avoiding handle reuse races.
    """
    def __init__(self, path, timeout=0.25, *, api=None, owner_check=None):
        validate_pipe_path(path)
        if api is None:
            import _winapi as api
        self._api = api
        self._closed = threading.Event()
        self._timeout = timeout
        self._handle = None
        # SECURITY_SQOS_PRESENT | SECURITY_ANONYMOUS prevents server-side
        # impersonation of this client. No remote (UNC host) pipes are allowed.
        handle = api.CreateFile(path, api.GENERIC_READ | api.GENERIC_WRITE,
                                0, 0, api.OPEN_EXISTING,
                                api.FILE_FLAG_OVERLAPPED | 0x00100000, 0)
        try:
            (owner_check or verify_pipe_owner)(handle)
        except BaseException:
            api.CloseHandle(handle)
            raise
        self._handle = handle

    def _complete(self, operation, error):
        api = self._api
        deadline = time.monotonic() + self._timeout
        if error == api.ERROR_IO_PENDING:
            try:
                while True:
                    if self._closed.is_set():
                        raise OSError("Codex IPC pipe was closed")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise socket.timeout("Codex IPC pipe timed out")
                    result = api.WaitForSingleObject(operation.event, max(1, min(50, int(remaining * 1000))))
                    if result == api.WAIT_OBJECT_0:
                        break
                    if result != api.WAIT_TIMEOUT:
                        raise OSError("Unexpected IPC wait result")
            except BaseException as exc:
                operation.cancel()
                # Keep the overlapped buffer alive until cancellation completes.
                count, completed_error = operation.GetOverlappedResult(True)
                # An input may complete concurrently with its timeout. Return
                # those bytes instead of dropping part of the framing stream.
                if isinstance(exc, socket.timeout) and completed_error == 0:
                    return count
                raise
        count, error = operation.GetOverlappedResult(True)
        if error:
            if error in (api.ERROR_BROKEN_PIPE, 232, 233):
                raise EOFError("Codex desktop disconnected")
            raise OSError("Codex IPC operation failed (Windows error %s)" % error)
        return count

    def recv(self, size):
        if self._closed.is_set() or self._handle is None:
            raise OSError("Codex IPC pipe is closed")
        operation, error = self._api.ReadFile(self._handle, size, overlapped=True)
        count = self._complete(operation, error)
        return bytes(operation.getbuffer())[:count]

    def sendall(self, data):
        if self._closed.is_set() or self._handle is None:
            raise OSError("Codex IPC pipe is closed")
        # The application frames a single byte stream; never add a second header.
        operation, error = self._api.WriteFile(self._handle, data, overlapped=True)
        if self._complete(operation, error) != len(data):
            raise OSError("Short Codex IPC write")

    def shutdown(self, _how=None):
        self._closed.set()

    def close(self):
        self._closed.set()
        if self._handle is not None:
            self._api.CloseHandle(self._handle)
            self._handle = None
