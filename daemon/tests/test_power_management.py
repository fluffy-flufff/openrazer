# SPDX-License-Identifier: GPL-2.0-or-later

import configparser
import types
import unittest
import unittest.mock

import dbus

from openrazer_daemon.daemon import RazerDaemon
from openrazer_daemon.hardware.device_base import RazerDevice
from openrazer_daemon.hardware.headsets import RazerKraken71V2
from openrazer_daemon.hardware import headsets
from openrazer_daemon.misc.screensaver_monitor import DBUS_SCREENSAVER_INTERFACES, ScreensaverMonitor
from openrazer_daemon.misc.system_sleep_monitor import (
    LOGIN1_BUS_NAME,
    LOGIN1_INTERFACE,
    LOGIN1_PATH,
    RESUME_RETRY_DELAYS_MS,
    SystemSleepMonitor,
)


class DevicePowerTest(unittest.TestCase):
    def setUp(self):
        self.device = object.__new__(RazerDevice)
        self.device.logger = unittest.mock.MagicMock()
        self.device._is_closed = True
        self.device._disable_notifications = False
        self.device._disable_persistence = False
        self.device._effect_restore_zones = {'backlight'}
        self.device._persisted_effect_state = {}
        self.device._lighting_state = 'software'
        self.device._lighting_state_applied = 'software'
        self.device._system_suspended = False
        self.device._lighting_restore_source = None
        self.device.DRIVER_MODE = True
        self.calls = []

        def record(name):
            def callback(*args):
                self.assertTrue(self.device.disable_notify)
                self.assertTrue(self.device.disable_persistence)
                self.calls.append((name, args))
            return callback

        self.device.disable_brightness = record('disable_brightness')
        self.device.restore_brightness = record('restore_brightness')
        self.device._restore_effects = record('_restore_effects')
        self.device.set_device_mode = record('set_device_mode')
        self.device._disable_lighting = record('_disable_lighting')
        self.device._restore_lighting = record('_restore_lighting')
        self.device._suspend_device = record('_suspend_device')
        self.device._resume_device = record('_resume_device')

    def test_screensaver_lighting_does_not_run_system_hooks(self):
        self.device.disable_lighting()
        self.device.restore_lighting()

        self.assertEqual([call[0] for call in self.calls], [
            'disable_brightness',
            '_disable_lighting',
            'set_device_mode',
            '_restore_lighting',
            '_restore_effects',
            'restore_brightness',
        ])
        self.assertFalse(self.device.disable_notify)
        self.assertFalse(self.device.disable_persistence)

    def test_system_resume_restores_mode_brightness_and_effect(self):
        self.device.suspend_device()
        self.device.resume_device()

        self.assertEqual([call[0] for call in self.calls], [
            'disable_brightness',
            '_disable_lighting',
            '_suspend_device',
            '_resume_device',
            'set_device_mode',
            '_restore_lighting',
            '_restore_effects',
            'restore_brightness',
        ])
        self.assertEqual(self.calls[4][1], (0x03, 0x00))
        self.assertEqual(self.calls[6][1], ({'backlight'},))

    def test_runtime_effect_marks_only_its_zone_for_resume(self):
        self.device._effect_restore_zones.clear()
        self.device._persisted_effect_state['logo'] = {'effect': 'static'}
        self.device.persistence = types.SimpleNamespace(status={"changed": False})
        self.device.zone = {
            'backlight': {'effect': 'spectrum'},
            'logo': {'effect': 'spectrum'},
        }

        self.device.set_persistence('logo', 'effect', 'breathMono')

        self.assertEqual(self.device._effect_restore_zones, {'logo'})
        self.assertNotIn('logo', self.device._persisted_effect_state)

        with self.device._suppress_state_updates():
            self.device.set_persistence('backlight', 'effect', 'static')

        self.assertEqual(self.device._effect_restore_zones, {'logo'})

    @unittest.mock.patch.object(RazerDevice, 'load_methods')
    @unittest.mock.patch.object(RazerDevice, 'add_dbus_method')
    @unittest.mock.patch.object(RazerDevice, 'set_device_mode')
    @unittest.mock.patch.object(RazerDevice, 'get_serial', return_value='TEST')
    @unittest.mock.patch(
        'openrazer_daemon.hardware.device_base.DBusService.__init__',
        return_value=None,
    )
    def test_restore_disabled_uses_supported_default_at_startup_and_resume(
            self, _dbus_init, _get_serial, set_device_mode, _add_dbus_method, _load_methods):
        class DriverModeDevice(RazerDevice):
            USB_VID = 0x1532
            USB_PID = 0xffff
            DRIVER_MODE = True
            METHODS = ['bw_set_static']
            effect_calls = []

            def setStatic(self):
                self.effect_calls.append('static')

        config = configparser.ConfigParser()
        config['Startup'] = {
            'restore_persistence': 'false',
            'persistence_dual_boot_quirk': 'false',
        }
        persistence = configparser.ConfigParser()
        persistence['TEST'] = {
            'backlight_active': 'false',
            'backlight_brightness': '25',
            'backlight_effect': 'pulsate',
            'backlight_colors': '1 2 3 4 5 6 7 8 9',
            'backlight_speed': '3',
            'backlight_wave_dir': '2',
        }

        DriverModeDevice.effect_calls = []
        device = DriverModeDevice('/missing', 1, config, persistence, True, [], [], {})

        self.assertEqual(device.zone['backlight']['effect'], 'static')
        self.assertEqual(
            device.zone['backlight']['colors'],
            [0, 255, 0, 0, 255, 255, 0, 0, 255],
        )
        self.assertEqual(device.zone['backlight']['speed'], 1)
        self.assertEqual(device.zone['backlight']['wave_dir'], 1)
        self.assertFalse(device.zone['backlight']['active'])
        self.assertEqual(device.zone['backlight']['brightness'], 25)
        self.assertEqual(device._effect_restore_zones, {'backlight'})
        self.assertEqual(device.get_persistence_effect_state('backlight'), {
            'effect': 'pulsate',
            'colors': [1, 2, 3, 4, 5, 6, 7, 8, 9],
            'speed': 3,
            'wave_dir': 2,
        })
        self.assertEqual(device.effect_calls, ['static'])

        device.effect_calls = []
        device.set_device_mode = unittest.mock.MagicMock()
        device.resume_device()

        self.assertEqual(device.effect_calls, ['static'])
        set_device_mode.assert_called_once_with(0x03, 0x00)

    def test_driver_mode_timeout_does_not_abort_resume(self):
        self.device.set_device_mode = unittest.mock.MagicMock(side_effect=TimeoutError('asleep'))

        successful = self.device.resume_device()

        self.assertEqual([call[0] for call in self.calls], [
            '_resume_device',
            '_restore_lighting',
            '_restore_effects',
            'restore_brightness',
        ])
        self.assertFalse(successful)
        self.device.logger.warning.assert_called_once()

    def test_resume_reports_brightness_failure(self):
        self.device.restore_brightness = unittest.mock.MagicMock(return_value=False)

        successful = self.device.resume_device()

        self.assertFalse(successful)

    def test_resume_applies_active_state_after_effect(self):
        self.device.DRIVER_MODE = False
        self.device._effect_restore_zones = {'logo'}
        self.device.zone = {'logo': {'active': False}}
        self.device.hardware_active = False

        def restore_effects(zones):
            self.assertEqual(zones, {'logo'})
            self.device.hardware_active = True

        def restore_brightness():
            self.device.hardware_active = self.device.zone['logo']['active']
            return True

        self.device._restore_effects = restore_effects
        self.device.restore_brightness = restore_brightness

        self.device.resume_device()

        self.assertFalse(self.device.hardware_active)

    def test_brightness_runs_when_effect_restore_fails(self):
        self.device._restore_effects = unittest.mock.MagicMock(side_effect=OSError('failed'))

        self.assertFalse(self.device.resume_device())

        self.assertIn(('restore_brightness', ()), self.calls)
        self.assertFalse(self.device.disable_notify)
        self.assertFalse(self.device.disable_persistence)

    def test_restore_effects_skips_stale_zones(self):
        self.device.ZONES = ('backlight', 'logo')
        self.device.zone = {
            'backlight': {
                'present': True,
                'effect': 'spectrum',
                'colors': [0] * 9,
                'speed': 1,
                'wave_dir': 1,
            },
            'logo': {
                'present': True,
                'effect': 'spectrum',
                'colors': [0] * 9,
                'speed': 1,
                'wave_dir': 1,
            },
        }
        restored = []
        self.device.setSpectrum = lambda: restored.append('backlight')
        self.device.setLogoSpectrum = lambda: restored.append('logo')

        RazerDevice._restore_effects(self.device, {'logo'})

        self.assertEqual(restored, ['logo'])

    def test_temporary_lighting_change_preserves_configured_brightness(self):
        self.device.zone = {'backlight': {'brightness': 0}}
        self.device.hardware_brightness = 0

        def disable_brightness():
            self.device.hardware_brightness = 0
            if not self.device.disable_persistence:
                self.device.zone['backlight']['brightness'] = 0

        def restore_brightness():
            self.device.hardware_brightness = self.device.zone['backlight']['brightness']

        self.device.disable_brightness = disable_brightness
        self.device.restore_brightness = restore_brightness

        for brightness in (0, 75):
            with self.subTest(brightness=brightness):
                self.device.zone['backlight']['brightness'] = brightness
                self.device.hardware_brightness = 50

                self.device.disable_lighting()
                self.device.restore_lighting()

                self.assertEqual(self.device.zone['backlight']['brightness'], brightness)
                self.assertEqual(self.device.hardware_brightness, brightness)

    def test_state_suppression_is_restored_after_error(self):
        self.device.disable_brightness = unittest.mock.MagicMock(side_effect=OSError)

        self.assertFalse(self.device.disable_lighting())

        self.assertFalse(self.device.disable_notify)
        self.assertFalse(self.device.disable_persistence)


class KrakenPowerTest(unittest.TestCase):
    @unittest.mock.patch.object(headsets._dbus_chroma, 'set_none_effect')
    def test_system_resume_restores_kraken_effect_once(self, set_none_effect):
        for restore_zones in (set(), {'backlight'}):
            with self.subTest(restore_zones=restore_zones):
                device = object.__new__(RazerKraken71V2)
                device.logger = unittest.mock.MagicMock()
                device._is_closed = True
                device._disable_notifications = False
                device._disable_persistence = False
                device._effect_restore_zones = restore_zones
                device._lighting_state = 'software'
                device._lighting_state_applied = 'software'
                device._system_suspended = False
                device._lighting_restore_source = None
                device.DRIVER_MODE = False
                device.ZONES = ('backlight',)
                device.suspend_args = {}
                device.zone = {
                    'backlight': {
                        'present': True,
                        'active': True,
                        'brightness': 75,
                        'effect': 'breathDual',
                        'colors': [1, 2, 3, 4, 5, 6, 0, 0, 0],
                        'speed': 1,
                        'wave_dir': 1,
                    },
                }
                device.disable_brightness = unittest.mock.MagicMock()
                device.restore_brightness = unittest.mock.MagicMock()
                device._suspend_device = unittest.mock.MagicMock()
                device._resume_device = unittest.mock.MagicMock()

                with unittest.mock.patch.object(
                        headsets._dbus_chroma, 'set_breath_dual_effect') as set_breath_dual:
                    device.suspend_device()
                    device.resume_device()

                set_breath_dual.assert_called_once_with(device, 1, 2, 3, 4, 5, 6)

        self.assertEqual(set_none_effect.call_count, 2)


class ScreensaverMonitorTest(unittest.TestCase):
    @unittest.mock.patch('openrazer_daemon.misc.screensaver_monitor.dbus.SessionBus')
    def setUp(self, session_bus):
        self.parent = unittest.mock.MagicMock()
        self.bus = session_bus.return_value
        self.bus.name_has_owner.return_value = False
        self.monitor = ScreensaverMonitor(self.parent)

    def test_registers_supported_screensaver_interfaces(self):
        self.assertEqual(self.bus.add_signal_receiver.call_count, 2 * len(DBUS_SCREENSAVER_INTERFACES))

    def test_reports_state_without_running_system_hooks(self):
        self.assertIsNone(self.monitor.active)
        self.monitor.signal_callback(False)
        self.assertFalse(self.monitor.active)
        self.monitor.signal_callback(True)
        self.assertTrue(self.monitor.active)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 2)
        self.parent.suspend_devices.assert_not_called()
        self.parent.resume_devices.assert_not_called()

    def test_repeated_signal_can_retry_failed_device_transition(self):
        self.monitor.signal_callback(False)
        self.monitor.signal_callback(False)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 2)

    def test_monitoring_change_applies_current_state(self):
        self.monitor.monitoring = False
        self.monitor.signal_callback(True)
        self.assertTrue(self.monitor.active)
        self.assertFalse(self.monitor.monitoring)
        self.monitor.monitoring = True
        self.assertTrue(self.monitor.monitoring)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 3)

    def test_reads_initial_active_state_without_starting_service(self):
        self.bus.name_has_owner.side_effect = lambda name: name == 'org.gnome.ScreenSaver'
        self.bus.get_object.return_value.GetActive.return_value = True
        self.monitor.refresh()
        self.assertTrue(self.monitor.active)
        self.bus.get_object.assert_called_once_with(
            'org.gnome.ScreenSaver', '/org/gnome/ScreenSaver', introspect=False)

    def test_tracks_multiple_interfaces(self):
        self.monitor.signal_callback(True, 'org.gnome.ScreenSaver')
        self.monitor.signal_callback(False, 'org.freedesktop.ScreenSaver')
        self.assertTrue(self.monitor.active)

    def test_query_timeout_preserves_known_lock_state(self):
        self.monitor.signal_callback(True, 'org.gnome.ScreenSaver')
        self.parent.reset_mock()
        self.bus.name_has_owner.side_effect = lambda name: name == 'org.gnome.ScreenSaver'
        self.bus.get_object.return_value.GetActive.side_effect = dbus.exceptions.DBusException('timed out')

        self.monitor.refresh()

        self.assertTrue(self.monitor.active)
        self.parent.apply_lighting_policy.assert_not_called()
        self.bus.get_object.return_value.GetActive.assert_called_once_with(
            dbus_interface='org.gnome.ScreenSaver', timeout=1.0)

    def test_bus_query_error_preserves_known_lock_state(self):
        self.monitor.signal_callback(True, 'org.gnome.ScreenSaver')
        self.parent.reset_mock()
        self.bus.name_has_owner.side_effect = dbus.exceptions.DBusException('bus unavailable')

        self.monitor.refresh()

        self.assertTrue(self.monitor.active)
        self.parent.apply_lighting_policy.assert_not_called()

    def test_service_disappearance_clears_stale_lock(self):
        self.monitor.signal_callback(True)
        self.monitor._owner_changed('org.gnome.ScreenSaver', ':1.2', '')
        self.assertIsNone(self.monitor.active)

    def test_close_removes_receivers_and_ignores_late_signals(self):
        matches = list(self.monitor._matches)
        self.monitor.close()
        self.parent.reset_mock()
        self.monitor.signal_callback(True)
        self.monitor.refresh()
        self.parent.apply_lighting_policy.assert_not_called()
        for match in matches:
            match.remove.assert_called()


class SystemSleepMonitorTest(unittest.TestCase):
    def setUp(self):
        system_bus_patcher = unittest.mock.patch('openrazer_daemon.misc.system_sleep_monitor.dbus.SystemBus')
        interface_patcher = unittest.mock.patch('openrazer_daemon.misc.system_sleep_monitor.dbus.Interface')
        close_patcher = unittest.mock.patch('openrazer_daemon.misc.system_sleep_monitor.os.close')
        timeout_add_patcher = unittest.mock.patch('openrazer_daemon.misc.system_sleep_monitor.GLib.timeout_add')
        source_remove_patcher = unittest.mock.patch('openrazer_daemon.misc.system_sleep_monitor.GLib.source_remove')
        logger_patcher = unittest.mock.patch('openrazer_daemon.misc.system_sleep_monitor.logging.getLogger')
        self.addCleanup(system_bus_patcher.stop)
        self.addCleanup(interface_patcher.stop)
        self.addCleanup(close_patcher.stop)
        self.addCleanup(timeout_add_patcher.stop)
        self.addCleanup(source_remove_patcher.stop)
        self.addCleanup(logger_patcher.stop)
        self.system_bus = system_bus_patcher.start()
        self.interface = interface_patcher.start()
        self.close = close_patcher.start()
        self.timeout_add = timeout_add_patcher.start()
        self.source_remove = source_remove_patcher.start()
        self.logger = logger_patcher.start().return_value
        self.bus = self.system_bus.return_value
        self.manager = self.interface.return_value
        self.inhibitor = self.manager.Inhibit.return_value
        self.inhibitor.take.return_value = 42
        self.timeout_add.return_value = 73

    def test_handles_each_sleep_transition_once(self):
        parent = unittest.mock.MagicMock()
        monitor = SystemSleepMonitor(parent)

        self.bus.get_object.assert_called_once_with(LOGIN1_BUS_NAME, LOGIN1_PATH)
        self.interface.assert_called_once_with(
            self.bus.get_object.return_value,
            dbus_interface=LOGIN1_INTERFACE,
        )
        self.bus.add_signal_receiver.assert_called_once_with(
            monitor.signal_callback,
            signal_name='PrepareForSleep',
            dbus_interface=LOGIN1_INTERFACE,
            bus_name=LOGIN1_BUS_NAME,
            path=LOGIN1_PATH,
        )
        self.manager.Inhibit.assert_called_once_with(
            'sleep',
            'OpenRazer',
            'Prepare Razer devices for sleep',
            'delay',
        )

        monitor.signal_callback(False)
        parent.resume_devices.assert_not_called()

        monitor.signal_callback(True)
        monitor.signal_callback(True)
        parent.suspend_devices.assert_called_once_with()
        self.close.assert_called_once_with(42)

        monitor.signal_callback(False)
        monitor.signal_callback(False)
        parent.resume_devices.assert_called_once_with()
        self.assertEqual(self.manager.Inhibit.call_count, 2)

    def test_releases_inhibitor_when_suspend_preparation_fails(self):
        parent = unittest.mock.MagicMock()
        parent.suspend_devices.side_effect = RuntimeError('failed')
        monitor = SystemSleepMonitor(parent)

        with self.assertRaises(RuntimeError):
            monitor.signal_callback(True)

        self.close.assert_called_once_with(42)
        self.assertIsNone(monitor._inhibitor_fd)

    def test_inhibitor_failure_falls_back_to_unprotected_notifications(self):
        self.manager.Inhibit.side_effect = dbus.exceptions.DBusException('denied')
        parent = unittest.mock.MagicMock()

        monitor = SystemSleepMonitor(parent)
        monitor.signal_callback(True)
        monitor.signal_callback(False)

        parent.suspend_devices.assert_called_once_with()
        parent.resume_devices.assert_called_once_with()
        self.assertEqual(self.manager.Inhibit.call_count, 2)
        self.close.assert_not_called()

    def test_duplicate_awake_signal_retries_missing_inhibitor(self):
        self.manager.Inhibit.side_effect = [
            dbus.exceptions.DBusException('unavailable'),
            self.inhibitor,
        ]
        parent = unittest.mock.MagicMock()

        monitor = SystemSleepMonitor(parent)
        monitor.signal_callback(False)

        self.assertEqual(self.manager.Inhibit.call_count, 2)
        self.assertEqual(monitor._inhibitor_fd, 42)
        parent.resume_devices.assert_not_called()

    def test_close_releases_inhibitor_once(self):
        monitor = SystemSleepMonitor(unittest.mock.MagicMock())

        monitor.close()
        monitor.close()

        self.close.assert_called_once_with(42)

        monitor.signal_callback(True)
        monitor.signal_callback(False)
        self.assertEqual(self.manager.Inhibit.call_count, 1)

    def test_failed_resume_is_retried_within_bounded_window(self):
        parent = unittest.mock.MagicMock()
        parent.resume_devices.side_effect = [False, False, True]
        monitor = SystemSleepMonitor(parent)

        monitor.signal_callback(True)
        monitor.signal_callback(False)

        self.assertFalse(monitor._retry_resume())
        self.assertFalse(monitor._retry_resume())

        self.assertEqual(parent.resume_devices.call_count, 3)
        self.assertEqual(self.timeout_add.call_args_list, [
            unittest.mock.call(RESUME_RETRY_DELAYS_MS[0], monitor._retry_resume),
            unittest.mock.call(RESUME_RETRY_DELAYS_MS[1], monitor._retry_resume),
        ])
        self.assertEqual(monitor._resume_retry_index, 0)

    def test_failed_resume_stops_after_retry_window(self):
        parent = unittest.mock.MagicMock()
        parent.resume_devices.return_value = False
        monitor = SystemSleepMonitor(parent)

        monitor.signal_callback(True)
        monitor.signal_callback(False)
        self.assertFalse(monitor._retry_resume())
        self.assertFalse(monitor._retry_resume())

        self.assertEqual(parent.resume_devices.call_count, 3)
        self.assertEqual(self.timeout_add.call_count, 2)
        self.logger.warning.assert_called_once()

    def test_pending_resume_retry_is_cancelled_before_sleep(self):
        parent = unittest.mock.MagicMock()
        parent.resume_devices.return_value = False
        monitor = SystemSleepMonitor(parent)

        monitor.signal_callback(True)
        monitor.signal_callback(False)
        monitor.signal_callback(True)

        self.source_remove.assert_called_once_with(73)
        self.assertIsNone(monitor._resume_retry_source)
        self.assertEqual(monitor._resume_retry_index, 0)

    def test_close_cancels_pending_resume_retry(self):
        parent = unittest.mock.MagicMock()
        parent.resume_devices.return_value = False
        monitor = SystemSleepMonitor(parent)

        monitor.signal_callback(True)
        monitor.signal_callback(False)
        monitor.close()

        self.source_remove.assert_called_once_with(73)
        self.assertFalse(monitor._retry_resume())
        self.assertEqual(parent.resume_devices.call_count, 1)


class DaemonPowerTest(unittest.TestCase):
    @unittest.mock.patch('builtins.open', new_callable=unittest.mock.mock_open)
    def test_write_persistence_preserves_unrestored_effect(self, _open):
        daemon = object.__new__(RazerDaemon)
        daemon.logger = unittest.mock.MagicMock()
        daemon._persistence = configparser.ConfigParser()
        effect_state = {
            'effect': 'static',
            'colors': [1, 2, 3, 4, 5, 6, 7, 8, 9],
            'speed': 3,
            'wave_dir': 2,
        }
        device = types.SimpleNamespace(
            storage_name='TEST',
            METHODS=[],
            ZONES=('backlight',),
            zone={
                'backlight': {
                    'present': True,
                    'active': False,
                    'brightness': 25,
                    'effect': 'spectrum',
                    'colors': [0, 255, 0, 0, 255, 255, 0, 0, 255],
                    'speed': 1,
                    'wave_dir': 1,
                },
            },
            get_persistence_effect_state=unittest.mock.MagicMock(return_value=effect_state),
        )
        daemon._razer_devices = [types.SimpleNamespace(dbus=device)]

        daemon.write_persistence('/persistence.conf')

        section = daemon._persistence['TEST']
        self.assertEqual(section['backlight_active'], 'False')
        self.assertEqual(section['backlight_brightness'], '25')
        self.assertEqual(section['backlight_effect'], 'static')
        self.assertEqual(section['backlight_colors'], '1 2 3 4 5 6 7 8 9')
        self.assertEqual(section['backlight_speed'], '3')
        self.assertEqual(section['backlight_wave_dir'], '2')

    def test_resume_refreshes_policy_before_touching_devices(self):
        daemon = object.__new__(RazerDaemon)
        device = unittest.mock.MagicMock()
        daemon.logger = unittest.mock.MagicMock()
        daemon._razer_devices = [types.SimpleNamespace(dbus=device, serial='device')]
        daemon._screensaver_monitor = unittest.mock.MagicMock()
        daemon._lighting_power_monitor = unittest.mock.MagicMock()
        daemon.apply_lighting_policy = unittest.mock.MagicMock()
        calls = unittest.mock.MagicMock()
        calls.attach_mock(daemon._screensaver_monitor.refresh, 'screen')
        calls.attach_mock(daemon._lighting_power_monitor.refresh, 'power')
        calls.attach_mock(daemon.apply_lighting_policy, 'policy')
        calls.attach_mock(device.resume_device, 'resume')

        daemon.resume_devices()

        self.assertEqual(calls.mock_calls, [
            unittest.mock.call.screen(), unittest.mock.call.power(),
            unittest.mock.call.policy(), unittest.mock.call.resume(),
        ])

    def test_resume_continues_after_device_timeout(self):
        daemon = object.__new__(RazerDaemon)
        daemon.logger = unittest.mock.MagicMock()
        timed_out = unittest.mock.MagicMock()
        timed_out.resume_device.side_effect = TimeoutError('asleep')
        resumed = unittest.mock.MagicMock()
        daemon._razer_devices = [
            types.SimpleNamespace(dbus=timed_out, serial='asleep'),
            types.SimpleNamespace(dbus=resumed, serial='awake'),
        ]
        daemon._screensaver_monitor = unittest.mock.MagicMock()
        daemon._lighting_power_monitor = None
        daemon.apply_lighting_policy = unittest.mock.MagicMock()

        successful = daemon.resume_devices()

        self.assertFalse(successful)
        resumed.resume_device.assert_called_once_with()
        daemon.apply_lighting_policy.assert_called_once_with()
        daemon.logger.warning.assert_called_once()

    def test_resume_updates_policy_even_when_device_raises(self):
        daemon = object.__new__(RazerDaemon)
        daemon.logger = unittest.mock.MagicMock()
        device = unittest.mock.MagicMock()
        device.resume_device.side_effect = RuntimeError('bug')
        daemon._razer_devices = [types.SimpleNamespace(dbus=device, serial='device')]
        daemon._screensaver_monitor = unittest.mock.MagicMock()
        daemon._lighting_power_monitor = None
        daemon.apply_lighting_policy = unittest.mock.MagicMock()

        with self.assertRaises(RuntimeError):
            daemon.resume_devices()

        daemon.apply_lighting_policy.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
