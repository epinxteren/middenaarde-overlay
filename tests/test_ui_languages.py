import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import platform_support
from overlay import Block
import qtui
import speech
from voiceui import VoiceDialog


class UiLanguageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        win32 = MagicMock()
        win32.GetWindowLongW.return_value = 0
        win32.SetLayeredWindowAttributes.return_value = 1
        win32.SetWindowDisplayAffinity.return_value = 1
        self.speaker = MagicMock()
        self.speaker.settings = {'provider': 'edge', 'profiles': copy.deepcopy(speech.DEFAULTS)}
        self.speaker.keys = {}
        self.speaker.busy = False
        for patcher in (
            patch('qtui.DATA', Path(self.folder.name)),
            patch('qtui.logging.basicConfig'),
            patch('qtui.user32', win32),
            patch('qtui.native_handle', return_value=1),
            patch('qtui.model_available', return_value=True),
            patch('qtui.Speaker', return_value=self.speaker),
            patch('qtui.App.worker'),
            patch('qtui.active_game', return_value=True),
            patch('qtui.client_box', return_value=(0, 0, 800, 600)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = qtui.App()
        self.app.timer.stop()
        self.app.ready = True
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.closing.set()
        self.app.timer.stop()
        self.app.qt.removeNativeEventFilter(self.app.hotkey_filter)
        self.app.overlay.close()
        self.app.root.close()

    def set_languages(self, overlay, narration):
        self.app.overlay_language.setCurrentIndex(self.app.overlay_language.findData(overlay))
        self.app.narration_language.setCurrentIndex(self.app.narration_language.findData(narration))

    def render(self, epoch=None):
        self.app.enabled.set()
        self.app.target = 1
        blocks = [Block('The heroes walk through the forest and discover an ancient tower in the distance.',
                        10, 20, 600, 100, 20, 'Die Helden sehen einen Turm.', 'Les héros voient une tour.')]
        self.app.events.put(('render', (self.app.epoch if epoch is None else epoch, 1, (0, 0, 800, 600), blocks, ('page',))))
        self.app.poll()
        return blocks

    def test_hidden_overlay_keeps_preview_and_narration_and_disables_clicks(self):
        self.set_languages('de', 'fr')
        self.app.show_text.setChecked(False)
        self.speaker.reset_mock()
        blocks = self.render()
        self.assertFalse(self.app.overlay.isVisible())
        self.assertFalse(self.app.click_to_speak.isEnabled())
        self.assertIsNone(self.app.overlay.click_handler)
        self.assertEqual(self.app.preview.toPlainText(), blocks[0].translated)
        self.speaker.say.assert_called_once_with(blocks[0].narrated, language='fr')
        self.app.repeat()
        self.speaker.say.assert_called_with(blocks[0].narrated, language='fr')
        self.app.overlay.show_progress((0, 0, 800, 600), 'Spraak laden…')
        self.assertFalse(self.app.overlay.isVisible())
        self.app.show_text.setChecked(True)
        self.assertTrue(self.app.overlay.isVisible())
        self.assertTrue(self.app.click_to_speak.isEnabled())
        self.app.qt.processEvents()
        self.assertEqual(self.app.overlay.click_regions[0][1], blocks[0].narrated)
        self.app.click_speech(blocks[0].narrated)
        self.speaker.say.assert_called_with(blocks[0].narrated, language='fr')

    def test_language_change_discards_previous_page_and_saves_preferences(self):
        self.render()
        old_epoch = self.app.epoch
        self.set_languages('de', 'en')
        self.app.show_text.setChecked(False)
        self.speaker.reset_mock()
        self.render(epoch=old_epoch)
        self.speaker.say.assert_not_called()
        self.assertEqual(self.app.latest_text, '')
        self.assertEqual(self.app.overlay.blocks, [])
        settings = qtui.QSettings(str(Path(self.folder.name) / 'ui.ini'), qtui.QSettings.Format.IniFormat)
        self.assertEqual(settings.value('overlay_language'), 'de')
        self.assertEqual(settings.value('narration_language'), 'en')
        self.assertFalse(settings.value('show_overlay_text', type=bool))

    def test_missing_model_pauses_and_requires_explicit_download(self):
        self.app.enabled.set()
        with patch('qtui.model_available', side_effect=lambda lang, *_: lang != 'de'), \
                patch('qtui.install_language') as install:
            self.set_languages('de', 'nl')
            self.assertFalse(self.app.enabled.is_set())
            self.assertFalse(self.app.start_button.isEnabled())
            self.assertFalse(self.app.download_button.isHidden())
            self.app.toggle()
            self.assertFalse(self.app.enabled.is_set())
            install.assert_not_called()

    def test_voice_dialog_uses_selected_language_and_sample(self):
        dialog = VoiceDialog(self.app.root, self.speaker, 'de')
        try:
            self.assertEqual(dialog.voice.currentText(), 'de-DE-ConradNeural')
            self.assertIn('Die Helden', dialog.sample.toPlainText())
            dialog.preview()
            self.assertEqual(self.speaker.say.call_args.kwargs['language'], 'de')
        finally:
            dialog.close()


class WorkerLanguageTests(unittest.TestCase):
    def test_worker_translates_overlay_and_narration_independently(self):
        class StopAfterThreeScans:
            count = 0
            def is_set(self): return False
            def wait(self, _):
                self.count += 1
                return self.count > 3
        class Recognizer:
            async def read(self, image):
                return [Block('The heroes enter the forest.', 1, 2, 200, 100, 20)]
        import queue
        import threading
        app = object.__new__(qtui.App)
        app.closing = StopAfterThreeScans()
        app.enabled = threading.Event(); app.enabled.set()
        app.events = queue.Queue()
        app.epoch, app.region, app.min_chars = 0, None, 0
        app.output_language, app.narration_choice = 'de', 'fr'
        def translator(language):
            model = MagicMock()
            model.translate.side_effect = lambda text: language + ': ' + text
            return model
        with patch('qtui.Recognizer', Recognizer), patch('qtui.Translator', side_effect=translator) as models, \
                patch('qtui.find_game', return_value=1), patch('qtui.client_box', return_value=(0, 0, 800, 600)), \
                patch('qtui.active_game', return_value=True), patch('qtui.ImageGrab.grab'):
            app.worker()
        events = list(app.events.queue)
        renders = [value for kind, value in events if kind == 'render']
        self.assertEqual(len(renders), 1)
        block = renders[0][3][0]
        self.assertEqual(block.translated, 'de: ' + block.text)
        self.assertEqual(block.narrated, 'fr: ' + block.text)
        self.assertEqual({call.args[0] for call in models.call_args_list}, {'de', 'fr'})
        self.assertEqual(models.call_count, 2)


if __name__ == '__main__': unittest.main()
