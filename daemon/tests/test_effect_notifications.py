# SPDX-License-Identifier: GPL-2.0-or-later

import threading
import unittest
import unittest.mock

from openrazer_daemon.hardware.device_base import RazerDevice
from openrazer_daemon.misc.ripple_effect import RippleManager


class DummyDevice(object):
    def __init__(self):
        self.notification = None
        self._disable_notifications = False
        self._lighting_state = 'software'

    def notify_observers(self, msg):
        self.notification = msg


class DummyRippleParent(object):
    CUSTOM_FRAME_EFFECT_ONCE = False

    def __init__(self):
        self.key_manager = unittest.mock.MagicMock()
        self.key_manager.temp_key_store_state = False


class DummyRippleThread(object):
    def __init__(self):
        self.active = False

    def enable(self, colour, refresh_rate):
        self.active = True

    def disable(self):
        self.active = False


class EffectNotificationTest(unittest.TestCase):
    def setUp(self):
        self.parent = DummyRippleParent()
        self.ripple_manager = RippleManager.__new__(RippleManager)
        self.ripple_manager._logger = unittest.mock.MagicMock()
        self.ripple_manager._parent = self.parent
        self.ripple_manager._ripple_thread = DummyRippleThread()
        self.ripple_manager._wheel_thread = None
        self.ripple_manager._suspend_reasons = set()
        self.ripple_manager._frame_lock = threading.Lock()
        self.ripple_manager._is_closed = True

    def test_effect_event_includes_zone(self):
        device = DummyDevice()

        RazerDevice.send_effect_event(device, 'logo', 'setStatic', 255, 255, 0)

        self.assertTupleEqual(device.notification, ('effect', device, 'logo', 'setStatic', 255, 255, 0))

    def test_logo_effect_does_not_stop_backlight_ripple(self):
        self.ripple_manager.notify(('effect', self.parent, 'backlight', 'setRipple', 255, 255, 0, 0.04))

        self.ripple_manager.notify(('effect', self.parent, 'logo', 'setNone'))

        self.assertTrue(self.ripple_manager._ripple_thread.active)
        self.assertTrue(self.parent.key_manager.temp_key_store_state)

    def test_synced_logo_effect_stops_backlight_ripple(self):
        self.ripple_manager.notify(('effect', self.parent, 'backlight', 'setRipple', 255, 255, 0, 0.04))

        self.ripple_manager.notify(('effect', object(), 'logo', 'setNone'))

        self.assertFalse(self.ripple_manager._ripple_thread.active)
        self.assertFalse(self.parent.key_manager.temp_key_store_state)

    def test_backlight_effect_stops_backlight_ripple(self):
        self.ripple_manager.notify(('effect', self.parent, 'backlight', 'setRipple', 255, 255, 0, 0.04))

        self.ripple_manager.notify(('effect', self.parent, 'backlight', 'setSpectrum'))

        self.assertFalse(self.ripple_manager._ripple_thread.active)
        self.assertFalse(self.parent.key_manager.temp_key_store_state)


if __name__ == '__main__':
    unittest.main()
