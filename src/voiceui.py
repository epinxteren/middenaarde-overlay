"""Voice configuration and asynchronous voice discovery."""
import copy
import queue
import threading
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QFormLayout, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox, QTextEdit, QPushButton, QDialogButtonBox)
from speech import PROVIDERS, VOICES, voices


class VoiceDialog(QDialog):
    def __init__(self, parent, speaker):
        super().__init__(parent)
        self.speaker = speaker
        self.settings, self.keys = copy.deepcopy(speaker.settings), dict(speaker.keys)
        self.current = self.settings['provider']
        self.results = queue.Queue()
        self.setWindowTitle('Stem & providers')
        self.resize(640, 680)
        layout = QVBoxLayout(self)
        form = QFormLayout(); self.form = form; layout.addLayout(form)
        self.provider = QComboBox()
        for key, label in PROVIDERS.items(): self.provider.addItem(label, key)
        self.provider.setCurrentIndex(self.provider.findData(self.current))
        form.addRow('Provider', self.provider)
        self.info = QLabel(); self.info.setWordWrap(True); layout.addWidget(self.info)
        self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('Versleuteld opgeslagen voor jouw Windows-account')
        form.addRow('API-sleutel', self.key)
        self.region = QLineEdit(); form.addRow('Azure-regio', self.region)
        self.voice = QComboBox(); self.voice.setEditable(True); form.addRow('Stem / Voice ID', self.voice)
        self.fetch = QPushButton('Haal stemmen op'); self.fetch.clicked.connect(self.fetch_voices); form.addRow('', self.fetch)
        self.model = QComboBox(); self.model.setEditable(True); form.addRow('Model', self.model)
        self.speed = QDoubleSpinBox(); self.speed.setRange(.7, 1.2); self.speed.setSingleStep(.05); form.addRow('Tempo (1 = normaal)', self.speed)
        self.volume = QSpinBox(); self.volume.setRange(0,100); self.volume.setSuffix(' %'); form.addRow('Volume', self.volume)
        self.style = QTextEdit(); self.style.setMaximumHeight(80); form.addRow('Google-vertelstijl', self.style)
        self.stability = QDoubleSpinBox(); self.similarity = QDoubleSpinBox(); self.expressiveness = QDoubleSpinBox()
        for label, widget in [('ElevenLabs stabiliteit', self.stability), ('ElevenLabs stemgelijkenis', self.similarity), ('ElevenLabs expressiviteit', self.expressiveness)]:
            widget.setRange(0,1); widget.setSingleStep(.05); form.addRow(label, widget)
        self.sample = QTextEdit('De helden trekken door het oude woud. In de verte klinkt een hoorn. Het avontuur in Midden-aarde begint.')
        self.sample.setMaximumHeight(75); layout.addWidget(self.sample)
        row = QHBoxLayout(); layout.addLayout(row)
        preview = QPushButton('Luistervoorbeeld'); preview.clicked.connect(self.preview); row.addWidget(preview)
        stop = QPushButton('Stop'); stop.clicked.connect(speaker.stop); row.addWidget(stop)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Opslaan')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Annuleren')
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.provider.currentIndexChanged.connect(self.change)
        self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(100)
        self.load()

    def capture(self):
        p = self.settings['profiles'][self.current]
        index = self.voice.currentIndex()
        p.update(voice=self.voice.itemData(index) if index>=0 and self.voice.currentText()==self.voice.itemText(index) else self.voice.currentText().strip(),
                 model=self.model.currentText().strip(), region=self.region.text().strip(), speed=self.speed.value(),
                 volume=self.volume.value(), style=self.style.toPlainText(), stability=self.stability.value(),
                 similarity=self.similarity.value(), expressiveness=self.expressiveness.value())
        self.keys[self.current] = self.key.text().strip()

    def load(self):
        p = self.settings['profiles'][self.current]
        self.voice.clear()
        for v in VOICES[self.current]: self.voice.addItem(v, v)
        self.voice.setCurrentText(p['voice'])
        self.model.clear()
        self.model.addItems(['gemini-3.1-flash-tts-preview', 'gemini-2.5-flash-preview-tts', 'gemini-2.5-pro-preview-tts'] if self.current=='google' else ['eleven_multilingual_v2', 'eleven_flash_v2_5'])
        self.model.setCurrentText(p['model'])
        self.key.setText(self.keys.get(self.current, '')); self.region.setText(p['region'])
        self.speed.setValue(p['speed']); self.volume.setValue(p['volume']); self.style.setPlainText(p['style'])
        self.stability.setValue(p['stability']); self.similarity.setValue(p['similarity']); self.expressiveness.setValue(p['expressiveness'])
        self.key.setEnabled(self.current in ('google','eleven','azure'))
        self.region.setEnabled(self.current=='azure'); self.style.setEnabled(self.current=='google')
        self.model.setEnabled(self.current in ('google','eleven'))
        for w in (self.stability,self.similarity,self.expressiveness): w.setEnabled(self.current=='eleven')
        for widget in (self.key,self.region,self.model,self.style,self.stability,self.similarity,self.expressiveness):
            self.form.setRowVisible(widget, widget.isEnabled())
        self.info.setText({
            'piper': 'Volledig lokaal, zonder account. Kies een van de 52 meegeleverde stemmen.',
            'edge': 'Online zonder API-sleutel, via het onofficiële edge-tts. Nederlandse tekst gaat naar Microsoft. Beschikbaarheid kan veranderen.',
            'google': 'Nederlandse tekst en vertelstijl gaan naar Google AI. Gebruik een Gemini API-sleutel uit AI Studio. Gratis quota en modeltoegang verschillen per account; daarboven kan gebruik geld kosten.',
            'eleven': 'Nederlandse tekst gaat naar ElevenLabs. API-sleutel en beschikbare stem nodig. Gebruik verbruikt je accounttegoed. Niet elke stem is beschikbaar met elk abonnement.',
            'azure': 'Nederlandse tekst gaat naar Microsoft Azure. Vereist een Speech-resource, sleutel en bijbehorende regio. Controleer je gratis quota en tarieven.'}[self.current])

    def change(self):
        self.capture(); self.speaker.stop()
        self.current = self.provider.currentData(); self.settings['provider'] = self.current
        self.load(); self.status.clear()

    def fetch_voices(self):
        self.capture()
        provider, profile, key = self.current, copy.deepcopy(self.settings['profiles'][self.current]), self.keys.get(self.current,'')
        self.fetch.setEnabled(False); self.status.setText('Stemmen ophalen…')
        def run():
            try: result = voices(provider, profile, key); error = None
            except Exception: result = []; error = 'Ophalen mislukt. Controleer sleutel en verbinding; je kunt ook zelf een Voice ID invullen.'
            self.results.put((provider,result,error))
        threading.Thread(target=run,daemon=True).start()

    def poll(self):
        try: provider, result, error = self.results.get_nowait()
        except queue.Empty: return
        self.fetch.setEnabled(True)
        if provider != self.current: return
        if error: self.status.setText(error); return
        previous = self.settings['profiles'][provider]['voice']
        self.voice.clear()
        for label, identifier in result: self.voice.addItem(label,identifier)
        index = self.voice.findData(previous)
        if index>=0: self.voice.setCurrentIndex(index)
        self.status.setText(f'{len(result)} stemmen gevonden. ElevenLabs toont maximaal de eerste 100; een Voice ID kan ook handmatig.')

    def preview(self):
        self.capture()
        self.status.setText('Voorbeeld aangevraagd. Online diensten kunnen hiervoor tegoed gebruiken.')
        self.speaker.say(self.sample.toPlainText(), self.settings, self.keys)

    def save(self):
        self.capture()
        try: self.speaker.save(self.settings,self.keys)
        except OSError:
            self.status.setText('Opslaan mislukt. Controleer of Windows toegang tot de instellingenmap toestaat.'); return
        self.accept()

    def done(self, result):
        self.speaker.stop(); super().done(result)
