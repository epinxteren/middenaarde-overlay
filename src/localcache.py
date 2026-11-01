"""Persistent local OCR and audio caches, independent of public sharing."""
import hashlib
import json
import logging
import os
import sys
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
    def __init__(self, recognizer, directory, identity):
        self.recognizer, self.directory, self.identity = recognizer, directory, identity
        self.backend, self.warning = recognizer.backend, recognizer.warning

    async def read(self, image):
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
        blocks = await self.recognizer.read(image)
        # Empty results may be transient; do not persist them.
        if blocks:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps([asdict(b) for b in blocks], ensure_ascii=False), 'utf-8')
                temporary.replace(path)
            except OSError: pass
        return blocks
