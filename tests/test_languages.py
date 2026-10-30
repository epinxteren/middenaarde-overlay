import copy
import hashlib
import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import platform_support
import languagepacks
from languages import LANGUAGES, speech_profile, speech_language
from translation import Translator
import speech


class LanguageTests(unittest.TestCase):
    def test_voice_matches_language_without_mutating_saved_profile(self):
        for provider in ('edge', 'azure'):
            original = copy.deepcopy(speech.DEFAULTS[provider])
            for language in LANGUAGES:
                profile = speech_profile(provider, original, language)
                self.assertTrue(profile['voice'].startswith(language + '-'))
                self.assertEqual(profile['language'], language)
            self.assertEqual(original, speech.DEFAULTS[provider])
        self.assertEqual(speech_language('de', 'same'), 'de')
        self.assertEqual(speech_language('de', 'fr'), 'fr')

    def test_english_requires_no_model_and_preserves_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            translator = Translator('en', folder, folder, folder)
            self.assertEqual(translator.translate('Continue to Middle-earth!'), 'Continue to Middle-earth!')
            self.assertTrue(languagepacks.model_available('en', folder, folder))

    def test_translation_caches_and_glossaries_do_not_cross_languages(self):
        with tempfile.TemporaryDirectory() as tmp, patch('languagepacks.model_folder', return_value=Path('.')), \
                patch('ctranslate2.Translator'), patch('sentencepiece.SentencePieceProcessor'):
            folder = Path(tmp)
            (folder / 'translations.json').write_text(json.dumps({'A story': 'Een verhaal'}))
            (folder / 'translations-de.json').write_text(json.dumps({'A story': 'Eine Geschichte'}))
            (folder / 'glossary.json').write_text(json.dumps({'Hero': 'Held'}))
            nl = Translator('nl', folder, folder, folder)
            de = Translator('de', folder, folder, folder)
            self.assertEqual(nl.translate('A story'), 'Een verhaal')
            self.assertEqual(de.translate('A story'), 'Eine Geschichte')
            self.assertEqual(nl.translate('Hero'), 'Held')
            self.assertNotIn('Hero', de.glossary)
            self.assertNotIn('Continue', de.glossary)

    def test_piper_rejects_other_languages_before_loading_model(self):
        with self.assertRaisesRegex(speech.SpeechError, 'alleen Nederlands'):
            speech.synthesize('piper', dict(speech.DEFAULTS['piper'], language='de'), '', 'Hallo', Path('.'))

    def test_azure_uses_selected_language_and_keeps_matching_voice(self):
        profile = dict(speech.DEFAULTS['azure'], language='fr', voice='fr-CA-SylvieNeural')
        with patch('speech.request', return_value=b'wave') as request:
            speech.synthesize('azure', profile, 'key', 'Bonjour.', Path('.'))
        payload = request.call_args.args[3].decode()
        self.assertIn('xml:lang="fr-FR"', payload)
        self.assertIn('fr-CA-SylvieNeural', payload)
        self.assertIn('Bonjour.', payload)

    def test_google_receives_language_and_translated_text(self):
        profile = dict(speech.DEFAULTS['google'], language='de')
        with patch('speech.request', return_value=b'{}') as request:
            with self.assertRaises(speech.SpeechError):
                speech.synthesize('google', profile, 'key', 'Die Helden kommen.', Path('.'))
        prompt = request.call_args.args[3]['contents'][0]['parts'][0]['text']
        self.assertIn('Read in German.', prompt)
        self.assertIn('Die Helden kommen.', prompt)

    def test_eleven_flash_passes_language(self):
        profile = dict(speech.DEFAULTS['eleven'], language='es', model='eleven_flash_v2_5', voice='voice')
        with patch('speech.request', side_effect=speech.SpeechError('stop before decoding')) as request:
            with self.assertRaises(speech.SpeechError):
                speech.synthesize('eleven', profile, 'key', 'Los héroes.', Path('.'))
        self.assertEqual(request.call_args.args[3]['language_code'], 'es')

    def test_voice_listing_filters_selected_language(self):
        voices = [{'DisplayName': 'Deutsch', 'ShortName': 'de-DE-ConradNeural', 'Locale': 'de-DE'},
                  {'DisplayName': 'Dutch', 'ShortName': 'nl-NL-MaartenNeural', 'Locale': 'nl-NL'}]
        with patch('speech.request', return_value=json.dumps(voices).encode()):
            result = speech.voices('azure', dict(speech.DEFAULTS['azure'], language='de'), 'key')
        self.assertEqual(result, [('Deutsch', 'de-DE-ConradNeural')])

    def test_speaker_snapshots_language_for_queue_and_audio_cache(self):
        with tempfile.TemporaryDirectory() as tmp, patch('overlay.DATA', Path(tmp)), \
                patch('credentials.load_keys', return_value={}), patch('speech.threading.Thread.start'):
            speaker = speech.Speaker(queue.Queue())
            saved = copy.deepcopy(speaker.settings)
            speaker.say('Hello.', language='en')
            _, _, settings, _ = speaker.queue.get_nowait()
            self.assertEqual(settings['profiles'][settings['provider']]['language'], 'en')
            self.assertEqual(speaker.settings, saved)
            speaker.closed = True


class PackageTests(unittest.TestCase):
    def archive(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name in ('sentencepiece.model', 'model/model.bin', 'model/shared_vocabulary.txt', 'README.md'):
                archive.writestr('en_es/' + name, b'fixture')
            archive.writestr('en_es/metadata.json', json.dumps({'from_code': 'en', 'to_code': 'es'}))
            archive.writestr('en_es/../../unexpected', b'do not extract')
        return output.getvalue()

    def test_verified_install_and_existing_package_needs_no_network(self):
        content = self.archive()
        manifest = ('test-es', 'en_es', len(content), hashlib.sha256(content).hexdigest())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(languagepacks.PACKAGES, es=manifest), \
                patch('languagepacks.urllib.request.urlopen', return_value=io.BytesIO(content)) as download:
            folder = Path(tmp)
            languagepacks.install_language('es', folder, folder)
            self.assertTrue(languagepacks.model_available('es', folder, folder))
            self.assertFalse((folder / 'language-models' / 'unexpected').exists())
            self.assertTrue((languagepacks.model_folder('es', folder, folder) / 'README.md').exists())
            languagepacks.install_language('es', folder, folder)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(list((folder / 'language-models').glob('.download-*')), [])

    def test_corrupted_download_is_not_installed_and_can_be_retried(self):
        content = self.archive()
        manifest = ('test-es', 'en_es', len(content), '0' * 64)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(languagepacks.PACKAGES, es=manifest), \
                patch('languagepacks.urllib.request.urlopen', return_value=io.BytesIO(content)):
            folder = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'Controle'):
                languagepacks.install_language('es', folder, folder)
            self.assertFalse(languagepacks.model_available('es', folder, folder))
            self.assertEqual(list((folder / 'language-models').iterdir()), [])


if __name__ == '__main__': unittest.main()
