# SPDX-License-Identifier: GPL-2.0-or-later

import configparser
import unittest
from unittest.mock import DEFAULT, MagicMock, patch

from openrazer_daemon.hardware import headsets


class KrakenLightingTest(unittest.TestCase):
    MODELS = (
        headsets.RazerKraken71,
        headsets.RazerKraken71Alternate,
        headsets.RazerKraken71Chroma,
        headsets.RazerKraken71V2,
        headsets.RazerKrakenTournamentEdition,
        headsets.RazerKrakenUltimate,
    )

    def setUp(self):
        methods = (
            'set_none_effect', 'set_static_effect', 'set_spectrum_effect',
            'set_breath_single_effect', 'set_breath_dual_effect', 'set_breath_triple_effect',
        )
        effect_patch = patch.multiple(
            headsets._dbus_chroma, **{method: DEFAULT for method in methods},
        )
        self.effects = effect_patch.start()
        self.addCleanup(effect_patch.stop)

    @staticmethod
    def device(model):
        device = object.__new__(model)
        device.logger = MagicMock()
        device._is_closed = True
        device._disable_notifications = False
        device._disable_persistence = False
        device._lighting_state = 'software'
        device._lighting_state_applied = 'software'
        device._system_suspended = False
        device._lighting_restore_source = None
        device._effect_restore_zones = {'backlight'}
        device._persisted_effect_state = {}
        device.suspend_args = {}
        device.ZONES = ('backlight',)
        device.zone = {
            'backlight': {
                'present': True, 'effect': 'static',
                'colors': [1, 2, 3, 4, 5, 6, 7, 8, 9],
            },
        }
        device.persistence = configparser.ConfigParser()
        device.persistence.status = {'changed': False}
        device._restore_effects = MagicMock()
        return device

    def test_hardware_transition_restores_current_effect_without_prior_off(self):
        for model in self.MODELS:
            with self.subTest(model=model.__name__):
                device = self.device(model)
                static = self.effects['set_static_effect']
                static.reset_mock()

                self.assertTrue(device.set_lighting_state('hardware'))

                colors = (0, 0, 0) if issubclass(model, headsets.RazerKraken71) else (1, 2, 3)
                self.assertEqual(static.call_count, 1)
                self.assertIs(static.call_args.args[0], device)
                self.assertEqual(static.call_args.args[1:], colors)
                device._restore_effects.assert_called_once_with(set())

    def test_lock_after_effect_change_does_not_restore_previous_off_snapshot(self):
        device = self.device(headsets.RazerKraken71V2)
        device.set_lighting_state('off')
        device.set_lighting_state('software')
        device.set_persistence('backlight', 'effect', 'breathSingle')
        device.set_persistence('backlight', 'colors', [10, 20, 30, 4, 5, 6, 7, 8, 9])
        self.effects['set_static_effect'].reset_mock()

        self.assertTrue(device.set_lighting_state('hardware'))

        self.effects['set_static_effect'].assert_not_called()
        breath = self.effects['set_breath_single_effect']
        self.assertEqual(breath.call_count, 1)
        self.assertEqual(breath.call_args.args[1:], (10, 20, 30))
        self.assertEqual(device.zone['backlight']['effect'], 'breathSingle')

    def test_changed_profile_while_off_is_restored(self):
        device = self.device(headsets.RazerKraken71V2)
        device.set_lighting_state('off')
        device.set_persistence('backlight', 'effect', 'breathDual')
        device.set_persistence('backlight', 'colors', [10, 20, 30, 40, 50, 60, 7, 8, 9])

        self.assertTrue(device.set_lighting_state('software'))

        self.effects['set_static_effect'].assert_not_called()
        breath = self.effects['set_breath_dual_effect']
        self.assertEqual(breath.call_count, 1)
        self.assertEqual(breath.call_args.args[1:], (10, 20, 30, 40, 50, 60))
        self.assertEqual(device.zone['backlight']['effect'], 'breathDual')

    def test_breathing_effects_receive_their_required_color_count(self):
        for model in self.MODELS[2:]:
            effects = [('breathSingle', 'set_breath_single_effect', 3)]
            if model is not headsets.RazerKraken71Chroma:
                effects.extend((
                    ('breathDual', 'set_breath_dual_effect', 6),
                    ('breathTriple', 'set_breath_triple_effect', 9),
                ))
            for effect, method, count in effects:
                with self.subTest(model=model.__name__, effect=effect):
                    device = self.device(model)
                    device.zone['backlight']['effect'] = effect
                    callback = self.effects[method]
                    callback.reset_mock()

                    self.assertTrue(device.set_lighting_state('hardware'))

                    self.assertEqual(callback.call_count, 1)
                    self.assertEqual(callback.call_args.args[1:], tuple(range(1, count + 1)))

    def test_unknown_effect_is_left_for_normal_restore(self):
        for model in self.MODELS:
            with self.subTest(model=model.__name__):
                device = self.device(model)
                device.zone['backlight']['effect'] = 'none'

                self.assertTrue(device.set_lighting_state('hardware'))

                device._restore_effects.assert_called_once_with({'backlight'})


if __name__ == '__main__':
    unittest.main()
