"""In-app setup instructions. No credentials are included in help content."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox

HELP = {
    'google': '''<h2>Google Gemini: één sleutel voor OCR en spraak</h2>
<ol><li>Open <a href="https://aistudio.google.com/api-keys">Google AI Studio – API Keys</a> en log zelf in.</li>
<li>Kies <b>Create API key</b> en een project. Gebruik een sleutel voor de <b>Gemini API / Generative Language API</b>.</li>
<li>Kopieer de sleutel. Plak hem in deze app bij <b>Gemini OCR-instellingen → Google API-sleutel</b>,
of bij <b>Stem &amp; providers → Google AI · Gemini → API-sleutel</b>.</li>
<li>Klik <b>Opslaan</b>. Dezelfde opgeslagen sleutel is daarna in beide instellingen beschikbaar.
Voor spraak kun je eerst <b>Luistervoorbeeld</b> gebruiken; voor OCR kies je <b>Google Gemini · online</b> en <b>Start overlay</b>.</li></ol>
<h3>Ik heb al een Google-project of sleutel</h3>
<p>AI Studio toont niet automatisch al je Cloud-projecten. Ga naar <b>Dashboard → Projects → Import projects</b>,
selecteer je bestaande project en ga terug naar <b>API Keys</b>. Controleer dat je met hetzelfde Google-account bent ingelogd.</p>
<p>Een sleutel die beperkt is tot een andere API wordt niet altijd getoond. Je kunt je bestaande project gebruiken
en daarin een aparte Gemini-sleutel maken; je hoeft de sleutel van andere apps niet te wijzigen.</p>
<h3>Google Cloud Text-to-Speech is een andere API</h3>
<p>Sleutels of serviceaccounts uit <a href="https://console.cloud.google.com/apis/credentials">Google Cloud → APIs &amp; Services → Credentials</a>
zijn niet automatisch Gemini-sleutels. Deze app ondersteunt <b>Gemini-spraak</b>, niet de afzonderlijke Cloud Text-to-Speech API.
Een serviceaccount-JSON-bestand kun je niet in het sleutelveld plakken.</p>
<p>Voor OCR gebruik je een <b>beeldmodel</b>; voor voorlezen een <b>TTS-model</b>. De sleutel kan dezelfde zijn,
maar modellen en quota verschillen. Bij een limietmelding: kijk in AI Studio bij <b>Usage / Rate limits</b> naar het juiste project en model.</p>
<p><a href="https://ai.google.dev/gemini-api/docs/api-key">Officiële uitleg over sleutels en het importeren van projecten</a></p>''',
    'eleven': '''<h2>ElevenLabs</h2><ol>
<li>Open <a href="https://elevenlabs.io/app/developers">ElevenLabs – Developers</a> en ga naar <b>API Keys</b>.</li>
<li>Maak een sleutel met <b>Text to Speech → Toegang</b> en <b>Stemmen / Voices → Lezen</b>. Overige rechten zijn niet nodig.</li>
<li>Kies in deze app <b>Stem &amp; providers → ElevenLabs</b> en plak de sleutel bij <b>API-sleutel</b>.</li>
<li>Klik <b>Haal stemmen op</b>, kies een stem, probeer <b>Luistervoorbeeld</b> en klik <b>Opslaan</b>.</li></ol>
<p>Een stem in de lijst is niet automatisch inbegrepen bij jouw abonnement. Voice Library-stemmen kunnen een betaald abonnement voor API-gebruik vereisen.</p>''',
    'azure': '''<h2>Microsoft Azure Speech</h2><ol>
<li>Open <a href="https://portal.azure.com/">Azure Portal</a> en selecteer je <b>Speech-resource</b>.</li>
<li>Open <b>Keys and Endpoint / Sleutels en eindpunt</b>. Kopieer een sleutel en noteer de regio van diezelfde resource.</li>
<li>Kies in deze app <b>Stem &amp; providers → Microsoft Azure Speech</b>.
Plak de sleutel bij <b>API-sleutel</b> en vul <b>Azure-regio</b> in, bijvoorbeeld westeurope als dat de regio van jouw resource is.</li>
<li>Klik <b>Haal stemmen op</b>, kies een stem en klik na een luistervoorbeeld op <b>Opslaan</b>.</li></ol>''',
}


def show_key_help(parent, provider='google'):
    dialog = QDialog(parent)
    dialog.setWindowTitle('API-sleutel toevoegen – uitleg')
    dialog.resize(660, 580)
    layout = QVBoxLayout(dialog)
    text = QTextBrowser(); text.setOpenExternalLinks(True)
    text.setStyleSheet('QTextBrowser { background:#101820; color:#e7e0cc; padding:10px; }')
    text.setHtml('<style>a { color:#8fc9ff; }</style>' + HELP.get(provider, '<h2>Geen API-sleutel nodig</h2><p>Piper en Edge hebben geen sleutelveld nodig.</p>') +
        '<hr><p><b>Opslag:</b> sleutels worden met Windows DPAPI versleuteld voor jouw Windows-account opgeslagen. '
        'Op een andere pc voer je ze opnieuw in. Plak sleutels alleen in de app, niet in chats of screenshots. '
        'Online gebruik kan API-tegoed kosten; ingebouwde OCR en Piper werken zonder sleutel.</p>')
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText('Sluiten')
    buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
    dialog.exec()
