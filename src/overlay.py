"""Offline, portable Windows translation overlay. No game assets or network calls."""
from __future__ import annotations

import asyncio
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import wave
import winsound

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from PIL import Image, ImageGrab

RESOURCE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
HOME = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
DATA = HOME / 'data'
VERSION = '0.1.0'
user32 = C.WinDLL('user32', use_last_error=True)
kernel32 = C.WinDLL('kernel32', use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(C.c_void_p(-4))
except (AttributeError, OSError):
    user32.SetProcessDPIAware()

user32.GetForegroundWindow.restype = W.HWND
user32.GetAncestor.argtypes = [W.HWND, W.UINT]
user32.GetAncestor.restype = W.HWND
user32.GetWindowLongW.argtypes = [W.HWND, C.c_int]
user32.SetWindowLongW.argtypes = [W.HWND, C.c_int, W.LONG]
user32.SetWindowDisplayAffinity.argtypes = [W.HWND, W.DWORD]
user32.SetWindowDisplayAffinity.restype = W.BOOL
user32.GetClientRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
user32.ClientToScreen.argtypes = [W.HWND, C.POINTER(W.POINT)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.IsIconic.argtypes = [W.HWND]
user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
user32.GetWindowTextLengthW.argtypes = [W.HWND]
user32.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.OpenProcess.restype = W.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
kernel32.CloseHandle.argtypes = [W.HANDLE]


def native_handle(widget):
    hwnd = int(widget.winId())
    return user32.GetAncestor(hwnd, 2) or hwnd


def process_id(hwnd):
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    return pid.value


def find_game():
    found = []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    @callback_type
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = C.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        if 'journeys in middle-earth' not in title.value.lower():
            return True
        handle = kernel32.OpenProcess(0x1000, False, process_id(hwnd))
        if handle:
            try:
                path = C.create_unicode_buffer(32768)
                length = W.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, C.byref(length)):
                    if Path(path.value).name.lower() == 'jime.exe':
                        found.append(hwnd)
            finally:
                kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows.argtypes = [callback_type, W.LPARAM]
    user32.EnumWindows(callback, 0)
    return found[0] if len(found) == 1 else None


def client_box(hwnd):
    if not hwnd or user32.IsIconic(hwnd):
        return None
    rect, pt = W.RECT(), W.POINT()
    if not user32.GetClientRect(hwnd, C.byref(rect)) or not user32.ClientToScreen(hwnd, C.byref(pt)):
        return None
    if rect.right < 100 or rect.bottom < 100:
        return None
    return (pt.x, pt.y, pt.x + rect.right, pt.y + rect.bottom)


def active_game(hwnd):
    return bool(hwnd and user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd))


@dataclass
class Block:
    text: str
    x: float
    y: float
    width: float
    height: float
    line_height: float
    translated: str = ''


def group_lines(lines):
    """Split horizontal controls, then join visually adjacent paragraph lines."""
    fragments = []
    for line in lines:
        words = list(line.words)
        run = []
        for word in words:
            r = word.bounding_rect
            if run:
                last = run[-1].bounding_rect
                if r.x - (last.x + last.width) > max(r.height * 2.4, 38):
                    fragments.append(run)
                    run = []
            run.append(word)
        if run:
            fragments.append(run)
    rows = []
    for words in fragments:
        txt = ' '.join(w.text for w in words).strip()
        if sum(c.isalpha() for c in txt) < 2:
            continue
        rects = [w.bounding_rect for w in words]
        x, y = min(r.x for r in rects), min(r.y for r in rects)
        right, bottom = max(r.x+r.width for r in rects), max(r.y+r.height for r in rects)
        rows.append(Block(txt, x, y, right-x, bottom-y, bottom-y))
    blocks = []
    for row in sorted(rows, key=lambda b: (b.y, b.x)):
        candidates = []
        for block in blocks:
            gap = row.y - (block.y + block.height)
            overlap = min(block.x+block.width, row.x+row.width)-max(block.x, row.x)
            aligned = abs(row.x-block.x) < max(24, row.line_height*1.7)
            if (0 <= gap <= row.line_height*0.9 and aligned and overlap > 0
                    and 0.65 < row.line_height / max(block.line_height, 1) < 1.5):
                candidates.append((gap, block))
        if candidates:
            block = min(candidates, key=lambda p: p[0])[1]
            right = max(block.x+block.width, row.x+row.width)
            block.text += ' ' + row.text
            block.width = right-block.x
            block.height = row.y+row.height-block.y
        else:
            blocks.append(row)
    return blocks


class Translator:
    def __init__(self):
        import ctranslate2
        import sentencepiece
        folder = RESOURCE/'model'/'translate-en_nl-1_8'
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder/'sentencepiece.model'))
        self.model = ctranslate2.Translator(str(folder/'model'), device='cpu', compute_type='int8',
                                          inter_threads=1, intra_threads=4)
        self.path = DATA/'translations.json'
        try:
            self.cache = json.loads(self.path.read_text(encoding='utf-8'))
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
        custom = HOME/'glossary.json'
        if custom.exists():
            self.glossary.update(json.loads(custom.read_text(encoding='utf-8')))

    def translate(self, text):
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
        output = re.sub(r'\bMiddle[- ]earth\b', 'Midden-aarde', output, flags=re.I)
        output = re.sub(r'\s+([,.!?;:])', r'\1', output)
        self.cache[text] = output
        if len(self.cache) > 15000:
            self.cache.pop(next(iter(self.cache)))
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
        return output


class Recognizer:
    def __init__(self):
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        self.engine = OcrEngine.try_create_from_language(Language('en-US'))
        if not self.engine:
            raise RuntimeError('Engelse Windows-tekstherkenning ontbreekt. Installeer Engels (VS) met tekstherkenning via Windows Instellingen > Tijd en taal > Taal en regio.')

    async def read(self, image):
        from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
        from winrt.windows.storage.streams import Buffer
        factor = min(1.0, 2500/max(image.size))
        if factor < 1:
            image = image.resize((int(image.width*factor), int(image.height*factor)))
        raw = image.convert('RGBA').tobytes('raw', 'BGRA')
        buffer = Buffer(len(raw))
        buffer.length = len(raw)
        memoryview(buffer)[:] = raw
        bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, image.width, image.height, BitmapAlphaMode.PREMULTIPLIED)
        try:
            bitmap.copy_from_buffer(buffer)
            result = await self.engine.recognize_async(bitmap)
            blocks = group_lines(result.lines)
            for b in blocks:
                b.x /= factor; b.y /= factor; b.width /= factor; b.height /= factor; b.line_height /= factor
            return blocks
        finally:
            bitmap.close()


class Speaker:
    def __init__(self, events):
        self.events = events
        self.queue = queue.Queue()
        self.generation = 0
        self.closed = False
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.generation += 1
        winsound.PlaySound(None, 0)

    def say(self, text):
        self.stop()
        self.queue.put((self.generation, text))

    def run(self):
        try:
            from piper import PiperVoice, SynthesisConfig
            self.voice = PiperVoice.load(str(RESOURCE/'voice'/'nl_NL-mls-medium.onnx'))
            self.config = SynthesisConfig(speaker_id=0, length_scale=1.05)
        except Exception as exc:
            logging.exception('Voice initialization')
            self.events.put(('speech_error', str(exc)))
            return
        while not self.closed:
            try:
                generation, text = self.queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if generation != self.generation:
                continue
            try:
                for i, chunk in enumerate(re.split(r'(?<=[.!?])\s+', text)):
                    if generation != self.generation or self.closed:
                        break
                    path = DATA/f'speech-{generation % 2}-{i % 2}.wav'
                    with wave.open(str(path), 'wb') as out:
                        self.voice.synthesize_wav(chunk, out, syn_config=self.config)
                    if generation != self.generation or self.closed:
                        break
                    with wave.open(str(path), 'rb') as inp:
                        duration = inp.getnframes()/inp.getframerate()
                    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    deadline = time.monotonic()+duration
                    while generation == self.generation and not self.closed and time.monotonic() < deadline:
                        time.sleep(0.05)
            except Exception as exc:
                logging.exception('Speech')
                self.events.put(('speech_error', str(exc)))



def main():
    try:
        from qtui import App
        App().run()
    except Exception:
        details = __import__('traceback').format_exc()
        try:
            DATA.mkdir(exist_ok=True)
            (DATA/'startup-error.txt').write_text(details, encoding='utf-8')
        except OSError:
            pass
        user32.MessageBoxW(None, 'De overlay kon niet starten. Details staan in data/startup-error.txt.\n\n'+details[-1200:], 'Middenaarde Overlay', 0x10)


if __name__ == '__main__':
    main()
