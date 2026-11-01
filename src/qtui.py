"""Qt desktop UI and a transparent, non-activating, click-through replacement layer."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import threading
import time
import asyncio
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QAbstractNativeEventFilter, QSettings
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QCheckBox, QTextEdit, QMessageBox, QRubberBand, QSpinBox, QComboBox, QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QScrollArea, QTabWidget, QPlainTextEdit)
from PIL import ImageGrab
from overlay import (DATA, RESOURCE, Translator, Recognizer, user32, native_handle,
                     find_game, client_box, active_game)


from translation import normalize_spacing
from speech import Speaker
from voiceui import VoiceDialog
from gemini_ocr import OCRError
from keyhelp import show_key_help
from childfriendly import ChildFriendlyRewriter, ChildFriendlyError, DEFAULT_INSTRUCTION
from languages import LANGUAGES, language_code, speech_language
from languagepacks import model_available, install_language


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
        self.text_visible = True
        self.progress_text = ''
        self.click_handler = None
        self.click_regions = []
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
        self.click_regions = []
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
            self.click_regions.append((QRect(x, y, w, draw_h), block.narrated or block.translated))
            painter.setPen(QColor('#f5e8c7'))
            painter.setFont(font)
            painter.drawText(QRect(x+5, y+3, w-10, draw_h-6), flags, block.translated)

        if self.progress_text:
            font = QFont('Segoe UI'); font.setPixelSize(13)
            status_width = min(460, physical_width-16, QFontMetrics(font).horizontalAdvance(self.progress_text)+20)
            if status_width > 40:
                status_rect = QRect(physical_width-status_width-8, 8, status_width, 32 if len(self.progress_text)<65 else 62)
                painter.setPen(QPen(QColor('#9c8451'), 1))
                painter.setBrush(QColor('#17212b'))
                painter.drawRoundedRect(status_rect, 5, 5)
                painter.setFont(font); painter.setPen(QColor('#f0d699'))
                painter.drawText(status_rect.adjusted(10, 4, -10, -4),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap), self.progress_text)

    def set_click_handler(self, handler):
        self.click_handler = handler
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, handler is None)
        bits = user32.GetWindowLongW(self.hwnd, -20)
        user32.SetWindowLongW(self.hwnd, -20, (bits & ~0x20) if handler else (bits | 0x20))

    def mousePressEvent(self, event):
        if self.click_handler and event.button() == Qt.MouseButton.LeftButton:
            point = event.position()*self.devicePixelRatioF()
            for rect, text in reversed(self.click_regions):
                if rect.contains(point.toPoint()):
                    self.click_handler(text)
                    event.accept()
                    return
        event.ignore()

    def show_progress(self, box, text, clear=False):
        if clear: self.blocks = []
        self.progress_text = text
        self.render_blocks(box, self.blocks)

    def render_blocks(self, box, blocks):
        self.blocks = blocks
        if not self.text_visible:
            self.click_regions = []
            self.hide()
            return
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
        logging.basicConfig(handlers=[RotatingFileHandler(DATA/'overlay.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8', delay=True)], level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setApplicationName('Middenaarde Overlay')
        self.events = queue.Queue()
        self.closing = threading.Event()
        self.enabled = threading.Event()
        self.epoch = 0
        self.region = None
        self.preferences = QSettings(str(DATA/'ui.ini'), QSettings.Format.IniFormat)
        self.ocr_provider = self.preferences.value('ocr_provider', 'portable', type=str)
        if self.ocr_provider not in ('portable', 'windows', 'gemini'): self.ocr_provider = 'portable'
        self.ocr_model = self.preferences.value('ocr_model', 'gemini-3.8-flash', type=str)
        self.ocr_interval = self.preferences.value('ocr_interval', 10, type=int)
        self.ocr_revision = 0
        self.child_friendly = self.preferences.value('child_friendly', False, type=bool)
        self.child_pause = self.preferences.value('child_pause_on_error', True, type=bool)
        self.child_instruction = self.preferences.value('child_instruction', DEFAULT_INSTRUCTION, type=str)
        self.min_chars = max(0, min(10000, self.preferences.value('minimum_characters', 30, type=int)))
        self.output_language = language_code(self.preferences.value('overlay_language', 'nl'))
        self.narration_choice = self.preferences.value('narration_language', 'same')
        if self.narration_choice not in LANGUAGES and self.narration_choice != 'same': self.narration_choice = 'same'
        self.downloading = False
        self.target = None
        self.last_game_box = None
        self.latest_text = ''
        self.spoken = set()
        self.voice_ok = True
        self.ready = False
        self.was_active = False
        self.root = QWidget(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.root.setWindowTitle('Middenaarde Overlay')
        self.root.setMinimumSize(600, 500)
        available_height = self.qt.primaryScreen().availableGeometry().height()
        self.root.resize(660, max(500, min(760, available_height - 90)))
        self.root.move(35, 70)
        self.root.setStyleSheet('''
            QWidget { background:#17212b; color:#e7e0cc; font:10pt "Segoe UI"; }
            QPushButton { background:#33414d; border:1px solid #59626a; border-radius:5px; padding:8px; }
            QPushButton:hover { background:#46535d; }
            QPushButton:disabled { color:#8a8f93; }
            QTextEdit { background:#101820; border:1px solid #3e4850; padding:7px; }
            QCheckBox { padding:4px 0; }
            QTabWidget::pane { border:0; }
            QTabBar::tab { background:#26333e; color:#c9c6bb; padding:10px 12px; border:0; }
            QTabBar::tab:selected { background:#3b4b58; color:#f0d699; }
            QTabBar::tab:hover { background:#33414d; }
            QPlainTextEdit { background:#101820; color:#e7e0cc; border:1px solid #3e4850; }
        ''')
        window_layout = QVBoxLayout(self.root)
        window_layout.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 18, 20, 18)
        window_layout.addLayout(layout)
        title = QLabel('MIDDEN-AARDE')
        title.setStyleSheet('font: bold 21pt "Segoe UI"; color:#f0d699;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Lokale vertaling · meertalige stemmen'))
        self.status = QLabel('Tekstherkenning laden…')
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
        self.tabs = QTabWidget()
        window_layout.addWidget(self.tabs, 1)
        pages = {}
        for name in ('Overzicht', 'Tekstherkenning', 'Talen & stem', 'Kindvriendelijk', 'Debuglogs'):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            page = QWidget(); section = QVBoxLayout(page)
            section.setContentsMargins(18, 14, 18, 14); section.setSpacing(10)
            scroll.setWidget(page); self.tabs.addTab(scroll, name); pages[name] = section
        overview = pages['Overzicht']
        layout = pages['Talen & stem']
        self.read_aloud = QCheckBox('Verhaaltekst automatisch voorlezen')
        self.read_aloud.setChecked(True)
        self.read_aloud.toggled.connect(self.voice_toggle)
        layout.addWidget(self.read_aloud)
        voice_button = QPushButton('Stem && providers…')
        voice_button.clicked.connect(self.voice_settings)
        layout.addWidget(voice_button)
        layout = pages['Tekstherkenning']
        ocr_row = QHBoxLayout()
        ocr_row.addWidget(QLabel('Tekstherkenning'))
        self.ocr_choice = QComboBox()
        self.ocr_choice.addItem('Ingebouwd · offline (aanbevolen)', 'portable')
        self.ocr_choice.addItem('Windows OCR · met automatische terugval', 'windows')
        self.ocr_choice.addItem('Google Gemini · online', 'gemini')
        self.ocr_choice.setCurrentIndex(self.ocr_choice.findData(self.ocr_provider))
        self.ocr_choice.currentIndexChanged.connect(self.change_ocr)
        ocr_row.addWidget(self.ocr_choice, 1)
        layout.addLayout(ocr_row)
        self.ocr_label = QLabel('OCR laden…')
        self.ocr_label.setWordWrap(True)
        layout.addWidget(self.ocr_label)
        ocr_settings = QPushButton('Gemini OCR-instellingen…')
        ocr_settings.clicked.connect(self.google_ocr_settings)
        layout.addWidget(ocr_settings)
        layout = pages['Talen & stem']
        language_row = QHBoxLayout()
        language_row.addWidget(QLabel('Taal overlaytekst'))
        self.overlay_language = QComboBox()
        for code, info in LANGUAGES.items(): self.overlay_language.addItem(info[0], code)
        self.overlay_language.setCurrentIndex(self.overlay_language.findData(self.output_language))
        language_row.addWidget(self.overlay_language, 1)
        layout.addLayout(language_row)
        narration_row = QHBoxLayout()
        narration_row.addWidget(QLabel('Voorleestaal'))
        self.narration_language = QComboBox()
        self.narration_language.addItem('Dezelfde taal als overlaytekst', 'same')
        for code, info in LANGUAGES.items(): self.narration_language.addItem(info[0], code)
        self.narration_language.setCurrentIndex(self.narration_language.findData(self.narration_choice))
        narration_row.addWidget(self.narration_language, 1)
        layout.addLayout(narration_row)
        self.language_info = QLabel()
        self.language_info.setWordWrap(True)
        layout.addWidget(self.language_info)
        self.download_button = QPushButton('Download ontbrekende taalpakketten')
        self.download_button.clicked.connect(self.download_languages)
        layout.addWidget(self.download_button)
        layout = overview
        self.show_text = QCheckBox('Overlaytekst tonen in het spel')
        self.show_text.setChecked(self.preferences.value('show_overlay_text', True, type=bool))
        self.show_text.setToolTip('Uit: de oorspronkelijke speltekst blijft zichtbaar. Vertalen en voorlezen blijven werken.')
        layout.addWidget(self.show_text)
        layout = pages['Kindvriendelijk']
        layout.addWidget(QLabel('Pas verhaaltekst aan voor jonge spelers.'))
        self.child_friendly_box = QCheckBox('Kindvriendelijk formuleren (Gemini)')
        self.child_friendly_box.setChecked(self.child_friendly)
        self.child_friendly_box.setToolTip('Optioneel: vertaald verhaal verzachten via Google. Gebruikt API-tegoed, ook bij lokale OCR.')
        self.child_friendly_box.toggled.connect(self.change_child_friendly)
        layout.addWidget(self.child_friendly_box)
        self.child_pause_box = QCheckBox('Pauzeren als kindvriendelijk maken mislukt')
        self.child_pause_box.setChecked(self.child_pause)
        self.child_pause_box.setToolTip('Uit: bij een fout wordt de gewone vertaling getoond en voorgelezen, zonder kindvriendelijke bewerking.')
        self.child_pause_box.toggled.connect(self.change_child_pause)
        layout.addWidget(self.child_pause_box)
        child_settings = QPushButton('Instructie kindvriendelijke tekst…')
        child_settings.clicked.connect(self.child_friendly_settings)
        layout.addWidget(child_settings)
        layout.addStretch()
        layout = pages['Tekstherkenning']
        length_row = QHBoxLayout()
        length_row.addWidget(QLabel('Vertaal vanaf'))
        self.minimum_length = QSpinBox()
        self.minimum_length.setRange(0, 10000)
        self.minimum_length.setSuffix(' tekens')
        self.minimum_length.setSpecialValueText('Alles vertalen')
        self.minimum_length.setValue(self.min_chars)
        self.minimum_length.setToolTip('Per herkend Engels tekstblok, inclusief spaties. 0 = alles vertalen.')
        self.minimum_length.valueChanged.connect(self.change_minimum_length)
        length_row.addWidget(self.minimum_length, 1)
        layout.addLayout(length_row)
        layout = pages['Talen & stem']
        self.click_to_speak = QCheckBox('Klik op vertaling om voor te lezen / stoppen')
        self.click_to_speak.setChecked(self.preferences.value('click_to_speak', True, type=bool))
        self.click_to_speak.toggled.connect(self.change_click_to_speak)
        layout.addWidget(self.click_to_speak)
        layout = pages['Tekstherkenning']
        areas = QHBoxLayout()
        pick = QPushButton('Kies tekstgebied'); pick.clicked.connect(self.select_region)
        areas.addWidget(pick)
        reset = QPushButton('Hele spelvenster'); reset.clicked.connect(self.reset_region)
        areas.addWidget(reset)
        layout.addLayout(areas)
        self.region_label = QLabel('Gebied: hele spelvenster')
        layout.addWidget(self.region_label)
        layout.addStretch()
        layout = overview
        layout.addWidget(QLabel('Laatste vertaling'))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(80)
        self.preview.setPlaceholderText('De tekst in de gekozen overlaytaal verschijnt hier ook.')
        layout.addWidget(self.preview, 1)
        repeat = QPushButton('Lees laatste tekst opnieuw'); repeat.clicked.connect(self.repeat)
        layout.addWidget(repeat)
        footer = QLabel('Speel in venstermodus (Alt+Enter).\nCtrl+Alt+P: pauze · Ctrl+Alt+S: stop stem')
        footer.setStyleSheet('color:#b5b7af;font-size:9pt;')
        layout.addWidget(footer)
        logs = pages['Debuglogs']
        hint = QLabel('Live stappen en tijden: spel vinden, focus, OCR, vertalen en tonen. Geen API-sleutels of verhaaltekst in deze diagnoseberichten.')
        hint.setWordWrap(True); logs.addWidget(hint)
        self.debug_view = QPlainTextEdit(); self.debug_view.setReadOnly(True)
        self.debug_view.setMaximumBlockCount(1000); logs.addWidget(self.debug_view, 1)
        copy_log = QPushButton('Kopieer zichtbare debuglogs')
        copy_log.clicked.connect(lambda: self.qt.clipboard().setText(self.debug_view.toPlainText()))
        logs.addWidget(copy_log)
        open_logs = QPushButton('Open logmap')
        open_logs.clicked.connect(lambda: os.startfile(str(DATA)))
        logs.addWidget(open_logs)
        self.overlay = Overlay()
        self.capture_ok = self.overlay.capture_ok
        user32.SetWindowDisplayAffinity(native_handle(self.root), 0x11)
        self.speaker = Speaker(self.events)
        self.overlay.text_visible = self.show_text.isChecked()
        self.change_click_to_speak(self.click_to_speak.isChecked())
        self.show_text.toggled.connect(self.change_overlay_visibility)
        self.overlay_language.currentIndexChanged.connect(self.change_languages)
        self.narration_language.currentIndexChanged.connect(self.change_languages)
        self.refresh_language_controls()
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

    def trace(self, message):
        now = time.monotonic()
        if message == getattr(self, '_last_trace', None) and now - getattr(self, '_trace_time', 0) < 10:
            return
        self._last_trace, self._trace_time = message, now
        logging.info(message)
        self.events.put(('diagnostic', time.strftime('%H:%M:%S') + '  ' + message))

    def toggle(self):
        if not self.ready: return
        if not self.enabled.is_set() and (self.downloading or self.missing_languages()):
            self.status.setText('Download eerst de ontbrekende taalpakketten.')
            return
        if not self.capture_ok:
            QMessageBox.critical(self.root, 'Overlay niet beschikbaar',
                'Windows kan de overlay niet uitsluiten van schermopnames. Gebruik Windows 10 versie 2004 of nieuwer.')
            return
        self.epoch += 1
        self.trace('Overlay pauzeren' if self.enabled.is_set() else 'Overlay gestart')
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

    def show_progress(self, text, clear=False):
        if self.enabled.is_set() and self.target and active_game(self.target):
            box = client_box(self.target)
            if box: self.overlay.show_progress(box, text, clear)

    def voice_settings(self):
        self.speaker.stop()
        dialog = VoiceDialog(self.root, self.speaker, self.current_speech_language())
        self.voice_dialog = dialog
        if dialog.exec():
            self.voice_ok = True
            self.spoken.clear()
            self.ocr_revision += 1
            self.refresh_language_controls()
        self.voice_dialog = None

    def voice_toggle(self, enabled):
        if not enabled: self.speaker.stop()

    def repeat(self):
        if self.latest_text: self.speaker.say(self.latest_text, language=self.current_speech_language())

    def invalidate_page(self):
        self.epoch += 1
        self.overlay.blocks = []
        self.overlay.click_regions = []
        self.overlay.hide()
        self.speaker.stop()
        self.latest_text = ''
        self.preview.clear()

    def change_child_pause(self, enabled):
        self.child_pause = enabled
        self.preferences.setValue('child_pause_on_error', enabled)
        self.preferences.sync()
        self.invalidate_page()

    def change_child_friendly(self, enabled):
        self.child_friendly = enabled
        self.preferences.setValue('child_friendly', enabled)
        self.preferences.sync()
        self.invalidate_page()

    def child_friendly_settings(self):
        dialog = QDialog(self.root); dialog.setWindowTitle('Kindvriendelijke tekst – instructie')
        dialog.resize(630, 470)
        layout = QVBoxLayout(dialog)
        label = QLabel('Deze optionele bewerking gaat via Gemini en gebruikt API-tegoed.\n'
                       'De aangepaste tekst wordt getoond én voorgelezen.\n'
                       'Spelbeelden en de oorspronkelijke Engelse tekst worden niet aangepast.\n'
                       'Automatische bewerking kan details missen; controleer spelregels bij twijfel.')
        label.setWordWrap(True); layout.addWidget(label)
        instruction = QTextEdit(self.child_instruction); layout.addWidget(instruction)
        reset = QPushButton('Standaardinstructie herstellen')
        reset.clicked.connect(lambda: instruction.setPlainText(DEFAULT_INSTRUCTION)); layout.addWidget(reset)
        help_button = QPushButton('Google-sleutel toevoegen: uitleg')
        help_button.clicked.connect(lambda: show_key_help(dialog, 'google')); layout.addWidget(help_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Opslaan')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Annuleren')
        layout.addWidget(buttons); buttons.rejected.connect(dialog.reject)
        def save():
            text = instruction.toPlainText().strip()
            if not text:
                QMessageBox.information(dialog, 'Instructie ontbreekt', 'Vul een instructie in of herstel de standaardinstructie.'); return
            self.child_instruction = text
            self.preferences.setValue('child_instruction', text); self.preferences.sync()
            self.invalidate_page(); dialog.accept()
        buttons.accepted.connect(save); dialog.exec()

    def google_ocr_settings(self):
        dialog = QDialog(self.root); dialog.setWindowTitle('Gemini OCR-instellingen')
        layout = QFormLayout(dialog)
        notice = QLabel('Alleen bij Google Gemini: het gekozen spelbeeldgebied gaat naar Google.\nDit gebruikt API-tegoed. Deze sleutel wordt ook voor Gemini-spraak gebruikt.')
        notice.setWordWrap(True); layout.addRow(notice)
        key = QLineEdit(self.speaker.keys.get('google', '')); key.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow('Google API-sleutel', key)
        help_button = QPushButton('Waar maak ik de sleutel en waar plak ik hem?')
        help_button.clicked.connect(lambda: show_key_help(dialog, 'google'))
        layout.addRow(help_button)
        model = QComboBox(); model.setEditable(True); model.addItems(['gemini-3.8-flash', 'gemini-2.5-flash'])
        model.setCurrentText(self.ocr_model); layout.addRow('Beeldmodel (geen TTS-model)', model)
        interval = QSpinBox(); interval.setRange(5, 120); interval.setValue(self.ocr_interval); interval.setSuffix(' seconden')
        layout.addRow('Minimale tijd tussen aanvragen', interval)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addRow(buttons); buttons.rejected.connect(dialog.reject)
        def save():
            keys = dict(self.speaker.keys); keys['google'] = key.text().strip()
            try: self.speaker.save(self.speaker.settings, keys)
            except OSError:
                QMessageBox.warning(dialog, 'Opslaan mislukt', 'Windows kon de sleutel niet versleuteld opslaan.'); return
            self.ocr_model, self.ocr_interval = model.currentText().strip(), interval.value()
            self.preferences.setValue('ocr_model', self.ocr_model)
            self.preferences.setValue('ocr_interval', self.ocr_interval)
            self.preferences.sync(); self.ocr_revision += 1; self.epoch += 1
            self.overlay.hide(); dialog.accept()
        buttons.accepted.connect(save); dialog.exec()

    def create_recognizer(self, provider):
        options = {}
        if provider == 'gemini':
            revision = self.ocr_revision
            options = dict(key=self.speaker.keys.get('google', ''), model=self.ocr_model, interval=self.ocr_interval,
                on_wait=lambda: self.events.put(('ocr_wait', self.epoch)),
                cancelled=lambda: self.closing.is_set() or not self.enabled.is_set() or self.ocr_provider != provider or self.ocr_revision != revision)
        return Recognizer(provider, **options)

    def change_ocr(self):
        self.ocr_provider = self.ocr_choice.currentData()
        self.preferences.setValue('ocr_provider', self.ocr_provider)
        self.preferences.sync()
        self.epoch += 1
        self.overlay.hide()
        self.speaker.stop()
        self.latest_text = ''
        self.preview.clear()
        self.ocr_label.setText('Google Gemini: beeldgebied gaat online naar Google; gebruikt API-tegoed.' if self.ocr_provider == 'gemini' else 'OCR wisselen…')

    def click_speech(self, text):
        if self.speaker.busy and self.speaker.current_text == text:
            self.speaker.stop()
            self.show_progress('')
        else:
            self.speaker.say(text, language=self.current_speech_language())

    def change_click_to_speak(self, enabled):
        self.preferences.setValue('click_to_speak', enabled)
        self.preferences.sync()
        self.click_to_speak.setEnabled(self.show_text.isChecked())
        self.overlay.set_click_handler(self.click_speech if enabled and self.show_text.isChecked() else None)

    def current_speech_language(self):
        return speech_language(self.output_language, self.narration_choice)

    def missing_languages(self):
        return [code for code in dict.fromkeys((self.output_language, self.current_speech_language()))
                if not model_available(code, RESOURCE, DATA)]

    def refresh_language_controls(self):
        missing = self.missing_languages()
        self.download_button.setVisible(bool(missing) or self.downloading)
        self.download_button.setEnabled(not self.downloading)
        self.overlay_language.setEnabled(not self.downloading)
        self.narration_language.setEnabled(not self.downloading)
        self.start_button.setEnabled(self.ready and not missing and not self.downloading)
        if missing:
            self.language_info.setText('Eenmalige download nodig: ' + ', '.join(LANGUAGES[c][0] for c in missing)
                + '. Circa 65–150 MB per taal. Daarna werkt vertaling offline. Het spel blijft in het Engels.')
        else:
            self.language_info.setText('Vertaling werkt lokaal. Het spel blijft in het Engels.')
        if self.current_speech_language() != 'nl' and self.speaker.settings['provider'] == 'piper':
            self.language_info.setText(self.language_info.text() + '\nVoor deze voorleestaal: kies een online stem bij Stem & providers. Piper spreekt alleen Nederlands.')

    def change_languages(self, _index):
        self.output_language = self.overlay_language.currentData()
        self.narration_choice = self.narration_language.currentData()
        self.preferences.setValue('overlay_language', self.output_language)
        self.preferences.setValue('narration_language', self.narration_choice)
        self.preferences.sync()
        self.epoch += 1
        self.overlay.blocks = []
        self.overlay.click_regions = []
        self.overlay.hide()
        self.speaker.stop()
        self.latest_text = ''
        self.preview.clear()
        self.spoken.clear()
        if self.missing_languages():
            self.enabled.clear()
            self.start_button.setText('Start overlay')
            self.status.setText('Download de gekozen taalpakketten en start daarna de overlay.')
        self.refresh_language_controls()

    def download_languages(self):
        missing = self.missing_languages()
        if self.downloading or not missing: return
        self.downloading = True
        self.status.setText('Taalpakketten downloaden… Dit kan enkele minuten duren.')
        self.refresh_language_controls()
        def download():
            try:
                for code in missing:
                    install_language(code, RESOURCE, DATA)
                self.events.put(('languages_ready', None))
            except Exception:
                logging.exception('Language package download')
                self.events.put(('languages_ready', 'Download mislukt. Controleer internet en vrije schijfruimte en probeer opnieuw.'))
        threading.Thread(target=download, daemon=True).start()

    def change_overlay_visibility(self, visible):
        self.preferences.setValue('show_overlay_text', visible)
        self.preferences.sync()
        self.overlay.text_visible = visible
        self.change_click_to_speak(self.click_to_speak.isChecked())
        if not visible:
            self.overlay.click_regions = []
            self.overlay.hide()
        elif self.enabled.is_set() and self.target and active_game(self.target):
            box = client_box(self.target)
            if box and box == self.last_game_box:
                self.overlay.render_blocks(box, self.overlay.blocks)

    def change_minimum_length(self, value):
        self.min_chars = value
        self.preferences.setValue('minimum_characters', value)
        self.preferences.sync()
        self.epoch += 1
        self.overlay.blocks = []
        self.overlay.click_regions = []
        self.overlay.hide()
        self.speaker.stop()
        self.latest_text = ''
        self.preview.clear()

    def reset_region(self):
        self.region = None
        self.epoch += 1
        self.overlay.blocks = []
        self.overlay.click_regions = []
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
        self.overlay.blocks = []
        self.overlay.click_regions = []
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
            selected_ocr = self.ocr_provider
            selected_revision = self.ocr_revision
            translators, recognizer = {}, self.create_recognizer(selected_ocr)
            self.events.put(('ocr_ready', (selected_ocr, recognizer.warning or recognizer.backend)))
            self.events.put(('ready', None))
        except Exception as exc:
            logging.exception('Initialization')
            self.events.put(('fatal', str(exc)))
            return
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        previous, rendered, previous_epoch = None, None, -1
        rewriter, rewrite_settings = None, None
        while not self.closing.is_set():
            if self.closing.wait(0.65): break
            if self.ocr_provider != selected_ocr or self.ocr_revision != selected_revision:
                selected_ocr = self.ocr_provider
                selected_revision = self.ocr_revision
                try:
                    replacement = self.create_recognizer(selected_ocr)
                    recognizer = replacement
                    self.events.put(('ocr_ready', (selected_ocr, recognizer.warning or recognizer.backend)))
                    previous = rendered = None
                except Exception:
                    logging.exception('OCR switch')
                    self.events.put(('ocr_ready', (selected_ocr, 'OCR wisselen mislukt; vorige herkenning blijft actief.')))
            if not self.enabled.is_set():
                self.trace('Gepauzeerd: klik Start overlay')
                continue
            try:
                hwnd = find_game()
                box = client_box(hwnd)
                self.target = hwnd
                if not box:
                    self.trace('Geen zichtbaar spelvenster gevonden (of geminimaliseerd)')
                    self.events.put(('status', 'Wachten op Journeys in Middle-earth…'))
                    previous = rendered = None
                    continue
                if not active_game(hwnd):
                    self.trace('Spel gevonden; wacht op focus. Klik in het spel.')
                    self.events.put(('status', 'Spel gevonden · klik in het spel om te vertalen'))
                    previous = rendered = None
                    continue
                epoch, region, minimum = self.epoch, self.region, self.min_chars
                child_enabled, child_instruction = self.child_friendly, self.child_instruction
                child_pause = self.child_pause
                output_language, narration_language = self.output_language, self.current_speech_language()
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
                self.trace(f'OCR starten: {selected_ocr}, gebied {grab_box[2]-grab_box[0]}x{grab_box[3]-grab_box[1]}')
                self.events.put(('ocr_wait', epoch))
                started = time.monotonic()
                image = ImageGrab.grab(bbox=grab_box, all_screens=True)
                blocks = loop.run_until_complete(recognizer.read(image))
                if selected_ocr == 'gemini': self.events.put(('ocr_wait_done', epoch))
                count = len(blocks)
                blocks = [b for b in blocks if len(b.text.strip()) >= minimum]
                self.trace(f'OCR klaar in {time.monotonic()-started:.1f}s: {count} blokken, {len(blocks)} boven minimum {minimum}')
                for block in blocks:
                    block.x += offset[0]; block.y += offset[1]
                signature = tuple((b.text, round(b.x/8), round(b.y/8), round(b.width/8)) for b in blocks)
                if epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                if signature != previous:
                    self.events.put(('clear', epoch))
                    previous = signature
                    rendered = None
                if signature == rendered: continue
                if not blocks:
                    self.events.put(('status', 'Actief · geen tekst gevonden die aan de ingestelde minimumlengte voldoet'))
                    rendered = signature
                    continue
                self.trace(f'Vertalen starten: {len(blocks)} blokken naar {output_language}/{narration_language}')
                self.events.put(('status', 'Tekst in de gekozen taal maken…'))
                required = {output_language, narration_language}
                # Keep at most the two active models in memory.
                for code in list(translators):
                    if code not in required: del translators[code]
                for code in required:
                    if code not in translators: translators[code] = Translator(code)
                for block in sorted(blocks, key=lambda b: -len(b.text)):
                    if self.closing.is_set() or epoch != self.epoch: break
                    block.translated = translators[output_language].translate(block.text)
                    block.narrated = (block.translated if narration_language == output_language
                                      else translators[narration_language].translate(block.text))
                if self.closing.is_set() or epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                if child_enabled:
                    self.events.put(('status', 'Kindvriendelijke tekst maken…'))
                    settings = (self.speaker.keys.get('google', ''), self.ocr_model, child_instruction)
                    if settings != rewrite_settings:
                        rewriter = ChildFriendlyRewriter(*settings); rewrite_settings = settings
                    try:
                        rewritten = rewriter.rewrite([text for block in blocks for text in (block.translated, block.narrated)])
                    except ChildFriendlyError:
                        if child_pause: raise
                        self.events.put(('status', 'Kindvriendelijke bewerking mislukt; gewone vertaling gebruiken.'))
                    else:
                        for i, block in enumerate(blocks):
                            block.translated, block.narrated = rewritten[2*i:2*i+2]
                    if self.closing.is_set() or epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                self.trace('Vertaling klaar; controleren of verhaaltekst nog zichtbaar is')
                fresh = ImageGrab.grab(bbox=grab_box, all_screens=True)
                if selected_ocr == 'gemini':
                    # Validate locally instead of paying for a second image request.
                    from capturecheck import text_regions_unchanged
                    if not text_regions_unchanged(image, fresh, blocks, offset):
                        self.trace('Tekstgebied veranderd tijdens vertalen; opnieuw scannen')
                        previous = rendered = None
                        continue
                    fresh_blocks = blocks
                else:
                    fresh_blocks = loop.run_until_complete(recognizer.read(fresh))
                fresh_blocks = [b for b in fresh_blocks if len(b.text.strip()) >= minimum]
                if sorted(b.text for b in fresh_blocks) != sorted(b.text for b in blocks):
                    self.trace('Herkenning veranderde tijdens vertalen; opnieuw scannen')
                    previous = rendered = None
                    continue
                for block in blocks:
                    block.translated = normalize_spacing(block.translated)
                    block.narrated = normalize_spacing(block.narrated)
                self.events.put(('render', (epoch, hwnd, box, blocks, signature)))
                rendered = signature
            except OCRError as exc:
                self.trace('Verwerking gepauzeerd: '+str(exc))
                self.enabled.clear()
                self.events.put(('ocr_failed', str(exc)))
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
            if kind == 'diagnostic':
                self.debug_view.appendPlainText(value)
            elif kind == 'ocr_ready':
                provider, message = value
                if provider == self.ocr_provider: self.ocr_label.setText(message)
            elif kind == 'ocr_wait' and value == self.epoch:
                self.show_progress('Tekst herkennen…')
            elif kind == 'ocr_wait_done' and value == self.epoch:
                if self.overlay.progress_text == 'Tekst herkennen…': self.show_progress('')
            elif kind == 'ocr_failed':
                self.start_button.setText('Start overlay')
                self.status.setText(value+' · Verwerking gepauzeerd; klik Start overlay om opnieuw te proberen.')
                self.speaker.stop()
                self.overlay.hide()
            elif kind == 'ready':
                self.ready = True
                self.status.setText('Gereed · start de overlay en klik in het spel')
                self.refresh_language_controls()
            elif kind == 'languages_ready':
                self.downloading = False
                self.refresh_language_controls()
                self.status.setText(value or 'Taalpakketten gereed · start de overlay en klik in het spel')
            elif kind == 'fatal':
                self.status.setText('Starten mislukt: '+value)
            elif kind == 'speech_error':
                self.voice_ok = True
                if getattr(self, "voice_dialog", None): self.voice_dialog.status.setText(value)
                self.read_aloud.setChecked(False)
                self.status.setText('Voorlezen niet beschikbaar: '+value[:160])
                self.show_progress('Voorlezen mislukt: '+value[:140])
            elif kind == 'speech_status':
                generation, message = value
                if generation == self.speaker.generation:
                    if message or self.overlay.progress_text == 'Spraak laden…': self.show_progress(message)
            elif kind == 'status' and self.enabled.is_set():
                self.status.setText(value)
                if 'mislukt' in value.lower(): self.show_progress(value)
                elif 'maken' in value.lower(): self.show_progress('Vertalen…')
                else: self.show_progress('')
            elif kind == 'clear' and value == self.epoch:
                self.overlay.hide()
                self.speaker.stop()
                self.show_progress('Tekst herkennen…', clear=True)
            elif kind == 'render':
                epoch, hwnd, box, blocks, signature = value
                if epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                self.trace(f'Overlay getoond: {len(blocks)} vertaalde blokken')
                self.overlay.progress_text = ''
                self.overlay.render_blocks(box, blocks)
                self.last_game_box = box
                narrative = [b.narrated for b in blocks if len(b.text) >= 65]
                self.latest_text = '\n\n'.join(narrative or [b.narrated for b in blocks])
                self.preview.setPlainText('\n\n'.join(b.translated for b in blocks))
                self.status.setText(f'Actief · {len(blocks)} tekstvlakken lokaal vertaald')
                spoken_text = ' '.join(narrative)
                digest = hashlib.sha256((self.current_speech_language() + ':' + spoken_text).encode()).hexdigest()
                if self.read_aloud.isChecked() and self.voice_ok and spoken_text and digest not in self.spoken:
                    self.spoken.add(digest)
                    self.speaker.say(spoken_text, language=self.current_speech_language())
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
                if not self.was_active and self.overlay.blocks:
                    self.overlay.render_blocks(box, self.overlay.blocks)
                    self.trace('Terug in het spel: bestaande overlay hersteld')
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
