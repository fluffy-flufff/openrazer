# SPDX-License-Identifier: GPL-2.0-or-later

import copy
import configparser
import types
import unittest
from unittest.mock import Mock, patch

from openrazer_daemon.hardware.device_base import RazerDevice
from openrazer_daemon.hardware.keyboards import _RippleKeyboard


class LightingStateTest(unittest.TestCase):
    def make_keyboard(self, effect='wheel'):
        keyboard = object.__new__(_RippleKeyboard)
        keyboard.logger = Mock()
        keyboard.serial = 'TEST'
        keyboard._is_closed = False
        keyboard._lighting_state = 'software'
        keyboard._lighting_state_applied = 'software'
        keyboard._system_suspended = False
        keyboard._lighting_restore_source = None
        keyboard._disable_notifications = False
        keyboard._disable_persistence = False
        keyboard._effect_restore_zones = {'backlight', 'logo'}
        keyboard._persisted_effect_state = {}
        keyboard._observer_list = []
        keyboard._effect_sync_propagate_up = False
        keyboard._parent = None
        keyboard._battery_manager = None
        keyboard.SOFTWARE_WHEEL = True
        keyboard.persistence = types.SimpleNamespace(status={'changed': False})
        keyboard.zone = {
            'backlight': {'effect': effect, 'wave_dir': 2, 'colors': [1, 2, 3], 'brightness': 75},
            'logo': {'effect': 'on', 'active': True},
        }
        keyboard.events = []

        def record(name, result=None):
            def callback(*args):
                keyboard.events.append((name, *args))
                return result
            return callback

        keyboard.ripple_manager = Mock()
        keyboard.ripple_manager._ripple_thread._refresh_rate = 0.04
        keyboard.ripple_manager.suspend.side_effect = record('pause')
        keyboard.ripple_manager.resume.side_effect = record('resume')
        keyboard.ripple_manager.close.side_effect = record('stop_renderer')
        keyboard.key_manager = Mock()
        keyboard.key_manager.close.side_effect = record('stop_keys')
        keyboard.set_device_mode = Mock(side_effect=record('mode'))
        keyboard.disable_brightness = Mock(side_effect=record('dark'))
        keyboard.restore_brightness = Mock(side_effect=record('brightness', True))
        keyboard._disable_lighting = Mock(side_effect=record('disable_hook'))
        keyboard._restore_lighting = Mock(side_effect=record('restore_hook', set()))
        keyboard._restore_effects = Mock(side_effect=record('effects'))
        keyboard._suspend_device = Mock(side_effect=record('suspend_hook'))
        keyboard._resume_device = Mock(side_effect=record('resume_hook'))

        def spectrum():
            keyboard.events.append(('spectrum',))
            keyboard.send_effect_event('backlight', 'setSpectrum')
            keyboard.set_persistence('backlight', 'effect', 'spectrum')

        keyboard.setSpectrum = spectrum
        keyboard.setStatic = Mock(side_effect=record('static'))
        self.addCleanup(setattr, keyboard, '_is_closed', True)
        return keyboard

    def test_handoff_preserves_profile_and_restores_wheel_after_brightness(self):
        keyboard = self.make_keyboard()
        profile = copy.deepcopy(keyboard.zone)

        self.assertTrue(keyboard.set_lighting_state('hardware'))
        self.assertEqual(keyboard.events, [
            ('pause', 'lighting'), ('restore_hook',), ('spectrum',),
            ('effects', {'logo'}), ('mode', 0, 0), ('brightness',),
        ])
        keyboard.ripple_manager.resume.assert_not_called()
        keyboard.events.clear()

        self.assertTrue(keyboard.set_lighting_state('software'))
        self.assertEqual(keyboard.events, [
            ('pause', 'lighting'), ('mode', 3, 0), ('restore_hook',),
            ('effects', {'backlight', 'logo'}), ('brightness',), ('resume', 'lighting'),
        ])
        keyboard.ripple_manager.notify.assert_called_once_with(('effect', keyboard, 'backlight', 'setWheel', 2))
        self.assertEqual(keyboard.zone, profile)
        self.assertFalse(keyboard.persistence.status['changed'])
        self.assertFalse(keyboard.disable_notify)
        self.assertFalse(keyboard.disable_persistence)

    def test_off_then_hardware_never_resumes_renderer(self):
        keyboard = self.make_keyboard()

        keyboard.set_lighting_state('off')
        self.assertEqual(keyboard.events, [('pause', 'lighting'), ('dark',), ('disable_hook',)])
        keyboard.events.clear()
        keyboard.set_lighting_state('hardware')

        self.assertIn(('spectrum',), keyboard.events)
        keyboard.ripple_manager.resume.assert_not_called()

    def test_successful_state_is_idempotent_and_force_reapplies(self):
        keyboard = self.make_keyboard()
        keyboard.set_lighting_state('hardware')
        keyboard.events.clear()

        keyboard.set_lighting_state('hardware')
        self.assertEqual(keyboard.events, [])
        keyboard.set_lighting_state('hardware', force=True)
        self.assertIn(('spectrum',), keyboard.events)

    def test_failed_transition_remains_paused_and_can_retry(self):
        keyboard = self.make_keyboard()
        keyboard.set_lighting_state('hardware')
        keyboard.restore_brightness.side_effect = [False, True]

        self.assertFalse(keyboard.set_lighting_state('software'))
        self.assertIsNone(keyboard._lighting_state_applied)
        keyboard.ripple_manager.resume.assert_not_called()
        self.assertTrue(keyboard.set_lighting_state('software'))
        self.assertEqual(keyboard._lighting_state_applied, 'software')
        keyboard.ripple_manager.resume.assert_called_once_with('lighting')

    def test_write_failure_does_not_mark_state_applied(self):
        keyboard = self.make_keyboard()
        keyboard.disable_brightness.side_effect = OSError('disconnected')

        self.assertFalse(keyboard.set_lighting_state('off'))
        self.assertIsNone(keyboard._lighting_state_applied)
        self.assertEqual(keyboard._lighting_state, 'off')
        keyboard.ripple_manager.resume.assert_not_called()
        self.assertFalse(keyboard.disable_notify)
        self.assertFalse(keyboard.disable_persistence)

    def test_suspend_defers_policy_and_resume_applies_current_off_state(self):
        keyboard = self.make_keyboard()
        keyboard.suspend_device()
        keyboard.events.clear()

        self.assertTrue(keyboard.set_lighting_state('hardware'))
        self.assertTrue(keyboard.set_lighting_state('off'))
        self.assertEqual(keyboard.events, [])
        self.assertTrue(keyboard.resume_device())
        self.assertEqual(keyboard.events, [
            ('resume_hook',), ('pause', 'lighting'), ('dark',), ('disable_hook',),
        ])
        keyboard.ripple_manager.resume.assert_not_called()

    def test_resume_locked_selects_firmware_fallback_without_wheel(self):
        keyboard = self.make_keyboard()
        keyboard.suspend_device()
        keyboard.set_lighting_state('hardware')
        keyboard.events.clear()

        self.assertTrue(keyboard.resume_device())
        self.assertIn(('spectrum',), keyboard.events)
        keyboard.ripple_manager.resume.assert_not_called()

    def test_resume_hook_error_still_applies_current_lighting_policy(self):
        for state in ('software', 'off'):
            with self.subTest(state=state):
                keyboard = self.make_keyboard()
                keyboard.suspend_device()
                keyboard.set_lighting_state(state)
                keyboard._resume_device.side_effect = OSError('reset')
                keyboard.events.clear()

                with self.assertRaises(OSError):
                    keyboard.resume_device()

                self.assertEqual(keyboard._lighting_state_applied, state)
                if state == 'software':
                    self.assertIn(('brightness',), keyboard.events)
                else:
                    self.assertIn(('dark',), keyboard.events)
                    self.assertNotIn(('brightness',), keyboard.events)
                self.assertFalse(keyboard.disable_notify)
                self.assertFalse(keyboard.disable_persistence)

    def test_hardware_effect_does_not_get_replaced_by_spectrum(self):
        keyboard = self.make_keyboard(effect='wave')

        keyboard.set_lighting_state('hardware')

        self.assertNotIn(('spectrum',), keyboard.events)
        self.assertIn(('effects', {'backlight', 'logo'}), keyboard.events)

    def test_monochrome_ripple_keyboard_uses_static_fallback(self):
        keyboard = self.make_keyboard(effect='ripple')
        keyboard.SOFTWARE_WHEEL = False
        keyboard.setSpectrum = None

        keyboard.set_lighting_state('hardware')

        keyboard.setStatic.assert_called_once_with(1, 2, 3)
        self.assertEqual(keyboard.zone['backlight']['effect'], 'ripple')

    def test_ripple_is_restored_without_broadcast_or_persistence(self):
        keyboard = self.make_keyboard(effect='rippleRandomColour')
        keyboard.set_lighting_state('hardware')

        keyboard.set_lighting_state('software')

        keyboard.ripple_manager.notify.assert_called_once_with((
            'effect', keyboard, 'backlight', 'setRipple', None, None, None, 0.04,
        ))
        self.assertFalse(keyboard.persistence.status['changed'])

    @patch('openrazer_daemon.hardware.device_base.GLib.idle_add', return_value=9)
    def test_client_changes_are_reapplied_after_setter_and_coalesced(self, idle_add):
        keyboard = self.make_keyboard()
        keyboard.set_lighting_state('off')
        keyboard.events.clear()

        keyboard.send_effect_event('backlight', 'setStatic', 1, 2, 3)
        keyboard.events.append(('client_write',))
        keyboard.send_effect_event('backlight', 'setBrightness', 75)
        idle_add.assert_called_once()
        self.assertEqual(keyboard.events, [('client_write',)])
        self.assertIsNone(keyboard._lighting_state_applied)

        self.assertFalse(idle_add.call_args.args[0]())
        self.assertEqual(keyboard.events, [
            ('client_write',), ('pause', 'lighting'), ('dark',), ('disable_hook',),
        ])
        self.assertIsNone(keyboard._lighting_restore_source)

    @patch('openrazer_daemon.hardware.device_base.GLib.idle_add', return_value=9)
    def test_synced_effects_also_reapply_locked_state(self, idle_add):
        keyboard = self.make_keyboard()
        keyboard.set_lighting_state('hardware')

        keyboard.notify(('effect', object(), 'backlight', 'setStatic', 1, 2, 3))

        idle_add.assert_called_once()
        self.assertIsNone(keyboard._lighting_state_applied)

    @patch('openrazer_daemon.hardware.device_base.GLib.idle_add', return_value=9)
    def test_active_property_without_effect_notification_reapplies_off(self, idle_add):
        keyboard = self.make_keyboard()
        keyboard.set_lighting_state('off')

        keyboard.set_persistence('logo', 'active', True)

        idle_add.assert_called_once()
        self.assertTrue(keyboard.zone['logo']['active'])
        self.assertIsNone(keyboard._lighting_state_applied)

    def test_close_stops_workers_before_firmware_fallback(self):
        keyboard = self.make_keyboard()

        keyboard.close()

        self.assertEqual(keyboard.events[:2], [('stop_renderer',), ('stop_keys',)])
        self.assertIn(('spectrum',), keyboard.events)
        self.assertTrue(keyboard._is_closed)
        self.assertEqual(keyboard.zone['backlight']['effect'], 'wheel')

    def test_close_while_dark_or_suspended_does_not_restore_brightness(self):
        for suspended in (False, True):
            with self.subTest(suspended=suspended):
                keyboard = self.make_keyboard()
                if suspended:
                    keyboard.suspend_device()
                else:
                    keyboard.set_lighting_state('off')
                keyboard.events.clear()

                keyboard.close()

                self.assertNotIn(('brightness',), keyboard.events)
                self.assertNotIn(('spectrum',), keyboard.events)

    def test_invalid_state_has_no_side_effects(self):
        keyboard = self.make_keyboard()

        with self.assertRaises(ValueError):
            keyboard.set_lighting_state('invalid')

        self.assertEqual(keyboard.events, [])
        self.assertEqual(keyboard._lighting_state, 'software')


class LightingStartupTest(unittest.TestCase):
    @patch('openrazer_daemon.hardware.keyboards._RippleManager')
    @patch('openrazer_daemon.hardware.keyboards._KeyboardKeyManager')
    @patch.object(RazerDevice, 'load_methods')
    @patch.object(RazerDevice, 'add_dbus_method')
    @patch.object(RazerDevice, 'get_serial', return_value='TEST')
    @patch('openrazer_daemon.hardware.device_base.DBusService.__init__', return_value=None)
    def test_startup_obeys_policy_before_effect_and_brightness_writes(self, _dbus_init, _serial, _add_methods, _load_methods, _keys, _ripple):
        class Keyboard(_RippleKeyboard):
            USB_VID = 0x1532
            USB_PID = 0xffff
            HAS_MATRIX = True
            MATRIX_DIMS = [6, 16]
            SOFTWARE_WHEEL = True
            METHODS = ['set_static_effect', 'set_spectrum_effect', 'set_wheel_effect']

            def set_device_mode(self, mode, parameter):
                self.events.append(('mode', mode, parameter))

            def setSpectrum(self):
                self.events.append(('spectrum',))
                self.set_persistence('backlight', 'effect', 'spectrum')

            def setWheel(self, direction):
                self.events.append(('wheel', direction))

            def setBrightness(self, brightness):
                self.events.append(('brightness', brightness))

        config = configparser.ConfigParser()
        config['Startup'] = {'restore_persistence': 'true', 'persistence_dual_boot_quirk': 'false'}
        persistence = configparser.ConfigParser()
        persistence['TEST'] = {
            'backlight_active': 'true', 'backlight_brightness': '75',
            'backlight_effect': 'wheel', 'backlight_wave_dir': '2', 'backlight_speed': '1',
            'backlight_colors': '1 2 3 4 5 6 7 8 9',
        }

        for state, expected in (
                ('off', [('brightness', 0)]),
                ('hardware', [('spectrum',), ('mode', 0, 0), ('brightness', 75)])):
            with self.subTest(state=state):
                Keyboard.events = []
                keyboard = Keyboard('/missing', 1, config, persistence, True, additional_interfaces=[], additional_methods=[], unknown_serial_counter={}, lighting_state=state)
                self.addCleanup(setattr, keyboard, '_is_closed', True)

                self.assertEqual(keyboard.events, expected)
                self.assertEqual(keyboard.zone['backlight']['effect'], 'wheel')
                self.assertEqual(keyboard.zone['backlight']['brightness'], 75)
                self.assertEqual(keyboard._lighting_state_applied, state)
                keyboard.ripple_manager.suspend.assert_called_with('lighting')


if __name__ == '__main__':
    unittest.main()
