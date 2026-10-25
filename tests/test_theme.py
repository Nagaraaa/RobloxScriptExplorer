import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from explorer_theme import TEXT_PAIRS, THEMES, contrast, resolve


class ThemeTests(unittest.TestCase):
    def test_light_and_dark_define_the_same_tokens(self):
        self.assertEqual(set(THEMES['light']), set(THEMES['dark']))

    def test_text_tokens_reach_wcag_aa_contrast(self):
        for name, palette in THEMES.items():
            for foreground, background in TEXT_PAIRS:
                with self.subTest(theme=name, pair=(foreground, background)):
                    self.assertGreaterEqual(contrast(palette[foreground], palette[background]), 4.5)

    def test_explicit_modes_win_over_the_system_theme(self):
        self.assertEqual(resolve('light'), 'light')
        self.assertEqual(resolve('dark'), 'dark')
        self.assertIn(resolve('system'), THEMES)

    def test_gui_colours_come_from_the_theme_tokens(self):
        source = (Path(__file__).resolve().parents[1] / 'explorer_gui.py').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{6}\b', source), [])


if __name__ == '__main__':
    unittest.main()
