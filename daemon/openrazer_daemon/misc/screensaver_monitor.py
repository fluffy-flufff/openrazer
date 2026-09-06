# SPDX-License-Identifier: GPL-2.0-or-later

"""
Screensaver state on the session bus
"""
import logging
from functools import partial

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
    """Monitor lock/screensaver state independently of display power."""

    def __init__(self, parent):
        self._logger = logging.getLogger('razer.screensaver')
        self._logger.info("Initialising DBus Screensaver Monitor")
        self._parent = parent
        self._monitoring = True
        self._active = None
        self._states = {}
        self._matches = []
        self._closed = False
        self._bus = dbus.SessionBus()
        for interface in DBUS_SCREENSAVER_INTERFACES:
            self._matches.append(self._bus.add_signal_receiver(
                partial(self.signal_callback, interface=interface),
                dbus_interface=interface,
                signal_name='ActiveChanged',
                bus_name=interface,
            ))
            self._matches.append(self._bus.add_signal_receiver(
                self._owner_changed,
                dbus_interface='org.freedesktop.DBus',
                signal_name='NameOwnerChanged',
                bus_name='org.freedesktop.DBus',
                arg0=interface,
            ))
        self.refresh(notify=False)

    @property
    def active(self):
        return self._active

    @property
    def monitoring(self):
        return self._monitoring

    @monitoring.setter
    def monitoring(self, value):
        self._monitoring = bool(value)
        self._parent.apply_lighting_policy()

    def refresh(self, notify=True):
        if self._closed:
            return
        for interface in DBUS_SCREENSAVER_INTERFACES:
            try:
                present = self._bus.name_has_owner(interface)
            except dbus.exceptions.DBusException:
                continue
            if not present:
                self._states.pop(interface, None)
                continue
            paths = ['/' + interface.replace('.', '/')]
            if interface == 'org.freedesktop.ScreenSaver':
                paths.append('/ScreenSaver')
            for path in paths:
                try:
                    proxy = self._bus.get_object(interface, path, introspect=False)
                    active = proxy.GetActive(dbus_interface=interface, timeout=1.0)
                except dbus.exceptions.DBusException:
                    continue
                self._states[interface] = bool(active)
                break
        active = any(self._states.values()) if self._states else None
        changed = self._active != active
        self._active = active
        if notify and changed:
            self._parent.apply_lighting_policy()

    def _owner_changed(self, name, old_owner, new_owner):
        if self._closed:
            return
        self._states.pop(name, None)
        self.refresh()

    def signal_callback(self, active, interface='org.freedesktop.ScreenSaver'):
        if self._closed:
            return
        self._states[interface] = bool(active)
        self._active = any(self._states.values())
        self._parent.apply_lighting_policy()

    def close(self):
        self._closed = True
        for match in self._matches:
            match.remove()
        self._matches.clear()
