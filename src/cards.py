"""Local card capture quality checks and durable, exact-text card library."""
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager


def text_key(text):
    # Preserve digits, punctuation and word boundaries: similar cards may differ
    # by a single rule. Camera movement must not create duplicate cards.
    return hashlib.sha256(' '.join(text.split()).encode('utf-8')).hexdigest()


def card_crop(frame, landscape=False):
    height, width = frame.shape[:2]
    ratio = 1.45 if landscape else .69
    h = min(int(height * .88), int(width * .88 / ratio))
    w = int(h * ratio)
    x, y = (width-w)//2, (height-h)//2
    return frame[y:y+h, x:x+w].copy(), (x, y, w, h)


class CaptureGate:
    def __init__(self):
        self.previous = None
        self.since = None

    def ready(self, frame, now=None):
        import cv2
        import numpy as np
        now = time.monotonic() if now is None else now
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (120, 160))
        sharp = cv2.Laplacian(gray, cv2.CV_64F).var() >= 55
        exposed = 25 < float(gray.mean()) < 235
        moved = self.previous is None or np.mean(np.abs(small.astype(float)-self.previous)) > 4
        self.previous = small.astype(float)
        if not sharp or not exposed or moved:
            self.since = now
            return False
        if self.since is None: self.since = now
        return now-self.since >= .8


class CardLibrary:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory/'cards.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, text TEXT NOT NULL, photo BLOB, created REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS readings (id TEXT, settings TEXT, text TEXT, PRIMARY KEY(id, settings))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db: yield db
        finally:
            db.close()

    def save(self, text, photo):
        key = text_key(text)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO cards VALUES (?, ?, ?, ?)', (key, text, photo, time.time()))
        return key

    def entries(self):
        with self.connect() as db:
            return db.execute('SELECT id, text FROM cards ORDER BY created DESC').fetchall()

    def reading(self, key, settings, value=None):
        settings = json.dumps(settings, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO readings VALUES (?, ?, ?)', (key, settings, value))
                return value
            row = db.execute('SELECT text FROM readings WHERE id=? AND settings=?', (key, settings)).fetchone()
            return row[0] if row else None


def readable_text(blocks):
    text = '\n'.join(b.text.strip() for b in blocks if b.text.strip())
    return text if sum(c.isalpha() for c in text) >= 8 and len(re.findall(r'\w+', text)) >= 2 else ''
