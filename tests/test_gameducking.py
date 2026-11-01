import platform_support
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from gameducking import attenuate_sessions


def session(name, initial=.8):
    state = [initial]
    volume = MagicMock()
    volume.GetMasterVolume.side_effect = lambda: state[0]
    volume.SetMasterVolume.side_effect = lambda value, _: state.__setitem__(0, value)
    return SimpleNamespace(Process=SimpleNamespace(name=lambda: name), SimpleAudioVolume=volume), state


class DuckingTests(unittest.TestCase):
    def test_only_game_changes_and_restores_on_failure(self):
        game, game_level = session('JiME.exe')
        other, other_level = session('music.exe')
        with self.assertRaises(RuntimeError):
            with attenuate_sessions([game, other], 25):
                self.assertAlmostEqual(game_level[0], .2)
                self.assertEqual(other_level[0], .8)
                raise RuntimeError('Playback failed or cancelled')
        self.assertEqual(game_level[0], .8)
        other.SimpleAudioVolume.SetMasterVolume.assert_not_called()

    def test_manual_change_is_preserved(self):
        game, level = session('JiME.exe')
        with attenuate_sessions([game], 30): level[0] = .5
        self.assertEqual(level[0], .5)

    def test_zero_mutes_temporarily_and_full_leaves_level(self):
        game, level = session('JiME.exe')
        with attenuate_sessions([game], 0): self.assertEqual(level[0], 0)
        self.assertEqual(level[0], .8)
        with attenuate_sessions([game], 100): self.assertEqual(level[0], .8)
