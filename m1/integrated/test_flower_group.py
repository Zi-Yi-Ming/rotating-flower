import unittest
from unittest.mock import patch

from integrated_bridge import FlowerGroup


class FakeFlower:
    """A controllable board; no serial descriptors or hardware are opened."""

    def __init__(self, port, fixture):
        self.port = port
        self.fixture = fixture
        self.fd = None
        self.path = None
        self.last_probe = -100
        self.capabilities = []
        self.last_device_line = None
        self.sent = []
        self.close_count = 0
        self.stop_count = 0

    def send(self, motor, light):
        probing = self.fd is None
        self.fixture.events.append((self.port, probing, motor, light))
        if probing:
            self.last_probe = self.fixture.now
            if self.port in self.fixture.unresponsive:
                return False
            self.fd = 42
            self.path = self.port
            self.capabilities = ['motor', 'attention']
        self.sent.append((motor, light))
        return True

    def close(self):
        self.close_count += 1
        self.fd = None
        self.path = None
        self.capabilities = []

    def stop(self):
        self.stop_count += 1
        if self.fd is not None:
            self.sent.append(('STOP', False))
        self.close()


class FlowerGroupTests(unittest.TestCase):
    def setUp(self):
        self.paths = []
        self.now = 100
        self.events = []
        self.unresponsive = set()
        self.instances = []
        self.discovery = self.start_patch(patch(
            'integrated_bridge.serial_candidates', side_effect=lambda: list(self.paths)))
        self.start_patch(patch('integrated_bridge.time.monotonic', side_effect=lambda: self.now))
        self.start_patch(patch('integrated_bridge.log'))
        self.start_patch(patch('integrated_bridge.Flower', side_effect=self.make_flower))
        self.group = FlowerGroup()

    def start_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_flower(self, port):
        flower = FakeFlower(port, self)
        self.instances.append(flower)
        return flower

    def connect_pair(self):
        self.paths[:] = ['usb-a', 'usb-b']
        self.group.send('STOP', False)
        self.now += .3
        self.group.send('STOP', False)
        return self.group.flowers['usb-a'], self.group.flowers['usb-b']

    def test_broadcasts_work_attention_and_completion_to_both_boards(self):
        first, second = self.connect_pair()
        states = [('RUN ULTRA', False), ('RUN HIGH', True), ('STOP', True), ('STOP', False)]
        first.sent.clear()
        second.sent.clear()
        for motor, light in states:
            self.now += .3
            self.assertTrue(self.group.send(motor, light))
        self.assertEqual(first.sent, states)
        self.assertEqual(second.sent, states)

    def test_hotplug_follows_current_state_after_existing_board_refresh(self):
        self.paths[:] = ['usb-a']
        self.group.send('RUN HIGH', False)
        self.paths.append('usb-b')
        self.events.clear()
        self.now += .3
        self.assertTrue(self.group.send('RUN ULTRA', True))
        self.assertEqual(self.events, [
            ('usb-a', False, 'RUN ULTRA', True),
            ('usb-b', True, 'RUN ULTRA', True),
        ])
        self.assertEqual(self.group.flowers['usb-b'].sent, [('RUN ULTRA', True)])

    def test_removal_closes_old_connection_and_replug_uses_current_state(self):
        old_first, second = self.connect_pair()
        self.paths.remove('usb-a')
        self.now += .3
        self.group.send('RUN HIGH', True)
        self.assertEqual(old_first.close_count, 1)
        self.assertNotIn('usb-a', self.group.flowers)
        self.assertEqual(second.sent[-1], ('RUN HIGH', True))
        self.paths.append('usb-a')
        self.now += .3
        self.group.send('STOP', True)
        new_first = self.group.flowers['usb-a']
        self.assertIsNot(new_first, old_first)
        self.assertEqual(new_first.sent, [('STOP', True)])
        self.assertEqual(second.sent[-1], ('STOP', True))
        self.assertEqual(self.group.status()['device_count'], 2)

    def test_fixed_port_does_not_discover_or_command_other_boards(self):
        self.paths[:] = ['usb-a', 'usb-b']
        group = FlowerGroup('usb-b')
        self.assertTrue(group.send('RUN HIGH', True))
        self.discovery.assert_not_called()
        self.assertEqual(list(group.flowers), ['usb-b'])
        self.assertEqual(self.events, [('usb-b', True, 'RUN HIGH', True)])

    def test_failed_probes_are_fair_bounded_and_follow_connected_heartbeat(self):
        self.paths[:] = ['bad-a', 'bad-b', 'usb-live']
        self.unresponsive.update(['bad-a', 'bad-b'])
        for now, expected in [(100, 'bad-a'), (100.3, 'bad-b'), (100.6, 'usb-live')]:
            self.now = now
            self.events.clear()
            self.group.send('RUN HIGH', False)
            self.assertEqual(self.events, [(expected, True, 'RUN HIGH', False)])
        for now, expected in [(102.1, 'bad-a'), (102.4, 'bad-b')]:
            self.now = now
            self.events.clear()
            self.assertTrue(self.group.send('RUN HIGH', True))
            self.assertEqual(self.events, [
                ('usb-live', False, 'RUN HIGH', True),
                (expected, True, 'RUN HIGH', True),
            ])
        self.now = 102.5
        self.events.clear()
        self.group.send('RUN HIGH', True)
        self.assertEqual(self.events, [('usb-live', False, 'RUN HIGH', True)])

    def test_disconnected_board_can_reconnect_while_other_board_keeps_working(self):
        first, second = self.connect_pair()
        first.close()
        self.now += 3
        self.events.clear()
        self.assertTrue(self.group.send('RUN LOW', False))
        self.assertEqual(self.events, [
            ('usb-b', False, 'RUN LOW', False),
            ('usb-a', True, 'RUN LOW', False),
        ])
        self.assertEqual(first.sent[-1], ('RUN LOW', False))
        self.assertEqual(second.sent[-1], ('RUN LOW', False))

    def test_stop_reaches_all_boards_and_clears_connections(self):
        first, second = self.connect_pair()
        self.paths.append('bad-a')
        self.unresponsive.add('bad-a')
        self.group.send('RUN HIGH', True)
        pending = self.group.flowers['bad-a']
        self.group.stop()
        for flower in [first, second, pending]:
            self.assertEqual(flower.stop_count, 1)
            self.assertIsNone(flower.fd)
        self.assertEqual(first.sent[-1], ('STOP', False))
        self.assertEqual(second.sent[-1], ('STOP', False))
        self.assertEqual(self.group.flowers, {})
        self.assertEqual(self.group.status()['device_count'], 0)

    def test_empty_status_preserves_installer_fields(self):
        self.assertEqual(self.group.status(), {
            'device_connected': False, 'device_count': 0, 'device_port': None,
            'device_capabilities': [], 'last_device_line': None, 'devices': [],
        })

    def test_status_lists_connected_boards_and_keeps_capabilities_separate(self):
        first, second = self.connect_pair()
        first.capabilities = ['motor']
        first.last_device_line = 'STOPPED'
        second.last_device_line = 'RUNNING HIGH'
        self.paths.append('bad-a')
        self.unresponsive.add('bad-a')
        self.group.send('RUN HIGH', True)
        status = self.group.status()
        self.assertTrue(status['device_connected'])
        self.assertEqual(status['device_count'], 2)
        self.assertEqual(status['device_port'], 'usb-a')
        self.assertEqual(status['device_capabilities'], ['motor'])
        self.assertEqual(status['last_device_line'], 'STOPPED')
        self.assertEqual(status['devices'], [
            {'port': 'usb-a', 'capabilities': ['motor'], 'last_device_line': 'STOPPED'},
            {'port': 'usb-b', 'capabilities': ['motor', 'attention'], 'last_device_line': 'RUNNING HIGH'},
        ])
        self.paths.remove('usb-a')
        self.group.send('RUN HIGH', True)
        status = self.group.status()
        self.assertEqual(status['device_count'], 1)
        self.assertEqual(status['device_port'], 'usb-b')
        self.assertEqual(status['device_capabilities'], ['motor', 'attention'])


if __name__ == '__main__':
    unittest.main()
