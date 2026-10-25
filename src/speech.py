"""Selectable narration providers. Credentials use Windows DPAPI, never plain settings/logs."""
import asyncio
import base64
import copy
import hashlib
import io
import json
import queue
import re
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import wave
import winsound
from xml.sax.saxutils import escape

PROVIDERS = {'piper': 'Piper · offline', 'edge': 'Microsoft Edge · online, zonder sleutel',
             'google': 'Google AI · Gemini', 'eleven': 'ElevenLabs', 'azure': 'Microsoft Azure Speech'}
VOICES = {
    'piper': [str(i) for i in range(52)],
    'edge': ['nl-NL-MaartenNeural', 'nl-NL-FennaNeural', 'nl-NL-ColetteNeural', 'nl-BE-ArnaudNeural', 'nl-BE-DenaNeural'],
    'google': ['Charon', 'Orus', 'Kore', 'Puck', 'Fenrir', 'Aoede', 'Leda', 'Zephyr', 'Algenib'],
    'eleven': [],
    'azure': ['nl-NL-MaartenNeural', 'nl-NL-FennaNeural', 'nl-NL-ColetteNeural', 'nl-BE-ArnaudNeural', 'nl-BE-DenaNeural'],
}
DEFAULTS = {p: {'voice': (v[0] if v else ''), 'speed': 1.0, 'volume': 90,
    'style': 'Lees in natuurlijk Nederlands, als een warme, meeslepende fantasyverteller.',
    'model': 'gemini-3.1-flash-tts-preview' if p == 'google' else 'eleven_multilingual_v2',
    'region': 'westeurope', 'stability': 0.5, 'similarity': 0.75, 'expressiveness': 0.35}
    for p, v in VOICES.items()}


class SpeechError(Exception):
    pass


def request(url, key_header=None, key='', payload=None, headers=None):
    headers = dict(headers or {})
    if key_header:
        if not key.strip(): raise SpeechError('Vul de API-sleutel in bij Stem & providers.')
        headers[key_header] = key.strip()
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload, headers=headers), timeout=40) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        messages = {400: 'Deze stem of dit model accepteert de instellingen niet.',
                    401: 'API-sleutel ongeldig.', 403: 'Geen toegang tot deze stem of dienst.',
                    404: 'Stem of model niet gevonden.', 429: 'Limiet bereikt. Controleer je tegoed of probeer later.'}
        raise SpeechError(messages.get(exc.code, f'Spraakdienst gaf HTTP {exc.code}. Probeer later.')) from None
    except (OSError, urllib.error.URLError):
        raise SpeechError('Geen verbinding met de spraakdienst. Controleer internet en probeer opnieuw.') from None


def voices(provider, profile, key):
    if provider == 'eleven':
        result = json.loads(request('https://api.elevenlabs.io/v2/voices?page_size=100', 'xi-api-key', key))
        return [(v['name'], v['voice_id']) for v in result.get('voices', [])]
    if provider == 'edge':
        import edge_tts
        async def fetch(): return await asyncio.wait_for(edge_tts.list_voices(), 30)
        return [(v['FriendlyName'], v['ShortName']) for v in asyncio.run(fetch()) if v['Locale'].startswith('nl-')]
    if provider == 'azure':
        region = profile['region']
        if not re.fullmatch('[a-z0-9]+', region): raise SpeechError('Ongeldige Azure-regio.')
        result = json.loads(request(f'https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list', 'Ocp-Apim-Subscription-Key', key))
        return [(v['DisplayName'], v['ShortName']) for v in result if v['Locale'].startswith('nl-')]
    return [(v, v) for v in VOICES[provider]]


def wav_bytes(pcm, rate=24000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate); stream.writeframes(pcm)
    return out.getvalue()


def synthesize(provider, profile, key, text, resource):
    voice, speed = profile['voice'], float(profile['speed'])
    if provider == 'piper':
        from piper import PiperVoice, SynthesisConfig
        # One worker owns this cached model.
        if not hasattr(synthesize, 'piper'):
            synthesize.piper = PiperVoice.load(str(resource/'voice'/'nl_NL-mls-medium.onnx'))
        out = io.BytesIO()
        with wave.open(out, 'wb') as stream:
            synthesize.piper.synthesize_wav(text, stream, syn_config=SynthesisConfig(speaker_id=int(voice), length_scale=1/speed))
        return out.getvalue()
    if provider == 'google':
        model = profile['model']
        if not re.fullmatch('[a-zA-Z0-9._-]+', model): raise SpeechError('Ongeldige Google-modelnaam.')
        prompt = f"{profile['style']} Spreek op tempo {speed:.2f} maal normaal. Lees uitsluitend de volgende tekst voor:\n\n{text}"
        payload = {'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {
            'responseModalities': ['AUDIO'], 'speechConfig': {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': voice}}}}}
        result = json.loads(request(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent', 'x-goog-api-key', key, payload))
        for candidate in result.get('candidates', []):
            for part in candidate.get('content', {}).get('parts', []):
                data = part.get('inlineData', {})
                if data.get('mimeType', '').lower().startswith('audio/') and data.get('data'):
                    audio = base64.b64decode(data['data'], validate=True)
                    if audio.startswith(b'RIFF'): return audio
                    mime = data['mimeType'].lower()
                    if 'l16' not in mime and 'pcm' not in mime:
                        import miniaudio
                        try:
                            decoded = miniaudio.decode(audio, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
                        except Exception:
                            raise SpeechError('Google-audioformaat wordt niet ondersteund: '+mime[:100]) from None
                        return wav_bytes(decoded.samples.tobytes())
                    rate = re.search(r'rate=(\d+)', mime)
                    return wav_bytes(audio, int(rate.group(1)) if rate else 24000)
        raise SpeechError('Google gaf geen audio terug. Probeer een andere tekst of model.')
    if provider == 'azure':
        region = profile['region']
        if not re.fullmatch('[a-z0-9]+', region): raise SpeechError('Ongeldige Azure-regio.')
        ssml = f'<speak version="1.0" xml:lang="nl-NL"><voice name="{escape(voice, {chr(34): "&quot;"})}"><prosody rate="{round((speed-1)*100):+d}%">{escape(text)}</prosody></voice></speak>'
        return request(f'https://{region}.tts.speech.microsoft.com/cognitiveservices/v1', 'Ocp-Apim-Subscription-Key', key, ssml.encode(),
                       {'Content-Type': 'application/ssml+xml', 'X-Microsoft-OutputFormat': 'riff-24khz-16bit-mono-pcm', 'User-Agent': 'MiddenaardeOverlay'})
    if provider == 'eleven':
        if not voice.strip(): raise SpeechError('Haal je ElevenLabs-stemmen op of vul een Voice ID in.')
        audio = request('https://api.elevenlabs.io/v1/text-to-speech/'+urllib.parse.quote(voice, safe='')+'?output_format=mp3_44100_128',
                        'xi-api-key', key, {'text': text, 'model_id': profile['model'], 'voice_settings': {
                            'stability': profile['stability'], 'similarity_boost': profile['similarity'],
                            'style': profile['expressiveness'], 'speed': speed, 'use_speaker_boost': True}})
    elif provider == 'edge':
        import edge_tts
        async def fetch():
            chunks = []
            async for chunk in edge_tts.Communicate(text, voice, rate=f'{round((speed-1)*100):+d}%').stream():
                if chunk['type'] == 'audio': chunks.append(chunk['data'])
            return b''.join(chunks)
        audio = asyncio.run(asyncio.wait_for(fetch(), 40))
    else:
        raise SpeechError('Onbekende spraakprovider.')
    import miniaudio
    decoded = miniaudio.decode(audio, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=24000)
    return wav_bytes(decoded.samples.tobytes())


class Speaker:
    def __init__(self, events):
        from overlay import DATA, RESOURCE
        self.data, self.resource, self.events = DATA, RESOURCE, events
        self.settings = {'provider': 'piper', 'profiles': copy.deepcopy(DEFAULTS)}
        try:
            saved = json.loads((DATA/'voices.json').read_text('utf-8'))
            if saved.get('provider') in PROVIDERS: self.settings['provider'] = saved['provider']
            for p, values in saved.get('profiles', {}).items():
                if p in DEFAULTS:
                    self.settings['profiles'][p].update({k: v for k, v in values.items() if k in DEFAULTS[p]})
        except (OSError, ValueError, TypeError): pass
        from credentials import load_keys
        try: self.keys = load_keys()
        except (OSError, ValueError):
            self.keys = {}
            self.events.put(('speech_error', 'Opgeslagen sleutels konden niet worden geopend. Voer ze opnieuw in.'))
        self.queue = queue.Queue()
        self.generation = 0
        self.closed = False
        self.busy = False
        self.current_text = ''
        self.lock = threading.Lock()
        self.cache = {}
        threading.Thread(target=self.run, daemon=True).start()

    def save(self, settings, keys):
        self.stop()
        from credentials import save_keys
        save_keys(keys)
        self.settings, self.keys = copy.deepcopy(settings), dict(keys)
        self.data.mkdir(exist_ok=True)
        (self.data/'voices.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2), 'utf-8')

    def stop(self):
        with self.lock:
            self.generation += 1
            self.busy = False
            winsound.PlaySound(None, 0)
            self.events.put(('speech_status', (self.generation, '')))

    def say(self, text, settings=None, keys=None):
        self.stop()
        self.current_text = text
        self.busy = True
        self.queue.put((self.generation, text, copy.deepcopy(settings or self.settings), dict(self.keys if keys is None else keys)))

    def run(self):
        while not self.closed:
            try: generation, text, settings, keys = self.queue.get(timeout=.3)
            except queue.Empty: continue
            if generation != self.generation: continue
            provider = settings['provider']; profile = settings['profiles'][provider]
            try:
                # Paragraph-sized chunks preserve prosody and bound request size.
                chunks = []
                current = ''
                for sentence in re.split(r'(?<=[.!?])\s+|\n\n+', text):
                    for start in range(0, len(sentence), 1400):
                        piece = sentence[start:start+1400]
                        if len(current)+len(piece)>1400: chunks.append(current); current = ''
                        current += (' ' if current else '')+piece
                if current: chunks.append(current)
                for chunk in chunks:
                    if generation != self.generation or self.closed: break
                    digest = hashlib.sha256(json.dumps([provider, profile, chunk], sort_keys=True).encode()).hexdigest()
                    audio = self.cache.get(digest)
                    if audio is None:
                        self.events.put(('speech_status', (generation, 'Spraak laden…')))
                        audio = synthesize(provider, profile, keys.get(provider, ''), chunk, self.resource)
                        if sum(map(len, self.cache.values()))+len(audio)>40_000_000: self.cache.clear()
                        if len(audio)<=40_000_000: self.cache[digest] = audio
                    with wave.open(io.BytesIO(audio), 'rb') as source:
                        params = source.getparams(); pcm = source.readframes(source.getnframes())
                        duration = source.getnframes()/source.getframerate()
                    if params.sampwidth != 2: raise SpeechError('Onbekend audioformaat.')
                    import numpy as np
                    pcm = (np.frombuffer(pcm, dtype='<i2').astype(np.float32)*(profile['volume']/100)).clip(-32768,32767).astype('<i2').tobytes()
                    path = self.data/f'playback-{generation % 2}.wav'
                    with wave.open(str(path), 'wb') as target:
                        target.setparams(params); target.writeframes(pcm)
                    with self.lock:
                        if generation != self.generation or self.closed: break
                        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    self.events.put(('speech_status', (generation, '')))
                    deadline = time.monotonic()+duration
                    while generation == self.generation and not self.closed and time.monotonic()<deadline: time.sleep(.05)
                if generation == self.generation and not self.closed:
                    self.busy = False
                    self.events.put(('speech_status', (generation, '')))
            except Exception as exc:
                if generation == self.generation:
                    self.busy = False
                    message = str(exc) if isinstance(exc, SpeechError) else 'Spraak maken mislukt. Controleer provider, stem en verbinding.'
                    self.events.put(('speech_error', message))
