"""Bounded serial I/O; pyserial is loaded only when Windows USB is used."""
from __future__ import annotations

import glob
import importlib
import os
import re
import select
import subprocess
import sys

from platform_support import is_windows


class SerialDependencyError(RuntimeError):
    pass


def _serial_module():
    try:
        return importlib.import_module('serial')
    except ImportError as exc:
        raise SerialDependencyError(
            'Windows USB control requires pyserial. Run: python -m pip install pyserial'
        ) from exc


def serial_candidates() -> list[str]:
    if is_windows():
        _serial_module()
        ports = importlib.import_module('serial.tools.list_ports').comports()
        # Match the USB-only auto discovery used on macOS. A fixed --port
        # remains available for adapters whose driver omits USB metadata.
        names = {port.device.upper() for port in ports
                 if re.fullmatch(r'COM\d+', port.device, re.I)
                 and (getattr(port, 'vid', None) is not None
                      or 'USB VID:PID=' in (getattr(port, 'hwid', '') or '').upper())}
        return sorted(names, key=lambda name: int(name[3:]))
    patterns = ['/dev/cu.usbmodem*', '/dev/cu.usbserial*',
                '/dev/serial/by-id/*', '/dev/ttyACM*', '/dev/ttyUSB*']
    return sorted({path for pattern in patterns for path in glob.glob(pattern)})


class PosixSerialTransport:
    def __init__(self, fd, exclusive=True):
        self.handle = fd
        self.exclusive = exclusive

    @classmethod
    def open(cls, path, exclusive=True):
        import fcntl
        import termios
        flag = '-f' if sys.platform == 'darwin' else '-F'
        subprocess.run(['stty', flag, path, '115200', 'raw', '-echo'],
                       check=True, capture_output=True, timeout=2)
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if exclusive:
                fcntl.ioctl(fd, termios.TIOCEXCL)
        except BaseException:
            os.close(fd)
            raise
        return cls(fd, exclusive)

    def read(self, size=4096, timeout=0):
        if not select.select([self.handle], [], [], timeout)[0]:
            return b''
        data = os.read(self.handle, size)
        if not data:
            raise OSError('Serial EOF')
        return data

    def write(self, data):
        return os.write(self.handle, data)

    def close(self):
        if self.handle is None:
            return
        try:
            if self.exclusive:
                import fcntl
                import termios
                try:
                    fcntl.ioctl(self.handle, termios.TIOCNXCL)
                except OSError:
                    pass
        finally:
            fd, self.handle = self.handle, None
            os.close(fd)


class WindowsSerialTransport:
    def __init__(self, port):
        serial = _serial_module()
        # Let pyserial handle the Win32 device path, including COM10 and above.
        self.handle = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=1)
        try:
            # Request inactive reset/boot lines; some USB drivers may still
            # briefly toggle these when opening, so hardware QA is required.
            self.handle.dtr = False
            self.handle.rts = False
            self.handle.port = port
            self.handle.open()
        except BaseException:
            self.handle.close()
            raise

    def read(self, size=4096, timeout=0):
        self.handle.timeout = timeout
        try:
            # A bounded one-byte wait avoids waiting for 4096 bytes on Windows.
            first = self.handle.read(1)
            if not first:
                return b''
            self.handle.timeout = 0
            return first + self.handle.read(size - 1)
        finally:
            self.handle.timeout = 0

    def write(self, data):
        return self.handle.write(data)

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            handle.close()


def open_transport(path, *, exclusive=True):
    return WindowsSerialTransport(path) if is_windows() else PosixSerialTransport.open(path, exclusive)


def write_frame(transport, data):
    """A partial command is unsafe to resume; drop the port and re-handshake."""
    if transport.write(data) != len(data):
        raise OSError('Short serial write')
