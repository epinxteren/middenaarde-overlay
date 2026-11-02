"""Qt desktop UI and a transparent, non-activating, click-through replacement layer."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import copy
import logging
from logging.handlers import RotatingFileHandler
import os
import queue
import threading
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QAbstractNativeEventFilter, QSettings
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QPolygon
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QCheckBox, QTextEdit, QMessageBox, QRubberBand, QSpinBox, QComboBox, QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QScrollArea, QTabWidget, QPlainTextEdit)
from PIL import ImageGrab
from overlay import (DATA, RESOURCE, Translator, Recognizer, user32, native_handle,
                     find_game, client_box, active_game, merge_blocks)


from translation import normalize_spacing
from speech import Speaker
from voiceui import VoiceDialog
from gemini_ocr import OCRError, OCRBudgetExceeded, RequestBudget
from keyhelp import show_key_help
from childfriendly import ChildFriendlyRewriter, ChildFriendlyError, DEFAULT_INSTRUCTION
from languages import LANGUAGES, language_code, speech_language
from languagepacks import model_available, install_language


def place_native(widget, box):
    """Win32 capture coordinates are physical pixels, including on mixed-DPI displays."""
    user32.SetWindowPos(native_handle(widget), C.c_void_p(-1), box[0], box[1],
                        box[2]-box[0], box[3]-box[1], 0x10 | 0x40)


class Overlay(QWidget):
    def nativeEvent(self, event_type, message):
        # Consume clicks without taking focus from JiME (MA_NOACTIVATE).
        msg = W.MSG.from_address(int(message))
        if msg.message == 0x0021:  # WM_MOUSEACTIVATE
            return True, 3
        return super().nativeEvent(event_type, message)

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
        self.original_english = False
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
        source_rects = [QRect(int(b.x), int(b.y), int(b.width), int(b.height)) for b in self.blocks]
        for block in self.blocks:
            if not block.translated: continue
            if self.original_english:
                # Put the control outside all source text, including neighbours.
                icon = self.play_icon_rect(block, source_rects, physical_width, physical_height)
                if icon is not None:
                    painter.setPen(QPen(QColor('#9c8451'), 1))
                    painter.setBrush(QColor('#211e18'))
                    painter.drawRoundedRect(icon, 4, 4)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor('#f5e8c7'))
                    x, y = icon.x(), icon.y()
                    painter.drawPolygon(QPolygon([QPoint(x+8, y+5), QPoint(x+8, y+17), QPoint(x+17, y+11)]))
                    self.click_regions.append((icon, block.narrated or block.translated))
                continue
            x, y = max(0, int(block.x)), max(0, int(block.y))
            w = min(physical_width-x, int(block.width))
            h = min(physical_height-y, int(block.height))
            if w < 15 or h < 10: continue
            rect = QRect(x+5, y+3, w-10, h-6)
            font = QFont('Segoe UI')
            initial = max(11, min(32, int(block.line_height*0.95)))
            flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)
            for size in range(initial, 6, -1):
                font.setPixelSize(size)
                metrics = QFontMetrics(font)
                text_rect = metrics.boundingRect(rect, flags, block.translated)
                if text_rect.height() <= rect.height(): break
            # Stay inside the source area so long translations cannot cover
            # a neighbouring block. Clip text to the same panel.
            draw_h = h
            painter.setPen(QPen(QColor('#9c8451'), 1))
            painter.setBrush(QColor('#211e18'))
            painter.drawRoundedRect(QRect(x, y, w, draw_h), 3, 3)
            self.click_regions.append((QRect(x, y, w, draw_h), block.narrated or block.translated))
            painter.setPen(QColor('#f5e8c7'))
            painter.setFont(font)
            painter.save()
            painter.setClipRect(QRect(x, y, w, draw_h))
            painter.drawText(QRect(x+5, y+3, w-10, draw_h-6), flags, block.translated)
            painter.restore()

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

    def play_icon_rect(self, block, source_rects, width, height):
        size, margin = 22, 5
        x, y, right, bottom = int(block.x), int(block.y), int(block.x+block.width), int(block.y+block.height)
        candidates = [QRect(x-size-margin, y, size, size), QRect(right+margin, y, size, size),
                      QRect(x, y-size-margin, size, size), QRect(x, bottom+margin, size, size)]
        screen = QRect(0, 0, width, height)
        occupied = source_rects + [rect for rect, _ in self.click_regions]
        return next((rect for rect in candidates if screen.contains(rect)
                     and not any(rect.intersects(other) for other in occupied)), None)

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
        self.ocr_limit = self.preferences.value('ocr_requests_per_minute', 5, type=int)
        self.ocr_budget = RequestBudget(self.ocr_limit)
        self.ocr_crop = self.preferences.value('gemini_crop_text', True, type=bool)
        self.ocr_revision = 0
        self.detect_enabled = self.preferences.value('detect_text_first', True, type=bool)
        self.detect_minimum = self.preferences.value('detect_minimum_lines', 3, type=int)
        self.cache_similarity = self.preferences.value('cache_similarity', 98, type=int)
        self.child_friendly = self.preferences.value('child_friendly', False, type=bool)
        self.auto_review = self.preferences.value('auto_review', False, type=bool)
        self.auto_review_model = self.preferences.value('auto_review_model', 'gemini-3.5-flash-lite')
        self.child_pause = self.preferences.value('child_pause_on_error', True, type=bool)
        self.child_instruction = self.preferences.value('child_instruction', DEFAULT_INSTRUCTION, type=str)
        self.min_chars = max(0, min(10000, self.preferences.value('minimum_characters', 30, type=int)))
        self.block_gap = max(0, min(500, self.preferences.value('block_merge_gap', 24, type=int)))
        self.output_language = language_code(self.preferences.value('overlay_language', 'nl'))
        self.narration_choice = self.preferences.value('narration_language', 'same')
        if self.narration_choice not in LANGUAGES and self.narration_choice != 'same': self.narration_choice = 'same'
        self.downloading = False
        self.target = None
        self.last_game_box = None
        self.latest_text = ''
        self.review_page = None
        self.review_blocks = []
        self.review_result = None
        self.review_history = []
        try: self.corrections = json.loads((DATA/'corrections.json').read_text('utf-8'))
        except (OSError, ValueError): self.corrections = {}
        self.spoken = set()
        self.voice_ok = True
        self.ready = False
        self.was_active = False
        self.root = QWidget(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.root.setWindowTitle('Middenaarde Overlay')
        self.root.setMinimumSize(800, 500)
        available_height = self.qt.primaryScreen().availableGeometry().height()
        self.root.resize(900, max(500, min(760, available_height - 90)))
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
        for name in ('Overzicht', 'Detectie', 'Tekstherkenning', 'Talen / Stem', 'Kindvriendelijk', 'Audiocache', 'Vertaalhulp', 'Debuglogs', 'Informatie'):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            page = QWidget(); section = QVBoxLayout(page)
            section.setContentsMargins(18, 14, 18, 14); section.setSpacing(10)
            scroll.setWidget(page); self.tabs.addTab(scroll, name); pages[name] = section
        self.build_detection_settings(pages['Detectie'])
        self.build_information(pages['Informatie'])
        self.build_translation_help(pages['Vertaalhulp'])
        overview = pages['Overzicht']
        layout = pages['Talen / Stem']
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
        self.google_panel = QWidget()
        self.google_panel.setVisible(self.ocr_provider == 'gemini')
        layout.addWidget(self.google_panel)
        layout = pages['Talen / Stem']
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
        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel('Tekstblokken samenvoegen tot'))
        self.block_gap_input = QSpinBox()
        self.block_gap_input.setRange(0, 500)
        self.block_gap_input.setSuffix(' px')
        self.block_gap_input.setValue(self.block_gap)
        self.block_gap_input.setToolTip('Maximale verticale afstand in spelpixels. Ook gecentreerde tekst wordt samengevoegd als de tekstvakken horizontaal overlappen. 0: alleen rakende of overlappende vakken.')
        self.block_gap_input.valueChanged.connect(self.change_block_gap)
        gap_row.addWidget(self.block_gap_input, 1)
        layout.addLayout(gap_row)
        layout = pages['Talen / Stem']
        self.click_to_speak = QCheckBox('Klik op vertaling om voor te lezen / stoppen')
        self.click_to_speak.setChecked(self.preferences.value('click_to_speak', True, type=bool))
        self.click_to_speak.toggled.connect(self.change_click_to_speak)
        layout.addWidget(self.click_to_speak)
        self.duck_box = QCheckBox('Spelgeluid zachter tijdens voorlezen')
        self.duck_box.setChecked(self.preferences.value('duck_game_audio', False, type=bool))
        layout.addWidget(self.duck_box)
        duck_row = QHBoxLayout(); duck_row.addWidget(QLabel('Spelvolume tijdens voorlezen'))
        self.duck_level = QSpinBox(); self.duck_level.setRange(0, 100)
        self.duck_level.setValue(self.preferences.value('duck_game_percent', 30, type=int))
        self.duck_level.setSuffix(' % van normaal')
        self.duck_level.setEnabled(self.duck_box.isChecked())
        duck_row.addWidget(self.duck_level); layout.addLayout(duck_row)
        duck_note = QLabel('Alleen het spel wordt zachter. Na voorlezen of stoppen keert het oorspronkelijke volume terug.')
        duck_note.setWordWrap(True); layout.addWidget(duck_note)
        self.duck_box.toggled.connect(self.change_ducking)
        self.duck_level.valueChanged.connect(self.change_ducking)
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
        self.speaker.duck_enabled = self.duck_box.isChecked()
        self.speaker.duck_level = self.duck_level.value()
        self.build_google_ocr_settings()
        self.build_cache_settings(pages['Audiocache'])
        self.overlay.text_visible = self.show_text.isChecked()
        self.overlay.original_english = self.output_language == 'en'
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
        self.review_result = None
        if hasattr(self, 'review_apply'): self.review_apply.setEnabled(False)
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

    @staticmethod
    def correction_key(language, text):
        return hashlib.sha256((language+'\0'+text).encode()).hexdigest()

    def build_translation_help(self, layout):
        self.auto_review_box = QCheckBox('Vertalingen automatisch verbeteren met Gemini vóór tonen en voorlezen')
        self.auto_review_box.setChecked(self.auto_review)
        layout.addWidget(self.auto_review_box)
        auto_hint = QLabel('Gebruikt je Google-sleutel. Stuurt origineel Engels en vertaling mee; extra API-verbruik. Resultaten worden lokaal hergebruikt. Bij een fout pauzeert de verwerking met een melding.')
        auto_hint.setWordWrap(True); layout.addWidget(auto_hint)
        model_row = QHBoxLayout(); model_row.addWidget(QLabel('Model voor automatische taalcontrole'))
        self.auto_model_choice = QComboBox(); self.auto_model_choice.setEditable(True)
        self.auto_model_choice.addItems(['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-2.5-flash'])
        self.auto_model_choice.setCurrentText(self.auto_review_model)
        model_row.addWidget(self.auto_model_choice); layout.addLayout(model_row)
        self.auto_model_choice.currentTextChanged.connect(self.change_auto_model)
        self.auto_review_box.toggled.connect(self.change_auto_review)
        label = QLabel('Bespreek fouten met AI op basis van de oorspronkelijke Engelse tekst. Een aanvraag gaat naar Gemini en gebruikt API-tegoed. Pas een voorstel pas toe nadat je het hebt gecontroleerd.')
        label.setWordWrap(True); layout.addWidget(label)
        context = QLabel('AI-context: The Lord of the Rings: Journeys in Middle-earth · coöperatief bordspel · Midden-aarde · fantasy, onderzoeken en gevechten')
        context.setWordWrap(True); layout.addWidget(context)
        columns = QHBoxLayout()
        original_column = QVBoxLayout(); original_column.addWidget(QLabel('Origineel Engels'))
        self.original_preview = QTextEdit(); self.original_preview.setReadOnly(True)
        original_column.addWidget(self.original_preview); columns.addLayout(original_column)
        proposal_column = QVBoxLayout(); proposal_column.addWidget(QLabel('Huidige vertaling / AI-voorstel'))
        self.review_preview = QTextEdit(); self.review_preview.setReadOnly(True)
        proposal_column.addWidget(self.review_preview); columns.addLayout(proposal_column)
        layout.addLayout(columns, 2)
        self.review_chat = QPlainTextEdit(); self.review_chat.setReadOnly(True)
        self.review_chat.setMaximumBlockCount(200); layout.addWidget(self.review_chat, 1)
        self.review_instruction = QTextEdit()
        self.review_instruction.setMaximumHeight(85)
        self.review_instruction.setPlaceholderText('Bijvoorbeeld: deduction betekent hier logisch redeneren, niet aftrek. Controleer ook de rest met de Engelse tekst.')
        layout.addWidget(self.review_instruction)
        row = QHBoxLayout()
        self.review_send = QPushButton('Vraag AI om verbetering'); self.review_send.clicked.connect(self.ask_translation_help)
        row.addWidget(self.review_send)
        self.review_apply = QPushButton('Gebruik verbeterde tekst'); self.review_apply.setEnabled(False)
        self.review_apply.clicked.connect(self.apply_translation_help); row.addWidget(self.review_apply)
        layout.addLayout(row)

    def change_auto_review(self, enabled):
        self.auto_review = enabled
        self.preferences.setValue('auto_review', enabled); self.preferences.sync()
        self.invalidate_page()

    def change_auto_model(self, model):
        self.auto_review_model = model.strip()
        self.preferences.setValue('auto_review_model', self.auto_review_model); self.preferences.sync()
        self.invalidate_page()

    def ask_translation_help(self):
        if not self.review_blocks:
            self.review_chat.appendPlainText('Laat eerst een spelpagina herkennen en vertalen.'); return
        instruction = self.review_instruction.toPlainText().strip() or 'Controleer en verbeter deze vertaling in de context van het spel.'
        self.review_chat.appendPlainText('Jij: '+instruction)
        self.review_send.setEnabled(False); self.review_apply.setEnabled(False)
        token = self.review_page
        blocks = copy.deepcopy(self.review_blocks)
        if self.review_result and self.review_result[0] == token:
            for block, item in zip(blocks, self.review_result[1]['items']):
                block.translated, block.narrated = item['translation'], item['narration']
        key, model = self.speaker.keys.get('google', ''), self.ocr_model
        output, narration = self.output_language, self.current_speech_language()
        history = list(self.review_history)
        if self.child_friendly: instruction += '\nBehoud ook deze kindvriendelijke stijl: '+self.child_instruction
        def ask():
            try:
                from translationhelp import review
                result = review(key, model, blocks, instruction, output, narration, history)
                self.events.put(('review_answer', (token, result, instruction, '')))
            except Exception as exc:
                self.events.put(('review_answer', (token, None, instruction, str(exc)[:300])))
        threading.Thread(target=ask, daemon=True).start()

    def apply_translation_help(self):
        if (not self.review_result or self.review_result[0] != self.review_page
                or self.review_page[0] != self.epoch or self.overlay.blocks is not self.review_blocks):
            self.review_chat.appendPlainText('Deze spelpagina is niet meer actueel; herken eerst de huidige pagina.'); return
        result = self.review_result[1]
        for block, item in zip(self.review_blocks, result['items']):
            block.translated, block.narrated = normalize_spacing(item['translation']), normalize_spacing(item['narration'])
            self.corrections[self.correction_key(self.output_language, block.text)] = block.translated
            self.corrections[self.correction_key(self.current_speech_language(), block.text)] = block.narrated
        try:
            temporary = DATA/'corrections.tmp'
            temporary.write_text(json.dumps(self.corrections, ensure_ascii=False), 'utf-8')
            temporary.replace(DATA/'corrections.json')
        except OSError:
            self.review_chat.appendPlainText('De tekst is aangepast, maar opslaan voor later is mislukt.')
        self.preview.setPlainText('\n\n'.join(b.translated for b in self.review_blocks))
        self.latest_text = '\n\n'.join(b.narrated for b in self.review_blocks)
        self.speaker.stop()
        self.overlay.blocks = self.review_blocks
        if self.target and self.enabled.is_set() and active_game(self.target):
            self.overlay.render_blocks(client_box(self.target), self.review_blocks)
        self.review_apply.setEnabled(False)
        self.review_chat.appendPlainText('Toegepast op overlay en voorleestekst; correcties opgeslagen voor hergebruik.')

    def build_detection_settings(self, layout):
        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel('Overeenkomst voor hergebruik van bekende tekst'))
        self.cache_similarity_control = QSpinBox(); self.cache_similarity_control.setRange(95,100)
        self.cache_similarity_control.setSuffix(' %'); self.cache_similarity_control.setValue(self.cache_similarity)
        cache_row.addWidget(self.cache_similarity_control); layout.addLayout(cache_row)
        cache_help = QLabel('Vergelijkt lettervormen in tekstvakken, met begin/einde als controle. Kleine OCR-afwijkingen mogen overeenkomen; getallen en belangrijke opdrachtwoorden moeten gelijk blijven. Lager is toleranter, maar vergroot de kans op onjuist hergebruik.')
        cache_help.setWordWrap(True); layout.addWidget(cache_help)
        self.cache_similarity_control.valueChanged.connect(self.change_cache_similarity)
        title = QLabel('Eerst lokaal controleren of er genoeg tekst staat')
        title.setWordWrap(True); layout.addWidget(title)
        self.detect_box = QCheckBox('Snelle tekstdetectie vóór OCR inschakelen')
        self.detect_box.setChecked(self.detect_enabled); layout.addWidget(self.detect_box)
        row = QHBoxLayout(); row.addWidget(QLabel('OCR starten vanaf'))
        self.detect_lines = QSpinBox(); self.detect_lines.setRange(1, 30)
        self.detect_lines.setValue(self.detect_minimum); self.detect_lines.setSuffix(' mogelijke tekstregels')
        row.addWidget(self.detect_lines); layout.addLayout(row)
        note = QLabel('Deze lichte controle draait lokaal, zonder Gemini-aanvraag. Ze zoekt vormen van tekstregels; ze leest de woorden nog niet. Bij genoeg regels start Ingebouwd, Microsoft of Gemini OCR. Bij ongewijzigde tekst hergebruikt de app de vorige vertaling. Afbeeldingen met veel lijnen kunnen ook als tekst worden gezien. Verlaag de grens of zet de controle uit als echte tekst wordt gemist.')
        note.setWordWrap(True); layout.addWidget(note)
        self.detect_status = QLabel('Nog geen beeld gecontroleerd. Start de overlay en klik in het spel.')
        self.detect_status.setWordWrap(True); layout.addWidget(self.detect_status)
        self.detect_box.toggled.connect(self.change_detection)
        self.detect_lines.valueChanged.connect(self.change_detection)
        layout.addStretch()

    def change_detection(self, _value):
        self.detect_enabled = self.detect_box.isChecked()
        self.detect_minimum = self.detect_lines.value()
        self.preferences.setValue('detect_text_first', self.detect_enabled)
        self.preferences.setValue('detect_minimum_lines', self.detect_minimum)
        self.preferences.sync(); self.invalidate_page()

    def change_cache_similarity(self, value):
        self.cache_similarity=value
        self.preferences.setValue('cache_similarity',value); self.preferences.sync()
        self.ocr_revision+=1
        self.invalidate_page()

    def build_information(self, layout):
        text = QTextEdit(); text.setReadOnly(True)
        text.setHtml("""<h2>Hoe werkt de overlay?</h2>
        <p><b>1. Spelbeeld:</b> alleen het actieve Journeys in Middle-earth-venster of jouw gekozen tekstgebied wordt bekeken. Buiten het spel wordt de overlay verborgen; bij terugkeer komt de bestaande vertaling terug.</p>
        <p><b>2. Snelle detectie (lokaal):</b> de app zoekt mogelijke tekstregels met beeldanalyse. Dit is geen OCR en kost geen API-aanvraag. Bij minder dan de ingestelde hoeveelheid wacht de app op een volgend beeld.</p>
        <p><b>3. Tekst herkennen (OCR):</b> pas daarna leest de gekozen provider de Engelse woorden en hun positie. Ingebouwd en Microsoft blijven lokaal; Gemini ontvangt standaard alleen lokaal uitgesneden tekststroken uit het geselecteerde gebied, verkleind tot maximaal 1600 pixels. Als uitsnijden geen kleinere afbeelding oplevert, wordt het gekozen gebied verkleind verstuurd. De posities worden terugvertaald naar het spelbeeld. De lokale teller blokkeert na standaard 5 Gemini OCR-aanvragen per minuut en vraagt bevestiging voor meer.</p>
        <p><b>4. Vertalen:</b> de herkende Engelse tekst wordt lokaal vertaald naar de gekozen overlay- en voorleestaal. Gemini OCR is dus tekstherkenning, niet de gewone vertaler. Tekstblokken onder de minimumlengte worden overgeslagen.</p>
        <p><b>5. Kindvriendelijk (optioneel):</b> Gemini herschrijft de vertaalde tekst met jouw instructie. Dit is een aparte online aanvraag; de OCR-teller telt deze niet mee.</p>
        <p><b>6. Tonen:</b> de app controleert of de tekst nog op dezelfde pagina staat en legt de vertaling over het Engels. Ongewijzigde tekst wordt hergebruikt; animatie buiten tekstgebieden hoeft geen nieuwe OCR-aanvraag te veroorzaken.</p>
        <p><b>7. Voorlezen:</b> de gekozen stem leest de vertaling. Een ingestelde gedeelde audiocache wordt eerst geraadpleegd. Zonder opname maakt de provider nieuwe audio; met uploadsleutel kan die worden gedeeld. Optioneel wordt alleen het spelgeluid tijdelijk zachter.</p>
        <p><b>Wat gaat online?</b> Gemini OCR: spelbeeldgebied. Kindvriendelijk: vertaalde tekst. Online spraak: voorleestekst. Audiocache: audio plus een technische cachesleutel, geen screenshot of provider-API-sleutel. Ingebouwde/Microsoft OCR, lokale vertaling en Piper werken lokaal.</p>
        <p><b>Vertaalhulp:</b> op dit tabblad staan het originele Engels en de huidige vertaling. Bespreek fouten met Gemini en controleer het voorstel vóór toepassen. Goedgekeurde correcties worden lokaal opgeslagen. Deze AI-aanvragen staan los van de OCR-teller.</p><p><b>Problemen?</b> Bekijk Detectie voor gevonden regels en meettijd. Debuglogs toont focus, OCR-tijden, filters, vertalen en cache. Alleen wacht- en foutmeldingen verschijnen in de speloverlay.</p>""")
        layout.addWidget(text)

    def change_ducking(self, _value):
        self.speaker.duck_enabled = self.duck_box.isChecked()
        self.speaker.duck_level = self.duck_level.value()
        self.duck_level.setEnabled(self.speaker.duck_enabled)
        self.preferences.setValue('duck_game_audio', self.speaker.duck_enabled)
        self.preferences.setValue('duck_game_percent', self.speaker.duck_level)
        self.preferences.sync()

    def build_cache_settings(self, layout):
        from sharedcache import server_url
        self.cache_enabled = QCheckBox('Gedeelde audiocache gebruiken en bijdragen')
        from localcache import cache_directory
        local_hint = QLabel('Lokale cache (blijft bewaard na herstart): '+str(cache_directory(DATA))+
                            '\nBeeldherkenning, tekstvertalingen, AI-verbeteringen en audio worden lokaal hergebruikt. De schakelaar hieronder betreft alleen openbaar delen.')
        local_hint.setWordWrap(True); layout.addWidget(local_hint)
        self.cache_enabled.setChecked(self.preferences.value('shared_audio_enabled', True, type=bool))
        layout.addWidget(self.cache_enabled)
        notice = QLabel('Opnamen worden openbaar gedeeld op jouw server. Dezelfde tekst, provider, stem, model, taal en steminstellingen gebruiken dezelfde opname. Geen screenshots of API-sleutels worden meegestuurd. Zonder serveradres blijft voorlezen normaal werken.')
        notice.setWordWrap(True); layout.addWidget(notice)
        layout.addWidget(QLabel('HTTPS-serveradres'))
        self.cache_url = QLineEdit(self.preferences.value('shared_audio_url', '', type=str))
        self.cache_url.setPlaceholderText('https://cache.example.com'); layout.addWidget(self.cache_url)
        layout.addWidget(QLabel('Uploadsleutel (zonder sleutel: alleen downloaden)'))
        self.cache_token = QLineEdit(self.speaker.keys.get('audio_cache', ''))
        self.cache_token.setEchoMode(QLineEdit.EchoMode.Password); layout.addWidget(self.cache_token)
        self.cache_info = QLabel(); self.cache_info.setWordWrap(True); layout.addWidget(self.cache_info)
        def configure():
            self.speaker.shared_cache.configure(self.cache_enabled.isChecked(), self.cache_url.text(), self.cache_token.text())
            self.cache_info.setText('Uitgeschakeld.' if not self.cache_enabled.isChecked() else
                ('Geen server ingesteld; er worden geen opnamen gedeeld.' if not self.cache_url.text().strip() else
                 'Ingeschakeld: downloaden en automatisch uploaden.' if self.cache_token.text().strip() else 'Ingeschakeld: alleen downloaden.'))
        def save():
            try:
                url = server_url(self.cache_url.text())
                keys = dict(self.speaker.keys); keys['audio_cache'] = self.cache_token.text().strip()
                self.speaker.save(self.speaker.settings, keys)
                self.preferences.setValue('shared_audio_url', url)
                self.preferences.setValue('shared_audio_enabled', self.cache_enabled.isChecked())
                self.preferences.sync(); configure()
            except (OSError, ValueError) as exc:
                self.cache_info.setText('Instellingen niet opgeslagen: '+str(exc))
        def toggle(enabled):
            self.preferences.setValue('shared_audio_enabled', enabled); self.preferences.sync()
            # Only apply saved settings; do not save unfinished address/token edits.
            self.speaker.shared_cache.configure(enabled, self.preferences.value('shared_audio_url', '', type=str), self.speaker.keys.get('audio_cache', ''))
            self.cache_info.setText('Ingeschakeld met opgeslagen instellingen.' if enabled else 'Uitgeschakeld.')
        button = QPushButton('Cache-instellingen opslaan'); button.clicked.connect(save); layout.addWidget(button)
        self.cache_enabled.toggled.connect(toggle)
        try: configure()
        except ValueError: self.cache_info.setText('Ongeldig serveradres; pas het aan en sla opnieuw op.')
        layout.addStretch()

    def build_google_ocr_settings(self):
        dialog = self.google_panel
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
        crop = QCheckBox('Tekststroken uitsnijden vóór verzenden (kleiner beeld)')
        crop.setChecked(self.ocr_crop); layout.addRow(crop)
        crop.setToolTip('Verkleint de afbeelding en herstelt posities op het spelbeeld. Zet uit als tekst wordt gemist.')
        interval = QSpinBox(); interval.setRange(1, 120); interval.setValue(self.ocr_limit); interval.setSuffix(' aanvragen/minuut')
        layout.addRow('Limiet vóór bevestiging', interval)
        rate_hint = QLabel('Geen vaste wachttijd. Bij de limiet stopt de app en vraagt toestemming voor een extra blok aanvragen. Ook de testknop telt mee; hergebruik van dezelfde afbeelding niet.')
        rate_hint.setWordWrap(True); layout.addRow(rate_hint)
        self.ocr_test_button = QPushButton('Test Gemini OCR met dit model')
        self.ocr_test_result = QLabel('Nog niet getest. De test stuurt één klein voorbeeldplaatje naar Google.')
        self.ocr_test_result.setWordWrap(True)
        layout.addRow(self.ocr_test_button); layout.addRow(self.ocr_test_result)
        self.ocr_test_button.clicked.connect(lambda: self.test_google_ocr(key.text().strip(), model.currentText().strip()))
        buttons = QPushButton('Gemini-instellingen opslaan')
        saved = QLabel(); saved.setWordWrap(True)
        layout.addRow(buttons); layout.addRow(saved)
        def save():
            keys = dict(self.speaker.keys); keys['google'] = key.text().strip()
            try: self.speaker.save(self.speaker.settings, keys)
            except OSError:
                QMessageBox.warning(dialog, 'Opslaan mislukt', 'Windows kon de sleutel niet versleuteld opslaan.'); return
            self.ocr_model, self.ocr_limit = model.currentText().strip(), interval.value()
            self.ocr_budget.limit = self.ocr_limit
            self.ocr_crop = crop.isChecked()
            self.preferences.setValue('gemini_crop_text', self.ocr_crop)
            self.preferences.setValue('ocr_model', self.ocr_model)
            self.preferences.setValue('ocr_requests_per_minute', self.ocr_limit)
            self.preferences.sync(); self.ocr_revision += 1; self.epoch += 1
            self.overlay.hide(); saved.setText('Gemini-instellingen opgeslagen.')
        buttons.clicked.connect(save)
        self.google_key_input = key

    def test_google_ocr(self, key, model):
        if not key:
            self.ocr_test_result.setText('Vul eerst een Google API-sleutel in.'); return
        self.ocr_test_button.setEnabled(False)
        self.ocr_test_result.setText('Test loopt: voorbeeld herkennen…')
        def test():
            started = time.monotonic()
            try:
                from PIL import Image, ImageDraw, ImageFont
                from gemini_ocr import GeminiRecognizer
                image = Image.new('RGB', (600, 100), 'white')
                ImageDraw.Draw(image).text((20, 25), 'The heroes enter the forest.', fill='black', font=ImageFont.load_default(size=28))
                blocks = asyncio.run(GeminiRecognizer(key, model, budget=self.ocr_budget).read(image))
                if 'heroes' not in ' '.join(b.text for b in blocks).lower():
                    raise OCRError('Verbinding werkt, maar de voorbeeldtekst is niet correct herkend.')
                message = f'Werkt: {model} herkende de voorbeeldtekst in {time.monotonic()-started:.1f} seconden.'
            except OCRBudgetExceeded as exc:
                self.events.put(('ocr_quota', (str(exc), ('test', key, model))))
                return
            except Exception as exc:
                message = 'Test mislukt: '+str(exc)[:240]
            self.events.put(('ocr_test', message))
        threading.Thread(target=test, daemon=True).start()

    def create_recognizer(self, provider):
        options = {}
        if provider == 'gemini':
            revision = self.ocr_revision
            options = dict(key=self.speaker.keys.get('google', ''), model=self.ocr_model, budget=self.ocr_budget, crop_text=self.ocr_crop, on_image=self.trace,
                on_wait=lambda: self.events.put(('ocr_wait', self.epoch)),
                cancelled=lambda: self.closing.is_set() or not self.enabled.is_set() or self.ocr_provider != provider or self.ocr_revision != revision)
        from localcache import CachedRecognizer, cache_directory
        return CachedRecognizer(Recognizer(provider, **options), cache_directory(DATA)/'ocr-cache',
                                [provider, self.ocr_model if provider == 'gemini' else '', self.ocr_crop, 'rows-v2'], self.cache_similarity/100)

    def change_ocr(self):
        self.ocr_provider = self.ocr_choice.currentData()
        self.google_panel.setVisible(self.ocr_provider == 'gemini')
        if self.ocr_provider == 'gemini': self.google_key_input.setText(self.speaker.keys.get('google', ''))
        self.preferences.setValue('ocr_provider', self.ocr_provider)
        self.preferences.sync()
        self.epoch += 1
        self.overlay.hide()
        self.speaker.stop()
        self.latest_text = ''
        self.preview.clear()
        self.ocr_label.setText('Google Gemini: beeldgebied gaat online naar Google; gebruikt API-tegoed.' if self.ocr_provider == 'gemini' else 'OCR wisselen…')

    def click_speech(self, text):
        self.trace('Overlayklik: stop stem' if self.speaker.busy and self.speaker.current_text == text else 'Overlayklik: start stem')
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
        self.overlay.original_english = self.output_language == 'en'
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

    def change_block_gap(self, value):
        self.block_gap = value
        self.preferences.setValue('block_merge_gap', value)
        self.preferences.sync()
        self.invalidate_page()

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
        cached_image, cached_blocks, cached_layout = None, None, None
        page_hidden = False
        from translationhelp import AutomaticReviewer
        from localcache import cache_directory
        automatic_reviewer = AutomaticReviewer(cache_directory(DATA)/'ai-translations.json')
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
                    continue
                epoch, region, minimum = self.epoch, self.region, self.min_chars
                block_gap = getattr(self, 'block_gap', 24)
                child_enabled, child_instruction = self.child_friendly, self.child_instruction
                child_pause = self.child_pause
                output_language, narration_language = self.output_language, self.current_speech_language()
                if epoch != previous_epoch:
                    previous = rendered = None
                    previous_epoch = epoch
                    cached_image = cached_blocks = cached_layout = None
                    page_hidden = False
                x, y, right, bottom = box
                width, height = right-x, bottom-y
                offset = (0, 0)
                grab_box = box
                if region:
                    a, b, c, d = region
                    offset = (int(width*a), int(height*b))
                    grab_box = (x+offset[0], y+offset[1], x+int(width*c), y+int(height*d))
                image = ImageGrab.grab(bbox=grab_box, all_screens=True)
                # The detector is only a cheap gate for NEW pages. Its contour
                # count/layout changes with animations and must not invalidate
                # text that is still visibly identical (or cancel pending TTS).
                if cached_image is not None and cached_blocks and rendered is not None:
                    from capturecheck import text_regions_unchanged
                    if text_regions_unchanged(cached_image, image, cached_blocks, offset):
                        if page_hidden:
                            self.events.put(('render', (epoch, hwnd, box, cached_blocks, rendered)))
                            page_hidden = False
                        self.trace('Tekst ongewijzigd: voorlezen blijft actief, geen OCR-aanvraag')
                        continue
                    if not page_hidden:
                        from capturecheck import text_page_replaced
                        if text_page_replaced(cached_image, image, cached_blocks, offset):
                            if self.closing.wait(.12): break
                            confirmation = ImageGrab.grab(bbox=grab_box, all_screens=True)
                            if text_regions_unchanged(image, confirmation, cached_blocks, offset):
                                self.trace('Nieuwe verhaalpagina gedetecteerd: oude tekst en spraak verwijderd')
                                self.events.put(('clear', epoch))
                                page_hidden = True
                                image = confirmation
                layout_signature = None
                if getattr(self, 'detect_enabled', False):
                    from textdetect import detect_text
                    started_detection = time.monotonic()
                    detected = detect_text(image)
                    layout_signature = detected.layout
                    message = f'{detected.lines} mogelijke tekstregels · {(time.monotonic()-started_detection)*1000:.0f} ms · grens {self.detect_minimum}'
                    self.events.put(('detect_status', message))
                    if detected.lines < self.detect_minimum and not cached_blocks:
                        self.trace('Detectie: te weinig tekst; geen OCR-aanvraag')
                        self.events.put(('detection_empty', epoch))
                        previous = rendered = None
                        cached_image = cached_blocks = cached_layout = None
                        continue
                if cached_image is not None and rendered is not None:
                    from capturecheck import text_regions_unchanged
                    identical = image.size == cached_image.size and image.tobytes() == cached_image.tobytes()
                    unchanged_text = (layout_signature is not None and layout_signature == cached_layout
                                      and text_regions_unchanged(cached_image, image, cached_blocks, offset))
                    if identical or unchanged_text:
                        self.trace('Tekst ongewijzigd: bestaande vertaling hergebruikt, geen OCR-aanvraag')
                        continue
                started = time.monotonic()
                blocks = recognizer.lookup(image) if hasattr(recognizer, 'lookup') else None
                if blocks is None:
                    self.trace(f'OCR starten: {selected_ocr}, gebied {grab_box[2]-grab_box[0]}x{grab_box[3]-grab_box[1]}')
                    self.events.put(('ocr_wait', epoch))
                    blocks = loop.run_until_complete(recognizer.read(image))
                else:
                    self.trace('Bekende pagina uit lokale cache: OCR overgeslagen')
                if selected_ocr == 'gemini': self.events.put(('ocr_wait_done', epoch))
                blocks = merge_blocks(blocks, block_gap)
                count = len(blocks)
                blocks = [b for b in blocks if len(b.text.strip()) >= minimum]
                self.trace(f'OCR klaar in {time.monotonic()-started:.1f}s: {count} blokken, {len(blocks)} boven minimum {minimum}')
                for block in blocks:
                    block.x += offset[0]; block.y += offset[1]
                # OCR box jitter is not a new story page.
                signature = tuple(' '.join(b.text.split()) for b in blocks)
                if epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                if signature != previous:
                    previous = signature
                if signature == rendered:
                    translations = {' '.join(b.text.split()):(b.translated,b.narrated) for b in cached_blocks or []}
                    for block in blocks:
                        block.translated, block.narrated = translations.get(' '.join(block.text.split()), ('',''))
                    if page_hidden and cached_blocks:
                        self.events.put(('render', (epoch, hwnd, box, blocks, signature)))
                        page_hidden = False
                    cached_image, cached_blocks, cached_layout = image, blocks, layout_signature
                    continue
                if rendered is not None and not page_hidden:
                    self.events.put(('clear', epoch))
                    page_hidden = True
                if not blocks:
                    self.events.put(('clear', epoch))
                    self.events.put(('status', 'Actief · geen tekst gevonden die aan de ingestelde minimumlengte voldoet'))
                    rendered = signature
                    cached_image = cached_blocks = cached_layout = None
                    continue
                self.trace(f'Vertalen starten: {len(blocks)} blokken naar {output_language}/{narration_language}')
                self.events.put(('status', 'Tekst in de gekozen taal maken…'))
                required = {output_language, narration_language}
                # Keep at most the two active models in memory.
                for code in list(translators):
                    if code not in required: del translators[code]
                for code in required:
                    if code not in translators: translators[code] = Translator(code)
                def translate_one(code, text):
                    return getattr(self, 'corrections', {}).get(self.correction_key(code,text)) or translators[code].translate(text)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    jobs = {}
                    for block in sorted(blocks,key=lambda b:-len(b.text)):
                        for code in required:
                            key=(code,block.text)
                            if key not in jobs: jobs[key]=pool.submit(translate_one,*key)
                    for block in blocks:
                        if self.closing.is_set() or epoch!=self.epoch:
                            for job in jobs.values(): job.cancel()
                            break
                        block.translated=jobs[(output_language,block.text)].result()
                        block.narrated=jobs[(narration_language,block.text)].result()
                if self.closing.is_set() or epoch != self.epoch or not self.enabled.is_set() or not active_game(hwnd): continue
                if getattr(self, 'auto_review', False):
                    self.events.put(('status', 'Vertaling natuurlijker maken met Gemini…'))
                    self.trace('Automatische AI-vertaalcontrole starten')
                    try:
                        result = automatic_reviewer.improve(self.speaker.keys.get('google', ''), self.auto_review_model,
                                                            blocks, output_language, narration_language)
                    except Exception as exc:
                        raise OCRError('AI-vertaalcontrole mislukt: '+str(exc)[:200]) from None
                    if self.closing.is_set() or epoch != self.epoch or not self.enabled.is_set(): continue
                    for block, item in zip(blocks, result['items']):
                        block.translated, block.narrated = item['translation'], item['narration']
                    self.trace('Automatische AI-vertaalcontrole klaar')
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
                    fresh_blocks = merge_blocks(fresh_blocks, block_gap)
                fresh_blocks = [b for b in fresh_blocks if len(b.text.strip()) >= minimum]
                if sorted(b.text for b in fresh_blocks) != sorted(b.text for b in blocks):
                    self.trace('Herkenning veranderde tijdens vertalen; opnieuw scannen')
                    previous = rendered = None
                    continue
                for block in blocks:
                    block.translated = normalize_spacing(block.translated)
                    block.narrated = normalize_spacing(block.narrated)
                self.events.put(('render', (epoch, hwnd, box, blocks, signature)))
                page_hidden = False
                rendered = signature
                cached_image, cached_blocks, cached_layout = image, blocks, layout_signature
            except OCRBudgetExceeded as exc:
                self.enabled.clear()
                self.events.put(('ocr_quota', (str(exc), ('overlay',))))
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
            elif kind == 'review_answer':
                token, result, instruction, error = value
                self.review_send.setEnabled(True)
                if token != self.review_page:
                    self.review_chat.appendPlainText('De spelpagina is gewijzigd; dit voorstel wordt niet toegepast.')
                elif error:
                    self.review_chat.appendPlainText('AI: '+error)
                else:
                    self.review_result = (token, result)
                    self.review_preview.setPlainText('\n\n'.join(i['translation'] for i in result['items']))
                    self.review_chat.appendPlainText('AI: '+result['explanation'])
                    self.review_history += [{'user': instruction, 'assistant': result['explanation']}]
                    self.review_history = self.review_history[-6:]
                    self.review_apply.setEnabled(True)
            elif kind == 'detect_status':
                self.detect_status.setText(value)
            elif kind == 'detection_empty' and value == self.epoch:
                if self.overlay.blocks: self.speaker.stop()
                self.overlay.blocks = []; self.overlay.click_regions = []; self.overlay.hide()
                self.status.setText('Detectie: wacht op voldoende tekst; OCR nog niet gestart')
            elif kind == 'ocr_quota':
                message, retry = value
                self.ocr_test_button.setEnabled(True)
                self.status.setText('Gemini OCR geblokkeerd: bevestiging nodig.')
                self.trace(message)
                answer = QMessageBox.question(self.root, 'Gemini OCR-limiet bereikt', message+'\nDit kan extra API-tegoed gebruiken.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer == QMessageBox.StandardButton.Yes:
                    self.ocr_budget.approve()
                    if retry[0] == 'test': self.test_google_ocr(retry[1], retry[2])
                    else:
                        self.enabled.set(); self.start_button.setText('Pauzeer overlay')
                        self.status.setText('Extra aanvragen toegestaan · klik in het spel')
                elif retry[0] == 'overlay':
                    self.start_button.setText('Start overlay')
                    self.status.setText('Gemini OCR gepauzeerd: extra aanvragen niet toegestaan.')
                else: self.ocr_test_result.setText('Test gestopt: extra aanvraag niet toegestaan.')
            elif kind == 'ocr_test':
                self.ocr_test_button.setEnabled(True)
                self.ocr_test_result.setText(value)
                self.trace(value)
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
                token = (epoch, hwnd, signature)
                if token != self.review_page:
                    # Replace only once the new translation has been validated.
                    # Background OCR must not erase a clickable translation or
                    # cancel its narration while preparing a replacement.
                    self.speaker.stop()
                    self.review_page, self.review_blocks = token, blocks
                    self.review_result, self.review_history = None, []
                    self.review_apply.setEnabled(False); self.review_chat.clear()
                    self.original_preview.setPlainText('\n\n'.join(b.text for b in blocks))
                    self.review_preview.setPlainText('\n\n'.join(b.translated for b in blocks))
                self.review_blocks = blocks
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
        self.speaker.close()
        for code in self.hotkeys:
            user32.UnregisterHotKey(native_handle(self.root), code)
        self.overlay.close()
        self.qt.quit()
        event.accept()

    def run(self):
        self.root.show()
        self.qt.exec()
