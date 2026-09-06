# SPDX-License-Identifier: GPL-2.0-or-later

"""
Monitor lid, display power and local session activity.
"""
import logging
import os

import dbus


PROPERTIES_INTERFACE = 'org.freedesktop.DBus.Properties'
UPOWER_BUS_NAME = 'org.freedesktop.UPower'
UPOWER_PATH = '/org/freedesktop/UPower'
DISPLAY_BUS_NAME = 'org.gnome.Mutter.DisplayConfig'
DISPLAY_PATH = '/org/gnome/Mutter/DisplayConfig'
LOGIN1_BUS_NAME = 'org.freedesktop.login1'
LOGIN1_PATH = '/org/freedesktop/login1'
LOGIN1_MANAGER_INTERFACE = 'org.freedesktop.login1.Manager'
LOGIN1_SESSION_INTERFACE = 'org.freedesktop.login1.Session'
DBUS_TIMEOUT = 1.0


class LightingPowerMonitor(object):
    """Keep lighting policy inputs separate from screen locking."""

    def __init__(self, parent):
        self._parent = parent
        self._logger = logging.getLogger('razer.lighting_power')
        self._closed = False
        self._matches = []
        self.lid_closed = False
        self.display_off = None
        self.session_active = None
        self._system_bus = dbus.SystemBus()
        self._session_bus = dbus.SessionBus()

        self._matches.append(self._system_bus.add_signal_receiver(
            self._lid_changed, signal_name='PropertiesChanged',
            dbus_interface=PROPERTIES_INTERFACE, bus_name=UPOWER_BUS_NAME,
            path=UPOWER_PATH, arg0=UPOWER_BUS_NAME,
        ))
        self._matches.append(self._session_bus.add_signal_receiver(
            self._display_changed, signal_name='PropertiesChanged',
            dbus_interface=PROPERTIES_INTERFACE, bus_name=DISPLAY_BUS_NAME,
            path=DISPLAY_PATH, arg0=DISPLAY_BUS_NAME,
        ))
        self._matches.append(self._system_bus.add_signal_receiver(
            self._session_changed, signal_name='PropertiesChanged',
            dbus_interface=PROPERTIES_INTERFACE, bus_name=LOGIN1_BUS_NAME,
            arg0=LOGIN1_SESSION_INTERFACE,
        ))
        for signal_name in ('SessionNew', 'SessionRemoved'):
            self._matches.append(self._system_bus.add_signal_receiver(
                self._sessions_changed, signal_name=signal_name,
                dbus_interface=LOGIN1_MANAGER_INTERFACE,
                bus_name=LOGIN1_BUS_NAME, path=LOGIN1_PATH,
            ))
        for bus, name in ((self._system_bus, UPOWER_BUS_NAME),
                          (self._system_bus, LOGIN1_BUS_NAME),
                          (self._session_bus, DISPLAY_BUS_NAME)):
            self._matches.append(bus.add_signal_receiver(
                self._owner_changed, signal_name='NameOwnerChanged',
                dbus_interface='org.freedesktop.DBus',
                bus_name='org.freedesktop.DBus', path='/org/freedesktop/DBus',
                arg0=name,
            ))

        self._read_lid()
        self._read_display()
        self._read_sessions()

    @staticmethod
    def _properties(bus, name, path, interface):
        proxy = bus.get_object(name, path, introspect=False)
        return dbus.Interface(proxy, PROPERTIES_INTERFACE).GetAll(interface, timeout=DBUS_TIMEOUT)

    def _read_lid(self):
        try:
            properties = self._properties(self._system_bus, UPOWER_BUS_NAME,
                                          UPOWER_PATH, UPOWER_BUS_NAME)
            self.lid_closed = bool(properties.get('LidIsPresent', False) and
                                   properties.get('LidIsClosed', False))
        except dbus.exceptions.DBusException as error:
            self._logger.debug('Lid state unavailable: %s', error)

    def _read_display(self):
        try:
            properties = self._properties(self._session_bus, DISPLAY_BUS_NAME,
                                          DISPLAY_PATH, DISPLAY_BUS_NAME)
            mode = properties.get('PowerSaveMode', -1)
            self.display_off = mode != 0 if mode in (0, 1, 2, 3) else None
        except dbus.exceptions.DBusException as error:
            self._logger.debug('Display power state unavailable: %s', error)

    def _read_sessions(self):
        try:
            proxy = self._system_bus.get_object(LOGIN1_BUS_NAME, LOGIN1_PATH,
                                                introspect=False)
            sessions = dbus.Interface(proxy, LOGIN1_MANAGER_INTERFACE).ListSessions(timeout=DBUS_TIMEOUT)
        except dbus.exceptions.DBusException as error:
            self._logger.debug('Local session state unavailable: %s', error)
            return

        previous = self.session_active
        unknown = False
        for _session_id, uid, _name, seat, path in sessions:
            if uid != os.getuid() or not seat:
                continue
            try:
                properties = self._properties(self._system_bus, LOGIN1_BUS_NAME,
                                              path, LOGIN1_SESSION_INTERFACE)
            except dbus.exceptions.DBusException as error:
                self._logger.debug('Session state unavailable: %s', error)
                unknown = True
                continue
            if (properties.get('Type') in ('x11', 'wayland') and
                    not properties.get('Remote', True) and
                    properties.get('Active', False)):
                self.session_active = True
                return
        self.session_active = previous if unknown else False

    def refresh(self):
        """Refresh state after waking, without waiting for another event."""
        if self._closed:
            return
        self._read_lid()
        self._read_display()
        self._read_sessions()
        self._parent.apply_lighting_policy()

    def _lid_changed(self, _interface, changed, invalidated):
        if self._closed or not {'LidIsPresent', 'LidIsClosed'}.intersection(set(changed) | set(invalidated)):
            return
        self._read_lid()
        self._parent.apply_lighting_policy()

    def _display_changed(self, _interface, changed, invalidated):
        if self._closed or 'PowerSaveMode' not in set(changed) | set(invalidated):
            return
        self._read_display()
        self._parent.apply_lighting_policy()

    def _session_changed(self, _interface, changed, invalidated):
        if {'Active', 'Type', 'Remote'}.intersection(set(changed) | set(invalidated)):
            self._sessions_changed()

    def _sessions_changed(self, *_args):
        if self._closed:
            return
        self._read_sessions()
        self._parent.apply_lighting_policy()

    def _owner_changed(self, name, _old_owner, new_owner):
        if self._closed:
            return
        if name == UPOWER_BUS_NAME:
            self.lid_closed = False
            if new_owner:
                self._read_lid()
        elif name == DISPLAY_BUS_NAME:
            self.display_off = None
            if new_owner:
                self._read_display()
        elif name == LOGIN1_BUS_NAME:
            self.session_active = None
            if new_owner:
                self._read_sessions()
        self._parent.apply_lighting_policy()

    def close(self):
        """Stop monitoring policy inputs."""
        self._closed = True
        for match in self._matches:
            match.remove()
        self._matches.clear()
