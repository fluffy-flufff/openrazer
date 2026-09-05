# SPDX-License-Identifier: GPL-2.0-or-later

"""
Contains the functions and classes to perform ripple effects
"""
import datetime
import logging
import math
import threading
import time

# pylint: disable=import-error
from openrazer_daemon.keyboard import KeyboardColour
from openrazer_daemon.misc.matrix_effects import render_wheel_frame, wheel_phase


class RippleEffectThread(threading.Thread):
    """
    Ripple thread.

    This thread contains the run loop which performs all the circle calculations and generating of the binary payload
    """

    def __init__(self, parent, device_number):
        super().__init__()

        self._logger = logging.getLogger('razer.device{0}.ripplethread'.format(device_number))
        self._parent = parent

        self._colour = (0, 255, 0)
        self._refresh_rate = 0.040
        self._error_retry_rate = 1.0

        self._shutdown = False
        self._active = False

        self._layout = self._parent.matrix_layout
        if self._layout is None:
            self._rows, self._cols = self._parent._parent.MATRIX_DIMS
            device_rows, device_cols = self._rows, self._cols
        else:
            self._rows, self._cols = self._layout.logical_dims
            device_rows, device_cols = self._layout.device_dims

        self._keyboard_grid = KeyboardColour(device_rows, device_cols)

    @property
    def shutdown(self):
        """
        Get the shutdown flag
        """
        return self._shutdown

    @shutdown.setter
    def shutdown(self, value):
        """
        Set the shutdown flag

        :param value: Shutdown
        :type value: bool
        """
        self._shutdown = value

    @property
    def active(self):
        """
        Get if the thread is active

        :return: Active
        :rtype: bool
        """
        return self._active

    @property
    def key_list(self):
        """
        Get key list

        :return: Key list
        :rtype: list
        """
        return self._parent.key_list

    def enable(self, colour, refresh_rate):
        """
        Enable the ripple effect

        If the colour tuple contains None then it will set the ripple to random colours
        :param colour: Colour tuple like (0, 255, 255)
        :type colour: tuple

        :param refresh_rate: Refresh rate in seconds
        :type refresh_rate: float
        """
        if colour[0] is None:
            self._colour = None
        else:
            self._colour = colour
        self._refresh_rate = refresh_rate
        self._active = True

    def disable(self):
        """
        Disable the ripple effect
        """
        self._active = False

    def _render_frame(self, radiuses, needslogohandling=False):
        self._keyboard_grid.reset_rows()

        if self._layout is None:
            positions = (((row, col), (row, col)) for row in range(0, self._rows) for col in range(0, self._cols))
        else:
            positions = self._layout.positions.items()

        for (row, col), (device_row, device_col) in positions:
            # The logo location is physically at (6, 11), logically at (0, 20)
            # Skip when we come across the logo location, as the ripple would look wrong
            if needslogohandling and row == 0 and col == 20:
                continue

            if needslogohandling and row == 6:
                if col != 11:
                    continue

                # To account for logo placement
                for cirlce_centre_row, circle_centre_col, rad, colour in radiuses:
                    radius = math.sqrt(math.pow(cirlce_centre_row - row, 2) + math.pow(circle_centre_col - col, 2))
                    if rad >= radius >= rad - 2:
                        # Again, (0, 20) is the logical location of the logo led
                        self._keyboard_grid.set_key_colour(0, 20, colour)
                        break
            else:
                for cirlce_centre_row, circle_centre_col, rad, colour in radiuses:
                    radius = math.sqrt(math.pow(cirlce_centre_row - row, 2) + math.pow(circle_centre_col - col, 2))
                    if rad >= radius >= rad - 2:
                        self._keyboard_grid.set_key_colour(device_row, device_col, colour)
                        break

        return self._keyboard_grid.get_total_binary()

    def run(self):
        """
        Event loop
        """
        # pylint: disable=too-many-nested-blocks,too-many-branches
        expire_diff = datetime.timedelta(seconds=2)

        # self._parent: RippleManager
        # self._parent._parent: The device class (e.g. RazerBlackWidowUltimate2013)
        if self._rows == 6 and self._cols == 22:
            needslogohandling = True
            # a virtual 7th row for logo handling
            self._rows += 1
        else:
            needslogohandling = False

        # TODO time execution and then sleep for _refresh_rate - time_taken
        while not self._shutdown:
            sleep_rate = self._refresh_rate
            try:
                if self._active and not self._parent.suspended:
                    now = datetime.datetime.now()

                    radiuses = []

                    for expire_time, (key_row, key_col), colour in self.key_list:
                        event_time = expire_time - expire_diff

                        now_diff = now - event_time

                        # Current radius is based off a time metric
                        if self._colour is not None:
                            colour = self._colour
                        radiuses.append((key_row, key_col, now_diff.total_seconds() * 24, colour))

                    # Set the colors on the device
                    payload = self._render_frame(radiuses, needslogohandling)

                    self._parent.set_rgb_matrix(payload, self)
            except OSError as err:
                self._logger.warning("Failed to update ripple frame: %s", err)
                self._parent.frame_write_failed()
                sleep_rate = max(sleep_rate, self._error_retry_rate)

            # Sleep until the next ripple refresh
            time.sleep(sleep_rate)


class WheelEffectThread(threading.Thread):
    """
    Thread which renders the wheel effect into custom matrix frames.
    """

    def __init__(self, parent, device_number):
        super().__init__()

        self._logger = logging.getLogger('razer.device{0}.wheelthread'.format(device_number))
        self._parent = parent
        self._refresh_rate = 0.040
        self._error_retry_rate = 1.0
        self._shutdown = False
        self._active = False
        self._direction = 1
        self._start_time = time.monotonic()
        self._wake_event = threading.Event()

    @property
    def active(self):
        """
        Get if the thread is active.
        """
        return self._active

    @property
    def shutdown(self):
        """
        Get the shutdown flag.
        """
        return self._shutdown

    @shutdown.setter
    def shutdown(self, value):
        """
        Set the shutdown flag.
        """
        self._shutdown = value
        self._wake_event.set()

    def enable(self, direction):
        """
        Enable the wheel effect.
        """
        self._direction = direction if direction in (1, 2) else 1
        self._start_time = time.monotonic()
        self._active = True
        self._wake_event.set()

    def disable(self):
        """
        Disable the wheel effect.
        """
        self._active = False
        self._wake_event.set()

    def wake(self):
        """
        Wake the worker after a state change.
        """
        self._wake_event.set()

    def _wait(self, timeout=None):
        self._wake_event.wait(timeout)
        self._wake_event.clear()

    def run(self):
        """
        Render wheel frames at 25 FPS.
        """
        while not self._shutdown:
            if not self._active or self._parent.suspended:
                self._wait()
                continue

            frame_started = time.monotonic()
            try:
                phase = wheel_phase(frame_started - self._start_time, self._direction)
                payload = render_wheel_frame(self._parent.matrix_layout, phase)
                self._parent.set_rgb_matrix(payload, self)
            except OSError as err:
                self._logger.warning("Failed to update Wheel frame: %s", err)
                self._parent.frame_write_failed()
                self._wait(self._error_retry_rate)
                continue

            elapsed = time.monotonic() - frame_started
            self._wait(max(0, self._refresh_rate - elapsed))


class RippleManager(object):
    """
    Class which manages the overall process of performing a ripple effect
    """

    def __init__(self, parent, device_number):
        self._logger = logging.getLogger('razer.device{0}.ripplemanager'.format(device_number))
        self._parent = parent
        self._parent.register_observer(self)

        self._is_closed = False
        self._suspend_reasons = set()
        self._frame_lock = threading.Lock()
        self.matrix_layout = getattr(parent, 'matrix_layout', None)

        self._ripple_thread = RippleEffectThread(self, device_number)
        self._ripple_thread.start()

        self._wheel_thread = None
        if self._parent.SOFTWARE_WHEEL:
            self._wheel_thread = WheelEffectThread(self, device_number)
            self._wheel_thread.start()

    @property
    def suspended(self):
        """
        Get if software effects are suspended.
        """
        return bool(self._suspend_reasons)

    @property
    def key_list(self):
        """
        Get the list of keys from the key manager

        :return: List of tuples (expire_time, (key_row, key_col), random_colour)
        :rtype: list of tuple
        """
        result = []
        if hasattr(self._parent, 'key_manager'):
            result = self._parent.key_manager.temp_key_store

        return result

    def set_rgb_matrix(self, payload, source=None):
        """
        Set the LED matrix on the keyboard

        :param payload: Binary payload
        :type payload: bytes
        """
        with self._frame_lock:
            if self.suspended or (source is not None and not source.active):
                return

            self._parent._set_key_row(payload)
            self.refresh_keyboard()

    def refresh_keyboard(self):
        """
        Refresh the keyboard
        """
        if self._parent.CUSTOM_FRAME_EFFECT_ONCE:
            self._parent._ensure_custom_frame_effect()
        else:
            self._parent._set_custom_effect()

    def _start_custom_frame_effect(self):
        if not self.suspended and self._parent.CUSTOM_FRAME_EFFECT_ONCE:
            self._parent._ensure_custom_frame_effect()

    def frame_write_failed(self):
        """
        Invalidate cached custom mode after a failed frame update.
        """
        if self._parent.CUSTOM_FRAME_EFFECT_ONCE:
            with self._frame_lock:
                self._parent._invalidate_custom_frame_effect()

    def notify(self, msg):
        """
        Receive notificatons from the device (we only care about effects)

        :param msg: Notification
        :type msg: tuple
        """
        if not isinstance(msg, tuple):
            self._logger.warning("Got msg that was not a tuple")
        elif msg[0] == 'effect':
            if msg[2] == 'setBrightness' and self._parent.CUSTOM_FRAME_EFFECT_ONCE:
                return

            with self._frame_lock:
                # We have a message directed at us
                # MSG format
                #  0         1       2             3
                # ('effect', Device, 'effectName', 'effectparams'...)
                # Device is the device the msg originated from (could be parent device)
                if msg[2] == 'setRipple':
                    if self._wheel_thread is not None:
                        self._wheel_thread.disable()

                    self._start_custom_frame_effect()

                    # Get (red, green, blue) tuple (args 3:6), and refreshrate arg 6
                    self._parent.key_manager.temp_key_store_state = True
                    self._ripple_thread.enable(msg[3:6], msg[6])
                elif msg[2] == 'setWheel' and self._wheel_thread is not None:
                    self._ripple_thread.disable()
                    self._parent.key_manager.temp_key_store_state = False

                    self._start_custom_frame_effect()
                    self._wheel_thread.enable(msg[3])
                else:
                    # Effect other than ripple so stop
                    self._ripple_thread.disable()

                    if self._wheel_thread is not None:
                        self._wheel_thread.disable()

                    self._parent.key_manager.temp_key_store_state = False

                    if self._parent.CUSTOM_FRAME_EFFECT_ONCE:
                        self._parent._invalidate_custom_frame_effect()

    def suspend(self, reason='device'):
        """
        Pause software effects while the device is suspended.
        """
        with self._frame_lock:
            was_suspended = self.suspended
            self._suspend_reasons.add(reason)
            if not was_suspended and self._parent.CUSTOM_FRAME_EFFECT_ONCE:
                self._parent._invalidate_custom_frame_effect()

    def resume(self, reason='device'):
        """
        Restore custom mode before software effects continue.
        """
        with self._frame_lock:
            if reason not in self._suspend_reasons:
                return

            self._suspend_reasons.remove(reason)
            if self.suspended:
                return

            if self._wheel_thread is not None:
                self._wheel_thread.wake()

            if self._parent.CUSTOM_FRAME_EFFECT_ONCE:
                self._parent._invalidate_custom_frame_effect()
                if self._ripple_thread.active or (self._wheel_thread is not None and self._wheel_thread.active):
                    self._parent._ensure_custom_frame_effect()

    def close(self):
        """
        Close the manager, stop ripple thread
        """
        if not self._is_closed:
            self._logger.debug("Closing Ripple Manager")
            self._is_closed = True

            self._ripple_thread.shutdown = True
            self._ripple_thread.join(timeout=2)
            if self._ripple_thread.is_alive():
                self._logger.error("Could not stop RippleEffect thread")

            if self._wheel_thread is not None:
                self._wheel_thread.shutdown = True
                self._wheel_thread.join(timeout=2)
                if self._wheel_thread.is_alive():
                    self._logger.error("Could not stop WheelEffect thread")

    def __del__(self):
        self.close()
