# SPDX-License-Identifier: GPL-2.0-or-later

"""
Helpers for daemon-rendered matrix effects.
"""
import colorsys
import math


class MatrixLayout(object):
    """
    Maps logical effect coordinates to the device LED matrix.
    """

    def __init__(self, logical_dims, device_dims, positions, key_positions, event_mapping=None):
        self.logical_dims = logical_dims
        self.device_dims = device_dims
        self.positions = positions
        self.key_positions = key_positions
        self.event_mapping = event_mapping or {}

    def render(self, pixel):
        """
        Render a logical matrix into matrix_custom_frame payloads.
        """
        rows, columns = self.device_dims
        frame = [[(0, 0, 0) for _ in range(columns)] for _ in range(rows)]

        for logical_position, device_position in self.positions.items():
            logical_row, logical_column = logical_position
            device_row, device_column = device_position
            frame[device_row][device_column] = pixel(logical_row, logical_column)

        payload = bytearray()
        for row, colours in enumerate(frame):
            payload.extend((row, 0, columns - 1))
            for colour in colours:
                payload.extend(colour)

        return bytes(payload)


def render_wheel_frame(layout, phase):
    """
    Render one colour wheel frame.
    """
    rows, columns = layout.logical_dims
    center_row = (rows - 1) / 2
    center_column = (columns - 1) / 2

    def pixel(row, column):
        angle = math.atan2(row - center_row, column - center_column)
        hue = ((angle / (2 * math.pi)) + phase) % 1
        red, green, blue = colorsys.hsv_to_rgb(hue, 1, 1)
        return round(red * 255), round(green * 255), round(blue * 255)

    return layout.render(pixel)


def wheel_phase(elapsed, direction, period=1.0):
    """
    Return the animation phase for a wheel direction.
    """
    phase = elapsed / period
    return -phase if direction == 1 else phase


def _blade_positions(row_maps):
    positions = {}
    for logical_row, columns in enumerate(row_maps):
        device_row = min(logical_row, 5)
        for logical_column, device_column in columns:
            positions[(logical_row, logical_column)] = (device_row, device_column)
    return positions


def _blade_key_positions():
    positions = {
        'ESC': (0, 0), 'INS': (0, 13), 'DELETE': (0, 14),
        'BACKTICK': (1, 0), 'DASH': (1, 11), 'EQUALS': (1, 12), 'BACKSPACE': (1, 14),
        'TAB': (2, 0), 'LEFTSQUAREBRACKET': (2, 11), 'RIGHTSQUAREBRACKET': (2, 12),
        'CAPSLK': (3, 0), 'SEMICOLON': (3, 10), 'APOSTROPHE': (3, 11),
        'LEFTSHIFT': (4, 0), 'COMMA': (4, 9), 'PERIOD': (4, 10), 'FORWARDSLASH': (4, 11), 'RIGHTSHIFT': (4, 14),
        'LEFTCTRL': (5, 0), 'SUPER': (5, 2), 'LEFTALT': (5, 3), 'SPACE': (5, 6), 'RIGHTALT': (5, 9), 'RIGHTCTRL': (5, 11),
        'LEFTARROW': (5, 12), 'HOME': (5, 12), 'UPARROW': (5, 13), 'PAGEUP': (5, 13),
        'RIGHTARROW': (5, 14), 'END': (5, 14), 'DOWNARROW': (6, 13), 'PAGEDOWN': (6, 13),
        'MUTE': (0, 1), 'VOL_DOWN': (0, 2), 'VOL_UP': (0, 3),
        'MEDIA_BACK': (0, 5), 'MEDIA_PLAY': (0, 6), 'MEDIA_FORWARD': (0, 7),
        'MACROMODE': (0, 9), 'GAMEMODE': (0, 10),
        'BRIGHTNESSDOWN': (0, 11), 'BRIGHTNESSUP': (0, 12), 'PRTSCR': (0, 13),
    }

    positions.update({'F{0}'.format(key): (0, key) for key in range(1, 13)})
    positions.update({key: (1, column) for column, key in enumerate(('1', '2', '3', '4', '5', '6', '7', '8', '9', '0'), 1)})
    positions.update({key: (2, column) for column, key in enumerate(('Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'), 1)})
    positions.update({key: (3, column) for column, key in enumerate(('A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L'), 1)})
    positions.update({key: (4, column) for column, key in enumerate(('Z', 'X', 'C', 'V', 'B', 'N', 'M'), 2)})
    return positions


_ANSI_ROWS = (
    tuple((column, column + 1) for column in range(15)),
    tuple((column, column + 1) for column in range(13)) + ((14, 15),),
    tuple((column, column + 1) for column in range(13)) + ((14, 15),),
    tuple((column, column + 1) for column in range(12)) + ((14, 15),),
    ((0, 1),) + tuple((column, column + 1) for column in range(2, 12)) + ((14, 15),),
    ((0, 1), (1, 2), (2, 3), (3, 5), (6, 6), (9, 9), (10, 10), (11, 11), (12, 12), (13, 13), (14, 14)),
    ((13, 15),),
)

_ISO_ROWS = (
    _ANSI_ROWS[0],
    _ANSI_ROWS[1],
    tuple((column, column + 1) for column in range(13)) + ((14, 14),),
    tuple((column, column + 1) for column in range(13)),
    tuple((column, column + 1) for column in range(12)) + ((14, 15),),
    _ANSI_ROWS[5],
    _ANSI_ROWS[6],
)

_JAPANESE_ROWS = (
    _ANSI_ROWS[0],
    tuple((column, column + 1) for column in range(15)),
    _ISO_ROWS[2],
    _ISO_ROWS[3],
    ((0, 1),) + tuple((column, column + 1) for column in range(2, 13)) + ((14, 15),),
    ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (6, 6), (8, 8), (9, 9), (10, 10), (11, 11), (12, 12), (13, 13), (14, 14)),
    _ANSI_ROWS[6],
)

_ANSI_KEYS = _blade_key_positions()
_ANSI_KEYS.update({'POUNDSIGN': (2, 14), 'RETURN': (3, 14)})

_ISO_KEYS = _blade_key_positions()
_ISO_KEYS.update({'RETURN': (2, 14), 'POUNDSIGN': (3, 12), 'BACKSLASH': (4, 1)})

_JAPANESE_KEYS = _blade_key_positions()
_JAPANESE_KEYS.update({
    'RETURN': (2, 14), 'POUNDSIGN': (3, 12),
    'YEN': (1, 13), 'RO': (4, 12),
    'MUHENKAN': (5, 4), 'HENKAN': (5, 8), 'KATAKANAHIRAGANA': (5, 9),
})

_JAPANESE_EVENTS = {
    89: 'RO',
    92: 'HENKAN',
    93: 'KATAKANAHIRAGANA',
    94: 'MUHENKAN',
    124: 'YEN',
}

_BLADE_ANSI_LAYOUT = MatrixLayout((7, 15), (6, 16), _blade_positions(_ANSI_ROWS), _ANSI_KEYS)
_BLADE_ISO_LAYOUT = MatrixLayout((7, 15), (6, 16), _blade_positions(_ISO_ROWS), _ISO_KEYS)
_BLADE_JAPANESE_LAYOUT = MatrixLayout((7, 15), (6, 16), _blade_positions(_JAPANESE_ROWS), _JAPANESE_KEYS, _JAPANESE_EVENTS)

BLADE_PRO_EARLY_2020_LAYOUTS = {
    'default': _BLADE_ANSI_LAYOUT,
    'en_US': _BLADE_ANSI_LAYOUT,
    'zh_TW': _BLADE_ANSI_LAYOUT,
    'ko_KR': _BLADE_ANSI_LAYOUT,
    'de_DE': _BLADE_ISO_LAYOUT,
    'fr_FR': _BLADE_ISO_LAYOUT,
    'en_GB': _BLADE_ISO_LAYOUT,
    'Nordic': _BLADE_ISO_LAYOUT,
    'ja_JP': _BLADE_JAPANESE_LAYOUT,
}


def select_matrix_layout(layouts, layout_name):
    """
    Select a firmware layout, falling back to the device default.
    """
    return layouts.get(layout_name, layouts['default'])
