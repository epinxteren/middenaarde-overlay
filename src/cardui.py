"""Camera work stays off the UI thread; capture never uploads photographs."""
import asyncio
import queue
import threading
from io import BytesIO

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QPlainTextEdit, QCheckBox)
from cards import CardLibrary, CaptureGate, card_crop, readable_text, text_key
from localcache import cache_directory
from overlay import DATA


def camera_scan(index, landscape, cancelled, events, previews):
    import cv2
    from PIL import Image
    from portable_ocr import PortableRecognizer
    camera = None
    try:
        camera = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not camera.isOpened():
            raise RuntimeError('Camera niet beschikbaar. Kies een ander nummer, sluit andere camera-apps en controleer Windows > Privacy > Camera.')
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        recognizer, gate = PortableRecognizer(), CaptureGate()
        previous_text = None
        while not cancelled.is_set():
            ok, frame = camera.read()
            if not ok: raise RuntimeError('Geen camerabeeld ontvangen. Sluit de scanner en probeer opnieuw.')
            crop, (x, y, w, h) = card_crop(frame, landscape)
            preview = frame.copy()
            cv2.rectangle(preview, (x, y), (x+w, y+h), (80, 210, 120), 3)
            rgb = cv2.cvtColor(cv2.resize(preview, (640, 360)), cv2.COLOR_BGR2RGB)
            try: previews.put_nowait(rgb)
            except queue.Full: pass
            if not gate.ready(crop):
                previous_text = None
                cancelled.wait(.08)
                continue
            events.put(('status', 'Tekst controleren… houd de kaart stil.'))
            photo = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            blocks = asyncio.run(recognizer.read(photo))
            text = readable_text(blocks)
            if cancelled.is_set(): return
            if text and previous_text == text_key(text):
                output = BytesIO(); photo.save(output, format='JPEG', quality=92)
                events.put(('capture', (text, output.getvalue())))
                return
            previous_text = text_key(text) if text else None
            events.put(('status', 'Nogmaals controleren…' if text else 'Geen leesbare tekst. Houd de kaart dichterbij en vermijd reflecties.'))
            cancelled.wait(.15)
    except Exception as exc:
        if not cancelled.is_set(): events.put(('error', str(exc)))
    finally:
        if camera is not None: camera.release()


class CardDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner.root)
        self.owner = owner
        self.setWindowTitle('Kaarten lezen · Ctrl+Alt+K')
        self.resize(720, 750)
        self.library = CardLibrary(cache_directory(DATA)/'cards')
        self.events, self.previews = queue.Queue(), queue.Queue(maxsize=1)
        self.cancelled = threading.Event()
        self.worker = None
        self.reading_text = ''
        self.reading_language = 'nl'
        layout = QVBoxLayout(self)
        info = QLabel('Plaats één kaart rechtop en volledig binnen het groene kader. '
                      'Bij een scherp, stil beeld en twee gelijke tekstherkenningen wordt automatisch een foto genomen. '
                      'Foto’s en kaarten blijven lokaal. Symbolen en pictogrammen worden niet betrouwbaar voorgelezen.')
        info.setWordWrap(True); layout.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel('Camera'))
        self.camera = QSpinBox(); self.camera.setRange(0, 9)
        self.camera.setValue(owner.preferences.value('card_camera', 0, type=int)); row.addWidget(self.camera)
        self.orientation = QComboBox(); self.orientation.addItems(['Staande kaart', 'Liggende kaart']); row.addWidget(self.orientation)
        self.source = QComboBox(); self.source.addItems(['Engelse kaart · vertalen', 'Kaart al in voorleestaal']); row.addWidget(self.source)
        layout.addLayout(row)
        self.preview = QLabel('Klik Camera starten om te scannen.'); self.preview.setMinimumHeight(200)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.preview)
        self.status = QLabel('Gereed'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.auto = QCheckBox('Na herkenning automatisch voorlezen'); self.auto.setChecked(True); layout.addWidget(self.auto)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); layout.addWidget(self.text)
        buttons = QHBoxLayout()
        self.scan = QPushButton('Camera starten / volgende kaart'); self.scan.clicked.connect(self.start_scan); buttons.addWidget(self.scan)
        self.play = QPushButton('Opnieuw voorlezen'); self.play.clicked.connect(self.speak); self.play.setEnabled(False); buttons.addWidget(self.play)
        stop = QPushButton('Stop stem'); stop.clicked.connect(owner.speaker.stop); buttons.addWidget(stop)
        layout.addLayout(buttons)
        self.saved = QComboBox(); layout.addWidget(self.saved)
        self.open_saved = QPushButton('Opgeslagen kaart lezen'); self.open_saved.clicked.connect(self.load_saved); layout.addWidget(self.open_saved)
        close = QPushButton('Terug naar spel'); close.clicked.connect(self.reject); layout.addWidget(close)
        self.refresh_library()
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(80)

    def refresh_library(self):
        self.saved.clear()
        for key, text in self.library.entries(): self.saved.addItem(text.replace('\n', ' ')[:100], (key, text))

    def busy(self, value):
        for widget in (self.scan, self.open_saved, self.camera, self.orientation, self.source, self.saved): widget.setEnabled(not value)
        self.play.setEnabled(not value and bool(self.reading_text))

    def start_scan(self):
        if self.worker and self.worker.is_alive(): return
        self.owner.speaker.stop(); self.reading_text = ''; self.text.clear()
        self.owner.preferences.setValue('card_camera', self.camera.value())
        self.cancelled = threading.Event(); self.busy(True)
        self.status.setText('Camera openen…')
        self.worker = threading.Thread(target=camera_scan, args=(self.camera.value(),
            bool(self.orientation.currentIndex()), self.cancelled, self.events, self.previews), daemon=True)
        self.worker.start()

    def load_saved(self):
        item = self.saved.currentData()
        if item: self.prepare(*item)

    def prepare(self, key, text):
        self.owner.speaker.stop(); self.busy(True)
        self.text.setPlainText(text); self.status.setText('Kaart voorbereiden…')
        options = self.owner.card_options(self.source.currentIndex() == 0)
        self.reading_language = options['language']
        cancelled = self.cancelled
        def work():
            try:
                value = self.library.reading(key, options)
                warning = ''
                if value is None:
                    value, warning = self.owner.prepare_card(text, options)
                    if cancelled.is_set(): return
                    if not warning: self.library.reading(key, options, value)
                if not cancelled.is_set(): self.events.put(('reading', (value, warning)))
            except Exception as exc:
                if not cancelled.is_set(): self.events.put(('error', str(exc)))
        self.worker = threading.Thread(target=work, daemon=True); self.worker.start()

    def speak(self):
        if self.reading_text: self.owner.speaker.say(self.reading_text, language=self.reading_language)

    def poll(self):
        try:
            rgb = self.previews.get_nowait()
            h, w = rgb.shape[:2]
            self.preview.setPixmap(QPixmap.fromImage(QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()))
        except queue.Empty: pass
        while True:
            try: kind, value = self.events.get_nowait()
            except queue.Empty: break
            if kind == 'capture':
                try:
                    text, photo = value
                    key = self.library.save(text, photo); self.refresh_library(); self.prepare(key, text)
                except Exception as exc: self.status.setText(str(exc)); self.busy(False)
            elif kind == 'reading':
                self.reading_text, warning = value; self.text.setPlainText(self.reading_text)
                self.status.setText(warning or 'Kaart opgeslagen. Kies volgende kaart om opnieuw te scannen.')
                self.busy(False)
                if self.auto.isChecked(): self.speak()
            elif kind == 'error': self.status.setText('Mislukt: '+value); self.busy(False)
            elif kind == 'status': self.status.setText(value)

    def done(self, result):
        self.cancelled.set(); self.timer.stop(); self.owner.speaker.stop()
        super().done(result)
