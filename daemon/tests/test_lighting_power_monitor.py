# SPDX-License-Identifier: GPL-2.0-or-later

import unittest
from unittest.mock import MagicMock, patch

import dbus

from openrazer_daemon.misc.lighting_power_monitor import (
    DBUS_TIMEOUT,
    DISPLAY_BUS_NAME,
    DISPLAY_PATH,
    LightingPowerMonitor,
    LOGIN1_BUS_NAME,
    LOGIN1_PATH,
    LOGIN1_SESSION_INTERFACE,
    UPOWER_BUS_NAME,
    UPOWER_PATH,
)


class LightingPowerMonitorTest(unittest.TestCase):
    def setUp(self):
        self.parent = MagicMock()
        self.system_bus = MagicMock()
        self.session_bus = MagicMock()
        self.matches = []
        for bus in (self.system_bus, self.session_bus):
            bus.get_object.side_effect = self.get_object
            bus.add_signal_receiver.side_effect = self.add_signal_receiver
        self.objects = {}
        self.upower = self.add_object(UPOWER_BUS_NAME, UPOWER_PATH, {
            'LidIsPresent': True,
            'LidIsClosed': False,
        })
        self.display = self.add_object(DISPLAY_BUS_NAME, DISPLAY_PATH, {
            'PowerSaveMode': 0,
        })
        self.login1 = self.add_object(LOGIN1_BUS_NAME, LOGIN1_PATH, {})
        self.login1.ListSessions.return_value = []

        for target, value in (
                ('dbus.SystemBus', self.system_bus),
                ('dbus.SessionBus', self.session_bus),
                ('os.getuid', 1000)):
            patcher = patch('openrazer_daemon.misc.lighting_power_monitor.' + target,
                            return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('openrazer_daemon.misc.lighting_power_monitor.dbus.Interface',
                        side_effect=lambda proxy, _interface: proxy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_signal_receiver(self, *_args, **_kwargs):
        match = MagicMock()
        self.matches.append(match)
        return match

    def add_object(self, name, path, properties):
        proxy = MagicMock()
        proxy.GetAll.return_value = properties
        self.objects[name, path] = proxy
        return proxy

    def get_object(self, name, path, **_kwargs):
        try:
            return self.objects[name, path]
        except KeyError:
            raise dbus.exceptions.DBusException('Service or object unavailable') from None

    def add_session(self, session_id='1', uid=1000, seat='seat1', **properties):
        path = '/org/freedesktop/login1/session/' + session_id
        self.login1.ListSessions.return_value.append((session_id, uid, 'user', seat, path))
        return self.add_object(LOGIN1_BUS_NAME, path, {
            'Type': 'wayland', 'Remote': False, 'Active': True, **properties,
        })

    def test_initial_state_does_not_apply_before_device_initialization(self):
        self.upower.GetAll.return_value['LidIsClosed'] = True
        self.display.GetAll.return_value['PowerSaveMode'] = 3
        self.add_session()

        monitor = LightingPowerMonitor(self.parent)

        self.assertTrue(monitor.lid_closed)
        self.assertTrue(monitor.display_off)
        self.assertTrue(monitor.session_active)
        self.parent.apply_lighting_policy.assert_not_called()

    def test_graphical_lock_does_not_mean_session_inactive(self):
        self.add_session(LockedHint=True)
        monitor = LightingPowerMonitor(self.parent)

        monitor._session_changed(LOGIN1_SESSION_INTERFACE, {'LockedHint': True}, [])

        self.assertTrue(monitor.session_active)
        self.parent.apply_lighting_policy.assert_not_called()

    def test_remote_tty_other_users_and_seatless_sessions_do_not_own_lighting(self):
        self.add_session('remote', Remote=True)
        self.add_session('tty', Type='tty')
        self.add_session('other', uid=1001)
        self.add_session('seatless', seat='')
        self.add_session('inactive', Active=False)

        monitor = LightingPowerMonitor(self.parent)

        self.assertFalse(monitor.session_active)

    def test_local_session_arrival_switch_and_removal(self):
        monitor = LightingPowerMonitor(self.parent)
        session = self.add_session()
        monitor._sessions_changed('1', '/session/1')
        self.assertTrue(monitor.session_active)

        session.GetAll.return_value['Active'] = False
        monitor._session_changed(LOGIN1_SESSION_INTERFACE, {}, ['Active'])
        self.assertFalse(monitor.session_active)

        session.GetAll.return_value['Active'] = True
        monitor._sessions_changed()
        self.login1.ListSessions.return_value.clear()
        monitor._sessions_changed('1', '/session/1')
        self.assertFalse(monitor.session_active)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 4)

    def test_optional_sources_are_unknown_when_unavailable(self):
        self.objects.clear()

        monitor = LightingPowerMonitor(self.parent)

        self.assertFalse(monitor.lid_closed)
        self.assertIsNone(monitor.display_off)
        self.assertIsNone(monitor.session_active)

    def test_failed_session_read_does_not_claim_no_local_session(self):
        session = self.add_session()
        session.GetAll.side_effect = dbus.exceptions.DBusException('temporarily unavailable')

        monitor = LightingPowerMonitor(self.parent)

        self.assertIsNone(monitor.session_active)

    def test_valid_active_session_wins_over_unavailable_other_session(self):
        session = self.add_session('unavailable')
        session.GetAll.side_effect = dbus.exceptions.DBusException('gone')
        self.add_session('active')

        monitor = LightingPowerMonitor(self.parent)

        self.assertTrue(monitor.session_active)

    def test_display_modes_and_invalidated_property(self):
        monitor = LightingPowerMonitor(self.parent)
        for mode, expected in ((1, True), (2, True), (3, True), (-1, None), (0, False)):
            with self.subTest(mode=mode):
                self.display.GetAll.return_value['PowerSaveMode'] = mode
                monitor._display_changed(DISPLAY_BUS_NAME, {}, ['PowerSaveMode'])
                self.assertIs(monitor.display_off, expected)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 5)

    def test_lid_events_require_a_present_lid(self):
        monitor = LightingPowerMonitor(self.parent)
        self.upower.GetAll.return_value['LidIsClosed'] = True
        monitor._lid_changed(UPOWER_BUS_NAME, {'LidIsClosed': True}, [])
        self.assertTrue(monitor.lid_closed)

        self.upower.GetAll.return_value['LidIsPresent'] = False
        monitor._lid_changed(UPOWER_BUS_NAME, {}, ['LidIsPresent'])
        self.assertFalse(monitor.lid_closed)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 2)

    def test_service_disappearance_and_late_arrival(self):
        self.add_session()
        monitor = LightingPowerMonitor(self.parent)
        monitor._owner_changed(DISPLAY_BUS_NAME, ':1.1', '')
        self.assertIsNone(monitor.display_off)
        monitor._owner_changed(LOGIN1_BUS_NAME, ':1.2', '')
        self.assertIsNone(monitor.session_active)

        self.display.GetAll.return_value['PowerSaveMode'] = 3
        monitor._owner_changed(DISPLAY_BUS_NAME, '', ':1.3')
        monitor._owner_changed(LOGIN1_BUS_NAME, '', ':1.4')
        self.assertTrue(monitor.display_off)
        self.assertTrue(monitor.session_active)

    def test_wake_refresh_reads_all_inputs_and_retries_unchanged_state(self):
        monitor = LightingPowerMonitor(self.parent)
        self.upower.GetAll.return_value['LidIsClosed'] = True
        self.display.GetAll.return_value['PowerSaveMode'] = 3
        self.add_session()

        monitor.refresh()
        monitor.refresh()

        self.assertTrue(monitor.lid_closed)
        self.assertTrue(monitor.display_off)
        self.assertTrue(monitor.session_active)
        self.assertEqual(self.parent.apply_lighting_policy.call_count, 2)

    def test_query_timeouts_preserve_known_policy_inputs(self):
        self.upower.GetAll.return_value['LidIsClosed'] = True
        self.display.GetAll.return_value['PowerSaveMode'] = 3
        self.add_session()
        monitor = LightingPowerMonitor(self.parent)
        self.upower.GetAll.assert_called_with(UPOWER_BUS_NAME, timeout=DBUS_TIMEOUT)
        self.display.GetAll.assert_called_with(DISPLAY_BUS_NAME, timeout=DBUS_TIMEOUT)
        self.login1.ListSessions.assert_called_with(timeout=DBUS_TIMEOUT)
        for method in (self.upower.GetAll, self.display.GetAll, self.login1.ListSessions):
            method.side_effect = dbus.exceptions.DBusException('timed out')

        monitor.refresh()

        self.assertTrue(monitor.lid_closed)
        self.assertTrue(monitor.display_off)
        self.assertTrue(monitor.session_active)
        self.parent.apply_lighting_policy.assert_called_once_with()

    def test_session_property_timeout_preserves_known_state(self):
        session = self.add_session()
        monitor = LightingPowerMonitor(self.parent)
        session.GetAll.side_effect = dbus.exceptions.DBusException('timed out')

        monitor._sessions_changed()

        self.assertTrue(monitor.session_active)
        self.parent.apply_lighting_policy.assert_called_once_with()

    def test_repeated_input_signals_can_retry_failed_device_transition(self):
        monitor = LightingPowerMonitor(self.parent)

        for _ in range(2):
            monitor._lid_changed(UPOWER_BUS_NAME, {'LidIsClosed': False}, [])
            monitor._display_changed(DISPLAY_BUS_NAME, {'PowerSaveMode': 0}, [])
            monitor._sessions_changed()

        self.assertEqual(self.parent.apply_lighting_policy.call_count, 6)

    def test_unrelated_properties_do_not_requery(self):
        monitor = LightingPowerMonitor(self.parent)
        self.upower.GetAll.reset_mock()
        self.display.GetAll.reset_mock()
        self.login1.ListSessions.reset_mock()

        monitor._lid_changed(UPOWER_BUS_NAME, {'OnBattery': True}, [])
        monitor._display_changed(DISPLAY_BUS_NAME, {'NightLightSupported': True}, [])
        monitor._session_changed(LOGIN1_SESSION_INTERFACE, {'LockedHint': True}, [])

        self.upower.GetAll.assert_not_called()
        self.display.GetAll.assert_not_called()
        self.login1.ListSessions.assert_not_called()

    def test_close_removes_signals_and_prevents_further_queries(self):
        monitor = LightingPowerMonitor(self.parent)
        monitor.close()
        monitor.close()
        self.upower.GetAll.reset_mock()
        self.display.GetAll.reset_mock()
        self.login1.ListSessions.reset_mock()

        monitor.refresh()
        monitor._lid_changed(UPOWER_BUS_NAME, {'LidIsClosed': True}, [])
        monitor._display_changed(DISPLAY_BUS_NAME, {'PowerSaveMode': 3}, [])
        monitor._sessions_changed()
        monitor._owner_changed(UPOWER_BUS_NAME, '', ':1.1')

        for match in self.matches:
            match.remove.assert_called_once_with()
        self.upower.GetAll.assert_not_called()
        self.display.GetAll.assert_not_called()
        self.login1.ListSessions.assert_not_called()
        self.parent.apply_lighting_policy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
