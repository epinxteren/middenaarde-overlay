"""Persistent local OCR and audio caches, independent of public sharing."""
import hashlib
import json
import logging
import os
import sys
import copy
from pathlib import Path
from dataclasses import asdict


def cache_directory(data):
    # Stable across downloaded versions/install folders; tests/development stay
    # isolated in their supplied data folder.
    if getattr(sys, 'frozen', False) and os.environ.get('LOCALAPPDATA'):
        return Path(os.environ['LOCALAPPDATA'])/'MiddenaardeOverlay'/'cache'
    return data


class DiskAudioCache:
    def __init__(self, directory, limit=None):
        self.directory, self.limit = directory, limit

    def get(self, key):
        from sharedcache import valid_audio
        try:
            path = self.directory/(key+'.wav')
            data = path.read_bytes()
            if valid_audio(data):
                path.touch()
                logging.info('Audio: lokale schijfcache gebruikt')
                return data
        except OSError: pass
        return None

    def put(self, key, data):
        from sharedcache import valid_audio
        if not valid_audio(data): return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory/(key+'.tmp')
            temporary.write_bytes(data)
            temporary.replace(self.directory/(key+'.wav'))
            if self.limit is None: return
            files = sorted(self.directory.glob('*.wav'), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            for path in files:
                if total <= self.limit: break
                total -= path.stat().st_size
                path.unlink()
        except OSError: pass


class CachedRecognizer:
    def __init__(self, recognizer, directory, identity, similarity=.98):
        self.recognizer, self.directory, self.identity = recognizer, directory, identity
        self.backend, self.warning = recognizer.backend, recognizer.warning
        self.similarity = similarity
        self.pages = {}
        self.known_texts = set()
        for path in directory.glob('*.regions.json'):
            try:
                page = json.loads(path.read_text('utf-8'))
                if page['identity'] == identity:
                    index = json.dumps([page['size'], page['layout']])
                    self.pages.setdefault(index, []).append(page)
                    self.known_texts.update(item['text'] for item in page['blocks'])
            except (OSError, ValueError, KeyError, TypeError): pass

    @staticmethod
    def region_image(image, item):
        box = (max(0,int(item['x'])-4), max(0,int(item['y'])-4),
               min(image.width,int(item['x']+item['width']+5)),
               min(image.height,int(item['y']+item['height']+5)))
        return image.crop(box)

    @staticmethod
    def region_key(image, item):
        return hashlib.sha256(CachedRecognizer.region_image(image,item).tobytes()).hexdigest()

    def lookup(self, image):
        from overlay import Block
        rgb = image.convert('RGB')
        digest = hashlib.sha256(json.dumps([1, self.identity, rgb.size]).encode()+rgb.tobytes()).hexdigest()
        path = self.directory/(digest+'.json')
        try:
            blocks = [Block(**item) for item in json.loads(path.read_text('utf-8'))]
            path.touch()
            logging.info('OCR: lokale beeldcache gebruikt')
            return blocks
        except (OSError, ValueError, TypeError): pass
        # Require both matching text layout and exact pixels in EVERY known
        # text box. Animation outside the text no longer invalidates the page.
        # Layout protects against silently omitting newly added text blocks.
        if self.pages:
            from textdetect import detect_text
            layout = detect_text(rgb).layout
            index = json.dumps([list(rgb.size), layout])
            candidates=list(self.pages.get(index, []))
            for other_index,pages in self.pages.items():
                if other_index==index: continue
                size,other_layout=json.loads(other_index)
                if size!=list(rgb.size) or len(other_layout)!=len(layout): continue
                if all(abs(a-b)<=1 for row,other in zip(layout,other_layout) for a,b in zip(row,other)):
                    candidates.extend(pages)
            for page in candidates:
                from visualmatch import similarity
                scores = []
                for i,item in enumerate(page['blocks']):
                    if self.region_key(rgb,item)==page['fingerprints'][i]: scores.append(1.)
                    elif 'letters' in page:
                        scores.append(similarity(page['letters'][i],self.region_image(rgb,item),self.similarity))
                    else: scores.append(0.)
                    if scores[-1]<self.similarity: break
                if len(scores)==len(page['blocks']) and min(scores,default=0)>=self.similarity:
                    logging.info('OCR: bekende tekstvakken hergebruikt; achtergrond genegeerd')
                    return [Block(**item) for item in page['blocks']]
        return None

    async def read(self, image):
        cached = self.lookup(image)
        if cached is not None: return cached
        rgb = image.convert('RGB')
        digest = hashlib.sha256(json.dumps([1, self.identity, rgb.size]).encode()+rgb.tobytes()).hexdigest()
        path = self.directory/(digest+'.json')
        blocks = copy.deepcopy(await self.recognizer.read(image))
        from textmatch import canonical_text
        for block in blocks:
            repaired=canonical_text(block.text,self.known_texts,self.similarity)
            if repaired!=block.text: logging.info('OCR: kleine herkenningsafwijking hersteld met opgeslagen tekst')
            block.text=repaired
        # Empty results may be transient; do not persist them.
        if blocks:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps([asdict(b) for b in blocks], ensure_ascii=False), 'utf-8')
                temporary.replace(path)
                from textdetect import detect_text
                from visualmatch import fingerprint
                items = [asdict(b) for b in blocks]
                page = {'identity':self.identity, 'size':list(rgb.size), 'layout':detect_text(rgb).layout,
                        'blocks':items, 'fingerprints':[self.region_key(rgb,item) for item in items],
                        'letters':[fingerprint(self.region_image(rgb,item)) for item in items]}
                self.known_texts.update(b.text for b in blocks)
                index = json.dumps([page['size'],page['layout']])
                self.pages.setdefault(index, []).append(page)
                region_path = self.directory/(digest+'.regions.json')
                temporary = region_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(page, ensure_ascii=False), 'utf-8')
                temporary.replace(region_path)
            except OSError: pass
        return blocks
