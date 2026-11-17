import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import integrated_bridge as bridge
import serial_transport as transport


class FakePort:
    def __init__(self, **settings):
        self.settings = settings
        self.timeout = settings['timeout']
        self.rx = bytearray()
        self.writes = []
        self.closed = False
        self.read_timeouts = []

    def open(self):
        self.open_state = (self.port, self.dtr, self.rts)

    def read(self, count):
        self.read_timeouts.append(self.timeout)
        result = bytes(self.rx[:count])
        del self.rx[:count]
        return result

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def close(self):
        self.closed = True


class SerialTransportTests(unittest.TestCase):
    def test_windows_ports_include_com10_and_sort_numerically(self):
        module = Mock()
        listing = SimpleNamespace(comports=lambda: [SimpleNamespace(device=p, vid=0x1A86)
                                for p in ['COM10', 'COM2', 'com10', 'LPT1']])
        with patch.object(transport, 'is_windows', return_value=True), \
                patch.object(transport.importlib, 'import_module',
                             side_effect=lambda name: listing if name.endswith('list_ports') else module):
            self.assertEqual(transport.serial_candidates(), ['COM2', 'COM10'])

    def test_windows_auto_only_probes_usb_backed_ports(self):
        module = Mock()
        ports = [
            SimpleNamespace(device='COM1', vid=None, hwid='ACPI\\PNP0501\\0'),
            SimpleNamespace(device='COM3', vid=None, hwid='BTHENUM\\DEV_1234'),
            SimpleNamespace(device='COM4', vid=None, hwid='n/a'),
            SimpleNamespace(device='COM5', vid=None, hwid=None),
            SimpleNamespace(device='COM7', vid=None, hwid='USB VID:PID=1A86:7523 SER=1234'),
            SimpleNamespace(device='COM12', vid=0x10C4, hwid='n/a'),
        ]
        listing = SimpleNamespace(comports=lambda: ports)
        with patch.object(transport, 'is_windows', return_value=True), \
                patch.object(transport.importlib, 'import_module',
                             side_effect=lambda name: listing if name.endswith('list_ports') else module):
            self.assertEqual(transport.serial_candidates(), ['COM7', 'COM12'])

    def test_windows_port_settings_bounded_read_and_com10_passthrough(self):
        serial = SimpleNamespace(Serial=FakePort)
        with patch.object(transport, '_serial_module', return_value=serial):
            channel = transport.WindowsSerialTransport('COM10')
        port = channel.handle
        self.assertEqual(port.open_state, ('COM10', False, False))
        self.assertEqual(port.settings['baudrate'], 115200)
        self.assertEqual(port.settings['write_timeout'], 1)
        port.rx.extend(b'PONG\n')
        self.assertEqual(channel.read(4096, .1), b'PONG\n')
        self.assertEqual(port.read_timeouts, [.1, 0])
        self.assertEqual(port.timeout, 0)
        self.assertEqual(channel.read(4096, 0), b'')
        transport.write_frame(channel, b'STOP\n')
        self.assertEqual(port.writes, [b'STOP\n'])
        channel.close()
        self.assertTrue(port.closed)

    def test_windows_read_disconnect_restores_nonblocking_timeout(self):
        serial = SimpleNamespace(Serial=FakePort)
        with patch.object(transport, '_serial_module', return_value=serial):
            channel = transport.WindowsSerialTransport('COM3')
        channel.handle.read = Mock(side_effect=OSError('USB disconnected'))
        with self.assertRaises(OSError):
            channel.read(timeout=.1)
        self.assertEqual(channel.handle.timeout, 0)

    def test_missing_pyserial_has_actionable_error(self):
        with patch.object(transport.importlib, 'import_module', side_effect=ImportError):
            with self.assertRaisesRegex(transport.SerialDependencyError, 'pip install pyserial'):
                transport.WindowsSerialTransport('COM1')

    def test_short_write_is_not_treated_as_a_complete_command(self):
        channel = SimpleNamespace(write=lambda data: len(data) - 1)
        with self.assertRaisesRegex(OSError, 'Short serial write'):
            transport.write_frame(channel, b'RUN LOW\nATTENTION ON\n')


class FlowerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = 10
        self.log = patch.object(bridge, 'log').start()
        self.clock_patch = patch.object(bridge.time, 'monotonic', side_effect=lambda: self.clock).start()
        self.addCleanup(patch.stopall)

    def channel(self):
        channel = Mock(handle=object())
        channel.read.side_effect = [b'FLOWER_COMBINED_V1 PWM=OK MOTOR_PINS=4,5,6,7\n', b'']
        channel.write.side_effect = lambda data: len(data)
        return channel

    def test_handshake_heartbeat_disconnect_and_reconnect_on_com_port(self):
        first, second = self.channel(), self.channel()
        with patch.object(bridge, 'open_transport', side_effect=[first, second]) as opened:
            flower = bridge.Flower('COM12')
            self.assertTrue(flower.send('RUN HIGH', True))
            self.assertEqual(first.write.call_args_list[0].args, (b'DEVICE INFO\nPING\n',))
            first.read.side_effect = None
            first.read.return_value = b''
            self.clock = 10.3
            self.assertTrue(flower.send('RUN HIGH', True))
            self.assertEqual(first.write.call_count, 2)
            self.clock = 11.6
            self.assertTrue(flower.send('RUN HIGH', True))
            self.assertEqual(first.write.call_count, 3)
            first.read.side_effect = OSError('unplugged')
            self.assertFalse(flower.send('STOP', False))
            first.close.assert_called_once()
            self.assertIsNone(flower.fd)
            self.clock = 13
            self.assertTrue(flower.send('STOP', True))
            self.assertEqual(opened.call_count, 2)
            self.assertEqual(second.write.call_args.args, (b'STOP\nATTENTION ON\n',))
            flower.stop()
            self.assertEqual(second.write.call_args.args, (b'STOP\nATTENTION OFF\n',))
            second.close.assert_called_once()

    def test_short_handshake_write_closes_port_before_running(self):
        channel = self.channel()
        channel.write.side_effect = lambda data: 2
        with patch.object(bridge, 'open_transport', return_value=channel):
            flower = bridge.Flower('COM10')
            self.assertFalse(flower.send('RUN ULTRA', True))
        channel.close.assert_called_once()
        channel.read.assert_not_called()
        self.assertIsNone(flower.fd)


class ReadOnlyBridgeTests(unittest.TestCase):
    def test_dry_run_writes_status_and_cooperatively_stops_without_usb(self):
        source = Mock()
        source.poll.return_value = {'healthy': True, 'motor': 'RUN HIGH', 'light': True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status, stop = root / '状态.json', root / 'stop.request'
            with patch.object(bridge, 'WorkBuddyAdapter', return_value=source), \
                    patch.object(bridge, 'serial_candidates') as discovery, \
                    patch.object(bridge, 'open_transport') as opened, \
                    patch.object(bridge, 'acquire_lock') as lock, \
                    patch.object(bridge.time, 'sleep', side_effect=lambda _: stop.write_text('stop')), \
                    patch.object(bridge, 'log'):
                bridge.main(['--source', 'workbuddy', '--dry-run', '--status-file', str(status),
                             '--stop-file', str(stop)])
            result = json.loads(status.read_text(encoding='utf-8'))
            self.assertTrue(result['dry_run'])
            self.assertEqual(result['motor'], 'RUN HIGH')
            self.assertTrue(result['light'])
            self.assertFalse(result['device_connected'])
            discovery.assert_not_called()
            opened.assert_not_called()
            lock.assert_not_called()
            source.close.assert_called_once()

    def test_codex_endpoint_is_passed_to_runtime_feed(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(bridge, 'DesktopRuntimeFeed') as feed:
            source = bridge.CodexSource(Path(directory) / 'sessions', None, r'\\.\pipe\explicit-test')
            feed.assert_called_once_with(socket_path=r'\\.\pipe\explicit-test', refresh_seconds=2)
            source.close()


if __name__ == '__main__':
    unittest.main()
