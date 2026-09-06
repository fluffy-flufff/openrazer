# SPDX-License-Identifier: GPL-2.0-or-later

import threading
import unittest
import unittest.mock

from openrazer_daemon.dbus_services.dbus_methods.all import layout_ids
from openrazer_daemon.dbus_services.dbus_methods.chroma_keyboard import set_wheel_effect
from openrazer_daemon.hardware.keyboards import _RippleKeyboard
from openrazer_daemon.misc.matrix_effects import BLADE_PRO_EARLY_2020_LAYOUTS, render_wheel_frame, select_matrix_layout, wheel_phase
from openrazer_daemon.misc.ripple_effect import RippleEffectThread, RippleManager, WheelEffectThread


def frame_colour(payload, row, column, columns=16):
    row_size = 3 + (columns * 3)
    offset = (row * row_size) + 3 + (column * 3)
    return tuple(payload[offset:offset + 3])


class MatrixLayoutTest(unittest.TestCase):
    def test_firmware_layout_groups(self):
        ansi = BLADE_PRO_EARLY_2020_LAYOUTS['en_US']
        iso = BLADE_PRO_EARLY_2020_LAYOUTS['en_GB']
        japanese = BLADE_PRO_EARLY_2020_LAYOUTS['ja_JP']

        self.assertEqual(ansi.logical_dims, (7, 15))
        self.assertEqual(ansi.device_dims, (6, 16))
        self.assertEqual(len(ansi.positions), 80)
        self.assertEqual(len(iso.positions), 81)
        self.assertEqual(len(japanese.positions), 84)
        self.assertIs(BLADE_PRO_EARLY_2020_LAYOUTS['zh_TW'], ansi)
        self.assertIs(BLADE_PRO_EARLY_2020_LAYOUTS['ko_KR'], ansi)
        self.assertIs(BLADE_PRO_EARLY_2020_LAYOUTS['de_DE'], iso)

        for layout_id in ('01', '08', '09'):
            self.assertIs(select_matrix_layout(BLADE_PRO_EARLY_2020_LAYOUTS, layout_ids[layout_id]), ansi)
        for layout_id in ('03', '04', '06', '07'):
            self.assertIs(select_matrix_layout(BLADE_PRO_EARLY_2020_LAYOUTS, layout_ids[layout_id]), iso)
        self.assertIs(select_matrix_layout(BLADE_PRO_EARLY_2020_LAYOUTS, layout_ids['0c']), japanese)

    def test_unknown_firmware_layout_uses_ansi(self):
        layout = select_matrix_layout(BLADE_PRO_EARLY_2020_LAYOUTS, 'unknown')

        self.assertIs(layout, BLADE_PRO_EARLY_2020_LAYOUTS['en_US'])

    def test_layout_mask_and_key_positions(self):
        layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_US']
        payload = layout.render(lambda _row, _column: (1, 2, 3))

        self.assertEqual(len(payload), 6 * (3 + (16 * 3)))
        self.assertEqual(frame_colour(payload, 0, 0), (0, 0, 0))
        self.assertEqual(frame_colour(payload, 0, 1), (1, 2, 3))
        self.assertEqual(layout.positions[layout.key_positions['RETURN']], (3, 15))
        self.assertEqual(layout.positions[layout.key_positions['SPACE']], (5, 6))
        self.assertEqual(layout.positions[layout.key_positions['DOWNARROW']], (5, 15))

    def test_iso_key_positions(self):
        layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_GB']

        self.assertEqual(layout.positions[layout.key_positions['RETURN']], (2, 14))
        self.assertEqual(layout.positions[layout.key_positions['BACKSLASH']], (4, 2))

    def test_japanese_key_events_use_lit_positions(self):
        layout = BLADE_PRO_EARLY_2020_LAYOUTS['ja_JP']
        expected = {
            89: ((4, 12), (4, 13)),
            92: ((5, 8), (5, 8)),
            93: ((5, 9), (5, 9)),
            94: ((5, 4), (5, 5)),
            124: ((1, 13), (1, 14)),
        }

        for event_code, (logical_position, device_position) in expected.items():
            key_name = layout.event_mapping[event_code]
            self.assertEqual(layout.key_positions[key_name], logical_position)
            self.assertEqual(layout.positions[logical_position], device_position)


class WheelEffectTest(unittest.TestCase):
    def test_wheel_directions_advance_opposite_ways(self):
        layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_US']
        right_phase = wheel_phase(0.4, 1)
        left_phase = wheel_phase(0.4, 2)

        self.assertEqual(right_phase, -left_phase)
        self.assertLess(right_phase, 0)
        self.assertGreater(left_phase, 0)
        self.assertNotEqual(render_wheel_frame(layout, right_phase), render_wheel_frame(layout, left_phase))

    def test_wheel_respects_layout_mask(self):
        layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_US']
        payload = render_wheel_frame(layout, 0)

        self.assertEqual(frame_colour(payload, 0, 0), (0, 0, 0))
        self.assertNotEqual(frame_colour(payload, 0, 1), (0, 0, 0))

    def test_wheel_thread_uses_25_fps_interval(self):
        parent = unittest.mock.Mock()
        parent.suspended = True
        thread = WheelEffectThread(parent, 0)

        self.assertEqual(thread._refresh_rate, 0.040)
        thread.start()
        thread.shutdown = True
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_wheel_thread_retries_transient_write_errors(self):
        attempts = []
        recovered = threading.Event()

        def write_frame(_payload, _source):
            attempts.append(None)
            if len(attempts) == 1:
                raise OSError('reset')
            recovered.set()

        parent = unittest.mock.Mock()
        parent.suspended = False
        parent.matrix_layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_US']
        parent.set_rgb_matrix.side_effect = write_frame
        thread = WheelEffectThread(parent, 0)
        thread._error_retry_rate = 0.01
        thread.enable(1)

        with self.assertLogs('razer.device0.wheelthread', level='WARNING'):
            thread.start()
            self.assertTrue(recovered.wait(timeout=1))
            self.assertTrue(thread.is_alive())
        parent.frame_write_failed.assert_called_once_with()
        thread.shutdown = True
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())


class RippleGeometryTest(unittest.TestCase):
    def test_ripple_uses_logical_to_device_mapping(self):
        manager = unittest.mock.Mock()
        manager.matrix_layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_US']
        thread = RippleEffectThread(manager, 0)

        payload = thread._render_frame([(3, 14, 0, (10, 20, 30))])

        self.assertEqual(frame_colour(payload, 3, 15), (10, 20, 30))
        self.assertEqual(frame_colour(payload, 3, 14), (0, 0, 0))


class SoftwareEffectRestoreTest(unittest.TestCase):
    def make_keyboard(self, effect, restore_persistence):
        keyboard = unittest.mock.Mock()
        keyboard._lighting_state = 'software'
        keyboard.config.getboolean.return_value = restore_persistence
        keyboard.zone = {
            'backlight': {
                'effect': effect,
                'colors': [1, 2, 3],
                'wave_dir': 2,
            },
        }
        keyboard.SOFTWARE_WHEEL = True
        keyboard.capitalize_first_char.side_effect = lambda value: value[0].upper() + value[1:]
        keyboard.ripple_manager._ripple_thread._refresh_rate = 0.040
        return keyboard

    def test_startup_does_not_restore_software_effects_when_disabled(self):
        for effect in ('wheel', 'ripple', 'rippleRandomColour'):
            with self.subTest(effect=effect):
                keyboard = self.make_keyboard(effect, restore_persistence=False)

                _RippleKeyboard._restore_software_effect(keyboard)

                keyboard.setWheel.assert_not_called()
                keyboard.setRipple.assert_not_called()
                keyboard.setRippleRandomColour.assert_not_called()

    def test_startup_restores_software_wheel_when_enabled(self):
        keyboard = self.make_keyboard('wheel', restore_persistence=True)

        _RippleKeyboard._restore_software_effect(keyboard)

        keyboard.setWheel.assert_called_once_with(2)

    def test_startup_defers_software_effect_while_locked_or_dark(self):
        for state in ('hardware', 'off'):
            with self.subTest(state=state):
                keyboard = self.make_keyboard('wheel', restore_persistence=True)
                keyboard._lighting_state = state

                _RippleKeyboard._restore_software_effect(keyboard)

                keyboard.setWheel.assert_not_called()


class LightingLifecycleOrderingTest(unittest.TestCase):
    def make_keyboard(self, events, custom_once=True):
        keyboard = object.__new__(_RippleKeyboard)
        keyboard.logger = unittest.mock.Mock()
        keyboard._is_closed = True
        keyboard._lighting_state = 'software'
        keyboard._lighting_state_applied = 'software'
        keyboard._system_suspended = False
        keyboard._disable_notifications = False
        keyboard._disable_persistence = False
        keyboard._effect_restore_zones = {'backlight'}
        keyboard.zone = {'backlight': {'effect': 'spectrum'}}
        keyboard.CUSTOM_FRAME_EFFECT_ONCE = custom_once
        keyboard.ripple_manager = unittest.mock.Mock()
        keyboard.ripple_manager.suspend.side_effect = lambda reason: events.append(('pause', reason))
        keyboard.ripple_manager.resume.side_effect = lambda reason: events.append(('resume', reason))
        keyboard.disable_brightness = lambda: events.append(('dark',))
        keyboard.restore_brightness = lambda: events.append(('brightness',))
        keyboard.set_device_mode = lambda *args: events.append(('mode', *args))
        keyboard._restore_effects = lambda zones: events.append(('effects',))
        return keyboard

    def test_workers_pause_before_shutdown_and_resume_after_restore(self):
        events = []
        keyboard = self.make_keyboard(events)
        keyboard.suspend_device()
        self.assertTrue(keyboard.resume_device())
        self.assertTrue(keyboard.disable_lighting())
        self.assertTrue(keyboard.restore_lighting())

        self.assertEqual(events, [
            ('pause', 'lighting'), ('dark',),
            ('pause', 'lighting'), ('mode', 3, 0), ('effects',), ('brightness',), ('resume', 'lighting'),
            ('pause', 'lighting'), ('dark',),
            ('pause', 'lighting'), ('mode', 3, 0), ('effects',), ('brightness',), ('resume', 'lighting'),
        ])

    def test_other_ripple_devices_pause_for_suspend_and_lock(self):
        events = []
        keyboard = self.make_keyboard(events, custom_once=False)

        keyboard.suspend_device()
        keyboard.resume_device()
        keyboard.disable_lighting()
        keyboard.restore_lighting()

        self.assertEqual(events, [
            ('pause', 'lighting'), ('dark',),
            ('pause', 'lighting'), ('mode', 3, 0), ('effects',), ('brightness',), ('resume', 'lighting'),
            ('pause', 'lighting'), ('dark',),
            ('pause', 'lighting'), ('mode', 3, 0), ('effects',), ('brightness',), ('resume', 'lighting'),
        ])

    def test_workers_remain_paused_after_restore_error(self):
        events = []
        keyboard = self.make_keyboard(events)
        keyboard.suspend_device()

        def fail(_zones):
            raise OSError('reset')

        keyboard._restore_effects = fail
        self.assertFalse(keyboard.resume_device())
        self.assertFalse(keyboard.restore_lighting())

        keyboard.ripple_manager.resume.assert_not_called()
        self.assertEqual(events.count(('brightness',)), 2)
        self.assertIsNone(keyboard._lighting_state_applied)


class FakeEffectThread(object):
    def __init__(self, _parent, _device_number):
        self.active = False
        self.shutdown = False
        self.started = False
        self.enable_args = None
        self.disable_count = 0
        self.join_count = 0
        self.wake_count = 0

    def start(self):
        self.started = True

    def enable(self, *args):
        self.active = True
        self.enable_args = args

    def disable(self):
        self.active = False
        self.disable_count += 1

    def wake(self):
        self.wake_count += 1

    def join(self, timeout=None):
        self.join_count += 1

    def is_alive(self):
        return False


class FakeKeyManager(object):
    def __init__(self):
        self.temp_key_store_state = False
        self.temp_key_store = []


class FakeManagerParent(object):
    MATRIX_DIMS = [6, 16]

    def __init__(self, software_wheel, custom_once):
        self.SOFTWARE_WHEEL = software_wheel
        self.CUSTOM_FRAME_EFFECT_ONCE = custom_once
        self.matrix_layout = BLADE_PRO_EARLY_2020_LAYOUTS['en_US'] if software_wheel else None
        self.key_manager = FakeKeyManager()
        self.observers = []
        self.ensure_count = 0
        self.invalidate_count = 0
        self.custom_count = 0

    def register_observer(self, observer):
        self.observers.append(observer)

    def _ensure_custom_frame_effect(self):
        self.ensure_count += 1

    def _invalidate_custom_frame_effect(self):
        self.invalidate_count += 1

    def _set_custom_effect(self):
        self.custom_count += 1

    def _set_key_row(self, _payload):
        pass


class RippleManagerTest(unittest.TestCase):
    def make_manager(self, parent):
        ripple_patcher = unittest.mock.patch('openrazer_daemon.misc.ripple_effect.RippleEffectThread', FakeEffectThread)
        wheel_patcher = unittest.mock.patch('openrazer_daemon.misc.ripple_effect.WheelEffectThread', FakeEffectThread)
        ripple_patcher.start()
        wheel_patcher.start()
        self.addCleanup(ripple_patcher.stop)
        self.addCleanup(wheel_patcher.stop)
        return RippleManager(parent, 0)

    def test_software_wheel_lifecycle(self):
        parent = FakeManagerParent(software_wheel=True, custom_once=True)
        manager = self.make_manager(parent)

        manager.notify(('effect', parent, 'backlight', 'setWheel', 2))

        self.assertTrue(manager._wheel_thread.active)
        self.assertEqual(manager._wheel_thread.enable_args, (2,))
        self.assertFalse(manager._ripple_thread.active)
        self.assertEqual(parent.ensure_count, 1)

        manager.notify(('effect', parent, 'backlight', 'setBrightness', 128))
        self.assertTrue(manager._wheel_thread.active)
        self.assertEqual(parent.ensure_count, 1)
        self.assertEqual(parent.invalidate_count, 0)

        manager.suspend('lighting')
        self.assertTrue(manager.suspended)
        manager.suspend('device')
        manager.resume('device')
        self.assertTrue(manager.suspended)
        self.assertEqual(parent.ensure_count, 1)
        manager.resume('lighting')
        self.assertFalse(manager.suspended)
        self.assertEqual(parent.invalidate_count, 2)
        self.assertEqual(parent.ensure_count, 2)
        self.assertEqual(manager._wheel_thread.wake_count, 1)

        manager.resume('lighting')
        self.assertEqual(parent.invalidate_count, 2)
        self.assertEqual(parent.ensure_count, 2)
        self.assertEqual(manager._wheel_thread.wake_count, 1)

        manager.notify(('effect', parent, 'backlight', 'setStatic', 1, 2, 3))
        self.assertFalse(manager._wheel_thread.active)
        self.assertEqual(parent.invalidate_count, 3)

        manager.close()
        self.assertTrue(manager._ripple_thread.shutdown)
        self.assertTrue(manager._wheel_thread.shutdown)

    def test_zone_aware_effect_handling(self):
        parent = FakeManagerParent(software_wheel=True, custom_once=True)
        manager = self.make_manager(parent)

        manager.notify(('effect', parent, 'backlight', 'setWheel', 1))
        manager.notify(('effect', parent, 'logo', 'setBreath'))

        self.assertTrue(manager._wheel_thread.active)
        self.assertEqual(parent.invalidate_count, 0)

        manager.notify(('effect', object(), 'logo', 'setBreath'))

        self.assertFalse(manager._wheel_thread.active)
        self.assertEqual(parent.invalidate_count, 1)
        manager.close()

    def test_other_devices_keep_per_frame_custom_selection(self):
        parent = FakeManagerParent(software_wheel=False, custom_once=False)
        manager = self.make_manager(parent)

        manager.refresh_keyboard()
        manager.refresh_keyboard()
        manager.notify(('effect', parent, 'backlight', 'setWheel', 1))

        self.assertEqual(parent.custom_count, 2)
        self.assertIsNone(manager._wheel_thread)
        self.assertEqual(manager._ripple_thread.disable_count, 1)
        self.assertEqual(parent.invalidate_count, 0)
        manager.close()


class WheelDBusMethodTest(unittest.TestCase):
    def make_device(self, software_wheel):
        device = unittest.mock.Mock()
        device.SOFTWARE_WHEEL = software_wheel
        device.get_driver_path.return_value = '/sys/fake/matrix_effect_wheel'
        return device

    def test_software_wheel_does_not_use_kernel_wheel_attribute(self):
        device = self.make_device(software_wheel=True)

        with unittest.mock.patch('builtins.open') as open_mock:
            set_wheel_effect(device, 2)

        open_mock.assert_not_called()
        device.send_effect_event.assert_called_once_with('backlight', 'setWheel', 2)
        device.set_persistence.assert_any_call('backlight', 'effect', 'wheel')
        device.set_persistence.assert_any_call('backlight', 'wave_dir', 2)

    def test_hardware_wheel_path_is_unchanged(self):
        device = self.make_device(software_wheel=False)
        driver_file = unittest.mock.mock_open()

        with unittest.mock.patch('builtins.open', driver_file):
            set_wheel_effect(device, 1)

        driver_file.assert_called_once_with('/sys/fake/matrix_effect_wheel', 'w')
        driver_file().write.assert_called_once_with('1')

    def test_hardware_wheel_keeps_existing_invalid_direction_handling(self):
        device = self.make_device(software_wheel=False)
        driver_file = unittest.mock.mock_open()

        with unittest.mock.patch('builtins.open', driver_file):
            set_wheel_effect(device, 7)

        device.send_effect_event.assert_called_once_with('backlight', 'setWheel', 7)
        device.set_persistence.assert_any_call('backlight', 'wave_dir', 7)
        driver_file().write.assert_called_once_with('1')


if __name__ == '__main__':
    unittest.main()
