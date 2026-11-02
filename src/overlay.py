"""Portable Windows translation overlay. OCR and translation run locally."""
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
    sys.path.append(str(Path(__file__).resolve().parents[1] / 'lib'))

from PIL import Image, ImageGrab
from translation import Translator

RESOURCE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
HOME = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
DATA = HOME / 'data'
VERSION = '0.4.5'
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
    narrated: str = ''


def merge_blocks(blocks, max_gap=24):
    """Join connected text areas in physical pixels, independent of alignment.

    Work on copies so regrouping never mutates OCR cache entries. Recheck the
    resulting rectangles: joining staggered rows can create a new intersection.
    """
    from dataclasses import replace
    groups = [[replace(b)] for b in sorted(blocks, key=lambda b: (b.y, b.x))]

    def bounds(group):
        return (min(b.x for b in group), min(b.y for b in group),
                max(b.x+b.width for b in group), max(b.y+b.height for b in group))

    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            a = bounds(groups[i])
            for j in range(i+1, len(groups)):
                b = bounds(groups[j])
                horizontal = min(a[2], b[2])-max(a[0], b[0])
                vertical_gap = max(a[1], b[1])-min(a[3], b[3])
                if horizontal > 0 and vertical_gap <= max(0, max_gap):
                    groups[i].extend(groups.pop(j))
                    changed = True
                    break
            if changed: break
    result = []
    for group in groups:
        group.sort(key=lambda b: (b.y, b.x))
        left, top, right, bottom = bounds(group)
        result.append(Block(' '.join(b.text for b in group), left, top,
                            right-left, bottom-top, min(b.line_height for b in group),
                            ' '.join(b.translated for b in group).strip(),
                            ' '.join(b.narrated for b in group).strip()))
    return sorted(result, key=lambda b: (b.y, b.x))


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
    # Keep rows separate in the OCR cache. The UI's adjustable pixel distance
    # is applied after lookup, before length filtering and translation.
    return sorted(rows, key=lambda b: (b.y, b.x))


class WindowsRecognizer:
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


class Recognizer:
    def __init__(self, provider='portable', **options):
        self.warning = ''
        if provider == 'gemini':
            from gemini_ocr import GeminiRecognizer
            self.engine = GeminiRecognizer(**options)
            self.backend = 'Gemini OCR · online: beeldgebied wordt naar Google gestuurd'
            return
        if provider == 'windows':
            try:
                self.engine = WindowsRecognizer()
                self.backend = 'Windows OCR'
                return
            except Exception:
                self.warning = 'Windows OCR niet beschikbaar; ingebouwde OCR wordt gebruikt.'
        from portable_ocr import PortableRecognizer
        self.engine = PortableRecognizer()
        self.backend = 'Ingebouwde OCR'

    async def read(self, image):
        return await self.engine.read(image)



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
