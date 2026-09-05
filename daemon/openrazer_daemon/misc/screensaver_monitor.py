# SPDX-License-Identifier: GPL-2.0-or-later

"""
Screensaver class which watches dbus signals to see if screensaver is active
"""
import logging
import dbus
import dbus.exceptions

DBUS_SCREENSAVER_INTERFACES = (
    'org.cinnamon.ScreenSaver',
    'org.freedesktop.ScreenSaver',
    'org.gnome.ScreenSaver',
    'org.mate.ScreenSaver',
    'org.xfce.ScreenSaver',
)


class ScreensaverMonitor(object):
    """
    Simple class for monitoring signals on the Session Bus
    """

    def __init__(self, parent):
        self._logger = logging.getLogger('razer.screensaver')
        self._logger.info("Initialising DBus Screensaver Monitor")

        self._parent = parent
        self._monitoring = True
        self._active = None
        self._lighting_disabled = False
        self._lighting_retry = False

        # Get session bus
        self._bus = dbus.SessionBus()
        # Loop through and monitor the signals
        for screensaver_interface in DBUS_SCREENSAVER_INTERFACES:
            self._bus.add_signal_receiver(self.signal_callback, dbus_interface=screensaver_interface, signal_name='ActiveChanged')

    @property
    def monitoring(self):
        """
        Monitoring property, if true then lighting will follow screensaver state.

        :return: If monitoring
        :rtype: bool
        """
        return self._monitoring

    @monitoring.setter
    def monitoring(self, value):
        """
        Monitoring property setter.

        :param value: If monitoring
        :type: bool
        """
        value = bool(value)
        self._monitoring = value
        if self._monitoring and self._active:
            self.disable_lighting()
        elif not self._monitoring:
            self.restore_lighting()

    def disable_lighting(self, force=False):
        """
        Turn off device lighting
        """
        if self._lighting_disabled and not self._lighting_retry and not force:
            return True

        self._logger.debug("Received screensaver active signal")
        self._lighting_disabled = True
        self._lighting_retry = True
        successful = self._parent.disable_lighting()
        self._lighting_retry = successful is False
        return not self._lighting_retry

    def restore_lighting(self):
        """
        Restore device lighting
        """
        if not self._lighting_disabled:
            return True

        self._logger.debug("Received screensaver inactive signal")
        self._lighting_retry = True
        successful = self._parent.restore_lighting()
        if successful is not False:
            self._lighting_disabled = False
            self._lighting_retry = False
        return not self._lighting_retry

    def reapply_lighting(self):
        """
        Keep lighting off after a device resumed.
        """
        if self.monitoring and self._active and self._lighting_disabled:
            return self.disable_lighting(force=True)
        return self.restore_lighting()

    def signal_callback(self, active):
        """
        Called by DBus when a signal is found

        :param active: If the screensaver is active
        :type active: dbus.Boolean
        """
        active = bool(active)
        self._active = active
        if not self.monitoring:
            self.restore_lighting()
            return

        if active:
            self.disable_lighting()
        else:
            self.restore_lighting()
