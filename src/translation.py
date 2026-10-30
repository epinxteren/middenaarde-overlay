"""Offline English-to-selected-language translation with separate persistent caches."""
import json
import re


class Translator:
    def __init__(self, language="nl", resource=None, data=None, home=None):
        from languagepacks import model_folder
        from languages import language_code
        if resource is None or data is None or home is None:
            from overlay import RESOURCE, DATA, HOME
            resource, data, home = resource or RESOURCE, data or DATA, home or HOME
        self.language = language_code(language)
        data.mkdir(parents=True, exist_ok=True)
        if self.language != 'en':
            import ctranslate2
            import sentencepiece
            folder = model_folder(self.language, resource, data)
            self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder/'sentencepiece.model'))
            self.model = ctranslate2.Translator(str(folder/'model'), device='cpu', compute_type='int8',
                                              inter_threads=1, intra_threads=4)
        self.path = data/('translations.json' if self.language == 'nl' else f'translations-{self.language}.json')
        try:
            self.cache = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.cache, dict): self.cache = {}
        except (OSError, ValueError):
            self.cache = {}
        self.glossary = {
            'Continue': 'Doorgaan', 'Confirm': 'Bevestigen', 'Cancel': 'Annuleren',
            'New Game': 'Nieuw spel', 'Load Game': 'Spel laden', 'Settings': 'Instellingen',
            'Collection': 'Collectie', 'Quit': 'Afsluiten', 'Back': 'Terug', 'Next': 'Volgende',
            'Yes': 'Ja', 'No': 'Nee', 'Main Menu': 'Hoofdmenu', 'End Turn': 'Beurt beëindigen',
            'Attack': 'Aanvallen', 'Search': 'Doorzoeken', 'Interact': 'Interactie',
            'Difficulty': 'Moeilijkheid', 'Normal': 'Normaal', 'Hard': 'Moeilijk',
            'Adventure': 'Avontuur', 'Heroes': 'Helden', 'Campaign': 'Campagne',
            'Journeys in Middle-earth': 'Reizen door Midden-aarde', 'Options': 'Opties',
            'Save and Quit': 'Opslaan en afsluiten', 'Inventory': 'Inventaris', 'Map': 'Kaart',
        }
        if self.language != 'nl': self.glossary = {}
        custom = home/('glossary.json' if self.language == 'nl' else f'glossary-{self.language}.json')
        if custom.exists():
            self.glossary.update(json.loads(custom.read_text(encoding='utf-8')))

    def translate(self, text):
        if self.language == 'en': return text
        if text in self.glossary:
            return self.glossary[text]
        if text in self.cache:
            return self.cache[text]
        # Bound long paragraphs; sentencepiece uses the same boundary marker on target pieces.
        parts = re.split(r'(?<=[.!?])\s+', text)
        chunks, current = [], ''
        for part in parts:
            if len(current)+len(part) > 650 and current:
                chunks.append(current)
                current = ''
            if len(part) > 650:
                words = part.split()
                while words:
                    chunks.append(' '.join(words[:75]))
                    words = words[75:]
            else:
                current = (current+' '+part).strip()
        if current:
            chunks.append(current)
        tokens = [self.tokenizer.encode(t, out_type=str) for t in chunks]
        results = self.model.translate_batch(tokens, beam_size=3, max_decoding_length=384)
        output = ' '.join(''.join(r.hypotheses[0]).replace('▁', ' ').strip() for r in results)
        if self.language == 'nl':
            output = re.sub(r'\bMiddle[- ]earth\b', 'Midden-aarde', output, flags=re.I)
        output = re.sub(r'\s+([,.!?;:])', r'\1', output)
        self.cache[text] = output
        if len(self.cache) > 15000:
            self.cache.pop(next(iter(self.cache)))
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
        return output

