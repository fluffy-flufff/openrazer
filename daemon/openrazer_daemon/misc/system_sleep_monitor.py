# SPDX-License-Identifier: GPL-2.0-or-later

"""
System sleep monitor using logind
"""
import logging
import os

import dbus
from gi.repository import GLib


LOGIN1_BUS_NAME = 'org.freedesktop.login1'
LOGIN1_INTERFACE = 'org.freedesktop.login1.Manager'
LOGIN1_PATH = '/org/freedesktop/login1'
RESUME_RETRY_DELAYS_MS = (500, 5000)


class SystemSleepMonitor(object):
    """
    Monitor system sleep and wake signals on the system bus.
    """

    def __init__(self, parent):
        self._logger = logging.getLogger('razer.system_sleep')
        self._logger.info("Initialising DBus System Sleep Monitor")

        self._parent = parent
        self._sleeping = False
        self._closed = False
        self._inhibitor_fd = None
        self._resume_retry_source = None
        self._resume_retry_index = 0
        self._bus = dbus.SystemBus()
        self._manager = dbus.Interface(
            self._bus.get_object(LOGIN1_BUS_NAME, LOGIN1_PATH),
            dbus_interface=LOGIN1_INTERFACE,
        )
        self._bus.add_signal_receiver(
            self.signal_callback,
            signal_name='PrepareForSleep',
            dbus_interface=LOGIN1_INTERFACE,
            bus_name=LOGIN1_BUS_NAME,
            path=LOGIN1_PATH,
        )
        self._acquire_inhibitor()

    def _acquire_inhibitor(self):
        if self._closed:
            return False
        if self._inhibitor_fd is not None:
            return True

        try:
            inhibitor = self._manager.Inhibit(
                'sleep',
                'OpenRazer',
                'Prepare Razer devices for sleep',
                'delay',
            )
            self._inhibitor_fd = inhibitor.take()
        except (dbus.exceptions.DBusException, OSError, ValueError) as error:
            self._logger.warning("Failed to acquire system sleep inhibitor: %s", error)
            return False

        return True

    def _release_inhibitor(self):
        inhibitor_fd = self._inhibitor_fd
        self._inhibitor_fd = None
        if inhibitor_fd is None:
            return

        try:
            os.close(inhibitor_fd)
        except OSError as error:
            self._logger.warning("Failed to release system sleep inhibitor: %s", error)

    def close(self):
        """Release the delay inhibitor."""
        self._closed = True
        self._cancel_resume_retry()
        self._release_inhibitor()

    def _cancel_resume_retry(self):
        source = self._resume_retry_source
        self._resume_retry_source = None
        self._resume_retry_index = 0
        if source is not None:
            GLib.source_remove(source)

    def _retry_resume(self):
        self._resume_retry_source = None
        if not self._closed and not self._sleeping:
            if self._parent.resume_devices() is False:
                if not self._schedule_resume_retry():
                    self._logger.warning("Failed to restore one or more devices after wake")
            else:
                self._resume_retry_index = 0
        return False

    def _schedule_resume_retry(self):
        if (self._resume_retry_source is not None or
                self._resume_retry_index >= len(RESUME_RETRY_DELAYS_MS)):
            return False

        delay = RESUME_RETRY_DELAYS_MS[self._resume_retry_index]
        self._resume_retry_index += 1
        self._resume_retry_source = GLib.timeout_add(delay, self._retry_resume)
        return True

    def signal_callback(self, sleeping):
        """
        Called before system sleep and after system wake.

        :param sleeping: True before sleep, false after wake
        :type sleeping: dbus.Boolean
        """
        sleeping = bool(sleeping)
        if self._closed:
            return
        if self._sleeping == sleeping:
            if not sleeping:
                self._acquire_inhibitor()
            return

        self._sleeping = sleeping
        if sleeping:
            self._logger.debug("Received system sleep signal")
            self._cancel_resume_retry()
            try:
                self._parent.suspend_devices()
            finally:
                self._release_inhibitor()
        else:
            self._logger.debug("Received system wake signal")
            self._acquire_inhibitor()
            self._resume_retry_index = 0
            if self._parent.resume_devices() is False:
                self._schedule_resume_retry()
