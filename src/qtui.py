"""Qt desktop UI and a transparent, non-activating, click-through replacement layer."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import logging
import queue
import threading
import time
import asyncio
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QAbstractNativeEventFilter
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QCheckBox, QTextEdit, QMessageBox, QRubberBand)
from PIL import ImageGrab
from overlay import (DATA, Translator, Recognizer, user32, native_handle,
                     find_game, client_box, active_game)


from speech import Speaker
from voiceui import VoiceDialog


def place_native(widget, box):
    """Win32 capture coordinates are physical pixels, including on mixed-DPI displays."""
    user32.SetWindowPos(native_handle(widget), C.c_void_p(-1), box[0], box[1],
                        box[2]-box[0], box[3]-box[1], 0x10 | 0x40)


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool |
                         Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # A Win32 color-key layer supports capture exclusion on Windows 10.
        # Qt's per-pixel alpha layer fails SetWindowDisplayAffinity with error 8.
        self.setStyleSheet('background:#ff00ff;')
        self.blocks = []
        self.hwnd = native_handle(self)
        bits = user32.GetWindowLongW(self.hwnd, -20)
        user32.SetWindowLongW(self.hwnd, -20, bits | 0x80000 | 0x20 | 0x08000000)
        user32.SetLayeredWindowAttributes.argtypes = [W.HWND, W.DWORD, C.c_ubyte, W.DWORD]
        if not user32.SetLayeredWindowAttributes(self.hwnd, 0xff00ff, 255, 1):
            raise OSError(C.get_last_error(), 'Could not initialize transparent overlay')
        self.capture_ok = bool(user32.SetWindowDisplayAffinity(self.hwnd, 0x11))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#ff00ff'))
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        ratio = self.devicePixelRatioF()
        painter.scale(1/ratio, 1/ratio)
        physical_width, physical_height = int(self.width()*ratio), int(self.height()*ratio)
        for block in self.blocks:
            if not block.translated: continue
            x, y = max(0, int(block.x)-5), max(0, int(block.y)-3)
            w = min(physical_width-x, max(45, int(block.width)+10))
            h = min(physical_height-y, max(20, int(block.height)+8))
            if w < 15 or h < 10: continue
            rect = QRect(x+5, y+3, w-10, h-6)
            font = QFont('Segoe UI')
            initial = max(11, min(32, int(block.line_height*0.95)))
            flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
            for size in range(initial, 8, -1):
                font.setPixelSize(size)
                metrics = QFontMetrics(font)
                text_rect = metrics.boundingRect(rect, flags, block.translated)
                if text_rect.height() <= rect.height(): break
            # If needed, expand down slightly rather than silently omit translated words.
            draw_h = min(physical_height-y, max(h, text_rect.height()+8))
            painter.setPen(QPen(QColor('#9c8451'), 1))
            painter.setBrush(QColor('#211e18'))
            painter.drawRoundedRect(QRect(x, y, w, draw_h), 3, 3)
            painter.setPen(QColor('#f5e8c7'))
            painter.setFont(font)
            painter.drawText(QRect(x+5, y+3, w-10, draw_h-6), flags, block.translated)

    def render_blocks(self, box, blocks):
        self.blocks = blocks
        self.setGeometry(box[0], box[1], box[2]-box[0], box[3]-box[1])
        self.show()
        place_native(self, box)
        self.update()


class RegionPicker(QWidget):
    def __init__(self, box, selected):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.selected = selected
        self.setGeometry(box[0], box[1], box[2]-box[0], box[3]-box[1])
        self.setWindowOpacity(0.55)
        self.setStyleSheet('background:#182838;color:white;')
        self.setCursor(Qt.CursorShape.CrossCursor)
        label = QLabel('Sleep over het Engelse tekstgebied · Esc: annuleren', self)
        label.move(20, 20)
        label.setStyleSheet('font: bold 17px "Segoe UI";')
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.start = QPoint()

    def mousePressEvent(self, event):
        self.start = event.position().toPoint()
        self.band.setGeometry(QRect(self.start, self.start))
        self.band.show()

    def mouseMoveEvent(self, event):
        self.band.setGeometry(QRect(self.start, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        rect = QRect(self.start, event.position().toPoint()).normalized().intersected(self.rect())
        if rect.width() >= 60 and rect.height() >= 25:
            self.selected((rect.x()/self.width(), rect.y()/self.height(),
                           (rect.x()+rect.width())/self.width(), (rect.y()+rect.height())/self.height()))
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape: self.close()


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if event_type in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = W.MSG.from_address(int(message))
            if msg.message == 0x0312:
                if msg.wParam == 1: self.owner.toggle()
                elif msg.wParam == 2: self.owner.speaker.stop()
                return True, 0
        return False, 0


class App:
    def __init__(self):
        DATA.mkdir(exist_ok=True)
        logging.basicConfig(filename=DATA/'overlay.log', level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setApplicationName('Middenaarde Overlay')
        self.events = queue.Queue()
        self.closing = threading.Event()
        self.enabled = threading.Event()
        self.epoch = 0
        self.region = None
        self.target = None
        self.last_game_box = None
        self.latest_text = ''
        self.spoken = set()
        self.voice_ok = True
        self.ready = False
        self.was_active = False
        self.root = QWidget(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.root.setWindowTitle('Middenaarde Overlay')
        self.root.setMinimumSize(450, 500)
        self.root.resize(490, 570)
        self.root.move(35, 70)
        self.root.setStyleSheet('''
            QWidget { background:#17212b; color:#e7e0cc; font:10pt "Segoe UI"; }
            QPushButton { background:#33414d; border:1px solid #59626a; border-radius:5px; padding:8px; }
            QPushButton:hover { background:#46535d; }
            QPushButton:disabled { color:#8a8f93; }
            QTextEdit { background:#101820; border:1px solid #3e4850; padding:7px; }
            QCheckBox { padding:4px 0; }
        ''')
        layout = QVBoxLayout(self.root)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(9)
        title = QLabel('MIDDEN-AARDE')
        title.setStyleSheet('font: bold 21pt "Segoe UI"; color:#f0d699;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Lokale vertaling · instelbare Nederlandse stemmen'))
        self.status = QLabel('Taalmodel laden…')
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(42)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('Start overlay')
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.toggle)
        buttons.addWidget(self.start_button, 1)
        stop = QPushButton('Stop stem')
        stop.clicked.connect(lambda: self.speaker.stop())
        buttons.addWidget(stop)
        layout.addLayout(buttons)
        self.read_aloud = QCheckBox('Verhaaltekst automatisch voorlezen')
        self.read_aloud.setChecked(True)
        self.read_aloud.toggled.connect(self.voice_toggle)
        layout.addWidget(self.read_aloud)
        voice_button = QPushButton('Stem & providers…')
        voice_button.clicked.connect(self.voice_settings)
        layout.addWidget(voice_button)
        areas = QHBoxLayout()
        pick = QPushButton('Kies tekstgebied'); pick.clicked.connect(self.select_region)
        areas.addWidget(pick)
        reset = QPushButton('Hele spelvenster'); reset.clicked.connect(self.reset_region)
        areas.addWidget(reset)
        layout.addLayout(areas)
        self.region_label = QLabel('Gebied: hele spelvenster')
        layout.addWidget(self.region_label)
        layout.addWidget(QLabel('Laatste vertaling'))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('De Nederlandse verhaaltekst verschijnt hier ook.')
        layout.addWidget(self.preview, 1)
        repeat = QPushButton('Lees laatste tekst opnieuw'); repeat.clicked.connect(self.repeat)
        layout.addWidget(repeat)
        footer = QLabel('Speel in venstermodus (Alt+Enter).\nCtrl+Alt+P: pauze · Ctrl+Alt+S: stop stem')
        footer.setStyleSheet('color:#b5b7af;font-size:9pt;')
        layout.addWidget(footer)
        self.overlay = Overlay()
        self.capture_ok = self.overlay.capture_ok
        user32.SetWindowDisplayAffinity(native_handle(self.root), 0x11)
        self.speaker = Speaker(self.events)
        self.hotkeys = []
        self.hotkey_filter = Hotkeys(self)
        self.qt.installNativeEventFilter(self.hotkey_filter)
        user32.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
        for code, key in [(1, ord('P')), (2, ord('S'))]:
            if user32.RegisterHotKey(native_handle(self.root), code, 0x4003, key):
                self.hotkeys.append(code)
        self.root.closeEvent = self.close_event
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        threading.Thread(target=self.worker, daemon=True).start()

    def toggle(self):
        if not self.ready: return
        if not self.capture_ok:
            QMessageBox.critical(self.root, 'Overlay niet beschikbaar',
                'Windows kan de overlay niet uitsluiten van schermopnames. Gebruik Windows 10 versie 2004 of nieuwer.')
            return
        self.epoch += 1
        if self.enabled.is_set():
            self.enabled.clear()
            self.start_button.setText('Start overlay')
            self.status.setText('Gepauzeerd')
            self.overlay.hide()
            self.speaker.stop()
        else:
            self.enabled.set()
            self.start_button.setText('Pauzeer overlay')
            self.status.setText('Actief · klik nu in het spel')

    def voice_settings(self):
        self.speaker.stop()
        dialog = VoiceDialog(self.root, self.speaker)
        self.voice_dialog = dialog
        if dialog.exec():
            self.voice_ok = True
            self.spoken.clear()
        self.voice_dialog = None

    def voice_toggle(self, enabled):
        if not enabled: self.speaker.stop()

    def repeat(self):
        if self.latest_text: self.speaker.say(self.latest_text)

    def reset_region(self):
        self.region = None
        self.epoch += 1
        self.overlay.hide()
        self.speaker.stop()
        self.region_label.setText('Gebied: hele spelvenster')

    def select_region(self):
        box = client_box(find_game())
        if not box:
            QMessageBox.information(self.root, 'Spel niet gevonden',
                                    'Open Journeys in Middle-earth in venstermodus.')
            return
        self.epoch += 1
        self.overlay.hide()
        self.speaker.stop()
        def selected(region):
            self.region = region
            self.epoch += 1
            self.region_label.setText('Gebied: gekozen tekstvak (schaalt mee)')
        self.picker = RegionPicker(box, selected)
        self.picker.show()
        place_native(self.picker, box)
        self.picker.activateWindow()

    def worker(self):
        try:
            translator, recognizer = Translator(), Recognizer()
            self.events.put(('ready', None))
        except Exception as exc:
            logging.exception('Initialization')
            self.events.put(('fatal', str(exc)))
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        previous, rendered, previous_epoch = None, None, -1
        while not self.closing.is_set():
            if self.closing.wait(0.65): break
            if not self.enabled.is_set(): continue
            try:
                hwnd = find_game()
                box = client_box(hwnd)
                self.target = hwnd
                if not box:
                    self.events.put(('status', 'Wachten op Journeys in Middle-earth…'))
                    previous = rendered = None
                    continue
                if not active_game(hwnd):
                    previous = rendered = None
                    continue
                epoch, region = self.epoch, self.region
                if epoch != previous_epoch:
                    previous = rendered = None
                    previous_epoch = epoch
                x, y, right, bottom = box
                width, height = right-x, bottom-y
                offset = (0, 0)
                grab_box = box
                if region:
                    a, b, c, d = region
                    offset = (int(width*a), int(height*b))
                    grab_box = (x+offset[0], y+offset[1], x+int(width*c), y+int(height*d))
                image = ImageGrab.grab(bbox=grab_box, all_screens=True)
                blocks = loop.run_until_complete(recognizer.read(image))
                for block in blocks:
                    block.x += offset[0]; block.y += offset[1]
                signature = tuple((b.text, round(b.x/8), round(b.y/8), round(b.width/8)) for b in blocks)
                if epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                if signature != previous:
                    self.events.put(('clear', epoch))
                    previous = signature
                    rendered = None
                    continue
                if signature == rendered: continue
                if not blocks:
                    self.events.put(('status', 'Actief · geen leesbare Engelse tekst gevonden'))
                    rendered = signature
                    continue
                self.events.put(('status', 'Nederlandse tekst maken…'))
                for block in sorted(blocks, key=lambda b: -len(b.text)):
                    if self.closing.is_set() or epoch != self.epoch: break
                    block.translated = translator.translate(block.text)
                if self.closing.is_set() or epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                fresh = ImageGrab.grab(bbox=grab_box, all_screens=True)
                fresh_blocks = loop.run_until_complete(recognizer.read(fresh))
                if [b.text for b in fresh_blocks] != [b.text for b in blocks]:
                    previous = rendered = None
                    continue
                self.events.put(('render', (epoch, hwnd, box, blocks, signature)))
                rendered = signature
            except Exception as exc:
                logging.exception('Capture / translation')
                self.events.put(('clear', self.epoch))
                self.events.put(('status', 'Herkenning mislukt: '+str(exc)[:160]))
                self.closing.wait(2)
        loop.close()

    def poll(self):
        if self.closing.is_set(): return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'ready':
                self.ready = True
                self.status.setText('Gereed · start de overlay en klik in het spel')
                self.start_button.setEnabled(True)
            elif kind == 'fatal':
                self.status.setText('Starten mislukt: '+value)
            elif kind == 'speech_error':
                self.voice_ok = True
                if getattr(self, "voice_dialog", None): self.voice_dialog.status.setText(value)
                self.read_aloud.setChecked(False)
                self.status.setText('Voorlezen niet beschikbaar: '+value[:160])
            elif kind == 'status' and self.enabled.is_set():
                self.status.setText(value)
            elif kind == 'clear' and value == self.epoch:
                self.overlay.hide()
                self.speaker.stop()
            elif kind == 'render':
                epoch, hwnd, box, blocks, signature = value
                if epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                self.overlay.render_blocks(box, blocks)
                self.last_game_box = box
                narrative = [b.translated for b in blocks if len(b.text) >= 65]
                self.latest_text = '\n\n'.join(narrative or [b.translated for b in blocks])
                self.preview.setPlainText(self.latest_text)
                self.status.setText(f'Actief · {len(blocks)} tekstvlakken lokaal vertaald')
                spoken_text = ' '.join(narrative)
                digest = hashlib.sha256(spoken_text.encode()).hexdigest()
                if self.read_aloud.isChecked() and self.voice_ok and spoken_text and digest not in self.spoken:
                    self.spoken.add(digest)
                    self.speaker.say(spoken_text)
        if self.target:
            box = client_box(self.target)
            if not active_game(self.target) or not box or not self.enabled.is_set():
                self.overlay.hide()
                if self.was_active: self.speaker.stop()
                self.was_active = False
            elif self.last_game_box and box != self.last_game_box:
                self.epoch += 1
                self.last_game_box = None
                self.overlay.hide()
                self.speaker.stop()
                self.was_active = True
            else:
                self.was_active = True

    def close_event(self, event):
        self.enabled.clear(); self.epoch += 1; self.closing.set()
        self.speaker.closed = True
        self.speaker.stop()
        for code in self.hotkeys:
            user32.UnregisterHotKey(native_handle(self.root), code)
        self.overlay.close()
        self.qt.quit()
        event.accept()

    def run(self):
        self.root.show()
        self.qt.exec()
