"""Light and dark design tokens for the Roblox Script Explorer UI."""
from __future__ import annotations

import os

# Every colour used by the interface lives here. Text tokens keep a WCAG
# contrast ratio of at least 4.5:1 against the surfaces they are drawn on.
THEMES = {
    'dark': {
        'bg': '#0f141b',           # window background
        'surface': '#171d27',      # panels and cards
        'raised': '#212a37',       # buttons, table headings
        'hover': '#2b3646',        # hovered buttons and rows
        'pressed': '#344257',
        'border': '#2c3747',
        'text': '#e8edf4',
        'muted': '#a3b1c4',
        'disabled': '#7d8ea6',
        'accent': '#ffb454',       # primary action background
        'accent_hover': '#ffc678',
        'on_accent': '#1a1205',
        'accent_text': '#ffb454',  # accent used as text on surfaces
        'focus': '#7cb8ff',
        'success': '#6fd6ae',
        'warning': '#ffb454',
        'danger': '#ff8f9c',
        'selection': '#2a4a6b',
        'on_selection': '#ffffff',
        'code_bg': '#0f141b',
        'gutter': '#7d8ea6',
        'keyword': '#c6a0f6',
        'string': '#a7d99b',
        'number': '#f1b477',
        'comment': '#8496ab',
        'function': '#8bd5ee',
        'match': '#5c4718',
        'on_match': '#e8edf4',
        'active_match': '#ffb454',
        'on_active_match': '#1a1205',
    },
    'light': {
        'bg': '#f4f6f9',
        'surface': '#ffffff',
        'raised': '#eef1f5',
        'hover': '#e2e7ee',
        'pressed': '#d5dce5',
        'border': '#d5dce5',
        'text': '#17202c',
        'muted': '#556274',
        'disabled': '#6b7686',
        'accent': '#b45309',
        'accent_hover': '#9a4507',
        'on_accent': '#ffffff',
        'accent_text': '#a24a06',
        'focus': '#2563eb',
        'success': '#0f7a55',
        'warning': '#a24a06',
        'danger': '#b42335',
        'selection': '#d4e4f7',
        'on_selection': '#0b1b2e',
        'code_bg': '#fbfcfd',
        'gutter': '#646f7f',
        'keyword': '#7c3aed',
        'string': '#2f7d32',
        'number': '#b45309',
        'comment': '#5f6b7a',
        'function': '#0e6f8c',
        'match': '#fde68a',
        'on_match': '#17202c',
        'active_match': '#b45309',
        'on_active_match': '#ffffff',
    },
}

# (foreground, background) pairs that carry readable text, checked by the tests.
TEXT_PAIRS = [('text', 'bg'), ('text', 'surface'), ('text', 'raised'), ('text', 'hover'),
              ('muted', 'bg'), ('muted', 'surface'), ('muted', 'raised'),
              ('accent_text', 'surface'), ('on_accent', 'accent'), ('on_accent', 'accent_hover'),
              ('success', 'surface'), ('warning', 'surface'), ('danger', 'surface'),
              ('on_selection', 'selection'), ('on_match', 'match'), ('on_active_match', 'active_match'),
              ('text', 'code_bg'), ('gutter', 'code_bg'), ('keyword', 'code_bg'), ('string', 'code_bg'),
              ('number', 'code_bg'), ('comment', 'code_bg'), ('function', 'code_bg')]

MODES = ('system', 'light', 'dark')


def contrast(first: str, second: str) -> float:
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def system_theme() -> str:
    """Windows app theme ('light' or 'dark'); dark when it cannot be read."""
    if os.name != 'nt':
        return 'dark'
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
            return 'light' if winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] else 'dark'
    except OSError:
        return 'dark'


def resolve(mode: str | None) -> str:
    return mode if mode in ('light', 'dark') else system_theme()


def set_title_bar(window, dark: bool) -> None:
    """Match the native Windows 10/11 title bar to the theme when supported."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, then the pre-20H1 id
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError):
        pass
