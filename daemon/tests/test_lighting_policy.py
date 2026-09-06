# SPDX-License-Identifier: GPL-2.0-or-later

import configparser
import contextlib
import types
import unittest
import unittest.mock

from openrazer_daemon.daemon import RazerDaemon
from openrazer_daemon.device import DeviceCollection


class LightingPolicyTest(unittest.TestCase):
    def setUp(self):
        self.daemon = object.__new__(RazerDaemon)
        self.daemon.logger = unittest.mock.MagicMock()
        self.daemon._config = configparser.ConfigParser()
        self.daemon._config['Startup'] = {'devices_off_on_display': 'true'}
        self.daemon._screensaver_monitor = types.SimpleNamespace(
            active=False, monitoring=False,
        )
        self.daemon._lighting_power_monitor = types.SimpleNamespace(
            display_off=False, lid_closed=False, session_active=True,
        )
        self.blade = self.device('BLADE', lid_lighting=True)
        self.keyboard = self.device('KEYBOARD')
        self.daemon._razer_devices = [self.blade, self.keyboard]

    @staticmethod
    def device(serial, lid_lighting=False):
        return types.SimpleNamespace(
            serial=serial,
            dbus=types.SimpleNamespace(
                LID_LIGHTING=lid_lighting,
                set_lighting_state=unittest.mock.MagicMock(return_value=True),
            ),
        )

    def assert_states(self, blade_state, keyboard_state, force=False):
        self.blade.dbus.set_lighting_state.assert_called_once_with(blade_state, force=force)
        self.keyboard.dbus.set_lighting_state.assert_called_once_with(keyboard_state, force=force)

    def test_active_session_uses_software_lighting(self):
        self.assertTrue(self.daemon.apply_lighting_policy())

        self.assert_states('software', 'software')

    def test_visible_locked_session_uses_hardware_lighting(self):
        self.daemon._screensaver_monitor.active = True

        self.assertTrue(self.daemon.apply_lighting_policy())

        self.assert_states('hardware', 'hardware')

    def test_legacy_screensaver_preference_turns_lighting_off(self):
        self.daemon._screensaver_monitor.active = True
        self.daemon._screensaver_monitor.monitoring = True

        self.daemon.apply_lighting_policy()

        self.assert_states('off', 'off')

    def test_screensaver_preference_does_not_turn_unlocked_lighting_off(self):
        self.daemon._screensaver_monitor.monitoring = True

        self.daemon.apply_lighting_policy()

        self.assert_states('software', 'software')

    def test_closed_lid_only_turns_off_lid_lighting_devices(self):
        self.daemon._lighting_power_monitor.lid_closed = True

        self.daemon.apply_lighting_policy()

        self.assert_states('off', 'software')

    def test_closed_lid_keeps_external_keyboard_available_at_lock_screen(self):
        self.daemon._lighting_power_monitor.lid_closed = True
        self.daemon._screensaver_monitor.active = True

        self.daemon.apply_lighting_policy()

        self.assert_states('off', 'hardware')

    def test_lid_reopening_restores_hardware_until_unlock(self):
        self.daemon._screensaver_monitor.active = True
        self.daemon._lighting_power_monitor.lid_closed = True
        self.daemon.apply_lighting_policy()

        self.daemon._lighting_power_monitor.lid_closed = False
        self.daemon.apply_lighting_policy()

        self.daemon._screensaver_monitor.active = False
        self.daemon.apply_lighting_policy()

        self.assertEqual(self.blade.dbus.set_lighting_state.call_args_list, [
            unittest.mock.call('off', force=False),
            unittest.mock.call('hardware', force=False),
            unittest.mock.call('software', force=False),
        ])

    def test_display_off_overrides_locked_and_unlocked_sessions(self):
        self.daemon._lighting_power_monitor.display_off = True
        for locked in (False, True):
            with self.subTest(locked=locked):
                self.daemon._screensaver_monitor.active = locked
                self.blade.dbus.set_lighting_state.reset_mock()
                self.keyboard.dbus.set_lighting_state.reset_mock()

                self.daemon.apply_lighting_policy()

                self.assert_states('off', 'off')

    def test_display_off_preference_preserves_session_lighting_when_disabled(self):
        self.daemon._config['Startup']['devices_off_on_display'] = 'false'
        self.daemon._lighting_power_monitor.display_off = True
        for locked, state in ((False, 'software'), (True, 'hardware')):
            with self.subTest(locked=locked):
                self.daemon._screensaver_monitor.active = locked
                self.blade.dbus.set_lighting_state.reset_mock()
                self.keyboard.dbus.set_lighting_state.reset_mock()

                self.daemon.apply_lighting_policy()

                self.assert_states(state, state)

    def test_inactive_session_uses_hardware_without_screensaver_service(self):
        self.daemon._screensaver_monitor = None
        self.daemon._lighting_power_monitor.session_active = False

        self.daemon.apply_lighting_policy()

        self.assert_states('hardware', 'hardware')

    def test_unknown_monitor_state_does_not_disable_lighting(self):
        self.daemon._screensaver_monitor.active = None
        self.daemon._lighting_power_monitor = types.SimpleNamespace(
            display_off=None, lid_closed=None, session_active=None,
        )

        self.daemon.apply_lighting_policy()

        self.assert_states('software', 'software')

    def test_unavailable_monitors_do_not_disable_lighting(self):
        self.daemon._screensaver_monitor = None
        self.daemon._lighting_power_monitor = None

        self.daemon.apply_lighting_policy()

        self.assert_states('software', 'software')

    def test_repeated_policy_is_delegated_for_device_retry_and_deduplication(self):
        self.daemon.apply_lighting_policy()
        self.daemon.apply_lighting_policy()
        self.daemon.apply_lighting_policy(force=True)

        self.assertEqual(self.blade.dbus.set_lighting_state.call_args_list, [
            unittest.mock.call('software', force=False),
            unittest.mock.call('software', force=False),
            unittest.mock.call('software', force=True),
        ])

    def test_device_failure_does_not_skip_other_devices(self):
        for failure in (False, TimeoutError('device asleep')):
            with self.subTest(failure=failure):
                self.blade.dbus.set_lighting_state.reset_mock(side_effect=True)
                self.keyboard.dbus.set_lighting_state.reset_mock()
                self.daemon.logger.warning.reset_mock()
                if isinstance(failure, Exception):
                    self.blade.dbus.set_lighting_state.side_effect = failure
                else:
                    self.blade.dbus.set_lighting_state.return_value = failure

                self.assertFalse(self.daemon.apply_lighting_policy())

                self.assert_states('software', 'software')
                self.assertEqual(self.daemon.logger.warning.call_count, int(isinstance(failure, Exception)))

    def test_hotplug_applies_latest_state_on_main_loop(self):
        device = unittest.mock.MagicMock()
        device.LID_LIGHTING = False
        device.get_serial.return_value = 'HOTPLUG'
        device.set_lighting_state.return_value = True
        device_class = unittest.mock.MagicMock(return_value=device)
        device_class.match.return_value = True
        self.daemon._device_classes = [device_class]
        self.daemon._razer_devices = DeviceCollection()
        self.daemon._test_dir = None
        self.daemon._persistence = configparser.ConfigParser()
        self.daemon._unknown_serial_counter = {}
        self.daemon.device_added = unittest.mock.MagicMock()
        udev_device = types.SimpleNamespace(
            sys_name='0003:1532:FFFF.0001', sys_path='/missing',
        )
        with unittest.mock.patch('openrazer_daemon.daemon.time.sleep'), \
                unittest.mock.patch('openrazer_daemon.daemon.GLib.idle_add') as idle_add:
            self.daemon._add_device(udev_device)

        device.set_lighting_state.assert_not_called()
        self.assertEqual(len(self.daemon._razer_devices), 1)
        self.daemon._screensaver_monitor.active = True

        self.assertFalse(idle_add.call_args.args[0]())

        device.set_lighting_state.assert_called_once_with('hardware', force=False)

    def test_startup_applies_initial_session_state_to_loaded_devices(self):
        device = unittest.mock.MagicMock()
        device.LID_LIGHTING = False
        device.set_lighting_state.return_value = True

        def read_config(daemon, _config_file):
            daemon._config['Startup'] = {
                'devices_off_on_display': 'true',
                'sync_effects_enabled': 'false',
            }
            daemon._config['General'] = {'verbose_logging': 'false'}

        def screensaver(daemon):
            daemon._screensaver_monitor = types.SimpleNamespace(active=True, monitoring=False)

        def power(daemon):
            daemon._lighting_power_monitor = types.SimpleNamespace(
                display_off=False, lid_closed=False, session_active=True,
            )

        def load_devices(daemon, first_run=False):
            self.assertTrue(first_run)
            daemon._razer_devices.add('0003:1532:FFFF.0001', 'STARTUP', device)

        with contextlib.ExitStack() as stack:
            for name in ('setproctitle.setproctitle', 'dbus.mainloop.glib.threads_init',
                         'dbus.mainloop.glib.DBusGMainLoop', 'GLib.MainLoop'):
                stack.enter_context(unittest.mock.patch('openrazer_daemon.daemon.' + name))
            stack.enter_context(unittest.mock.patch(
                'openrazer_daemon.daemon.DBusService.__init__', return_value=None,
            ))
            stack.enter_context(unittest.mock.patch(
                'openrazer_daemon.daemon.openrazer_daemon.hardware.get_device_classes', return_value=[],
            ))
            for name in ('read_persistence', '_init_signals', '_init_udev_monitor',
                         '_init_system_sleep_monitor', '_init_autosave_persistence',
                         'add_dbus_method', 'sync_effects'):
                stack.enter_context(unittest.mock.patch.object(RazerDaemon, name))
            stack.enter_context(unittest.mock.patch.object(RazerDaemon, '_check_plugdev_group', return_value=True))
            stack.enter_context(unittest.mock.patch.object(RazerDaemon, '_create_logger'))
            for name, callback in (
                    ('read_config', read_config),
                    ('_init_screensaver_monitor', screensaver),
                    ('_init_lighting_power_monitor', power),
                    ('_load_devices', load_devices)):
                stack.enter_context(unittest.mock.patch.object(RazerDaemon, name, callback))

            RazerDaemon()

        device.set_lighting_state.assert_called_once_with('hardware', force=False)


if __name__ == '__main__':
    unittest.main()
