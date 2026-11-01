"""Optional public audio cache. Credentials and source text never leave via metadata."""
import hashlib
import io
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

MAX_AUDIO = 20_000_000


def audio_key(provider, profile, text):
    # Volume is applied locally, after synthesis; all synthesis settings remain in the key.
    settings = {k: v for k, v in profile.items() if k != 'volume'}
    payload = ['audio-v1', provider, settings, ' '.join(text.split())]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def valid_audio(data):
    if not data or len(data) > MAX_AUDIO: return False
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            return (audio.getsampwidth() == 2 and audio.getnchannels() in (1, 2)
                    and 8000 <= audio.getframerate() <= 96000
                    and 0 < audio.getnframes() / audio.getframerate() <= 300
                    and len(audio.readframes(audio.getnframes())) == audio.getnframes()*audio.getnchannels()*2)
    except (ValueError, EOFError, wave.Error): return False


def server_url(value):
    value = value.strip().rstrip('/')
    if not value: return ''
    parsed = urllib.parse.urlsplit(value)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')) or not parsed.hostname:
        raise ValueError('Gebruik HTTPS (HTTP is alleen voor localhost-tests toegestaan).')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Gebruik een serveradres zonder wachtwoord, query of fragment.')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class SharedAudioCache:
    def __init__(self, events):
        self.events = events
        self.config = (True, '', '')
        self.cooldown = 0
        self.uploads = queue.Queue(maxsize=4)
        self.opener = urllib.request.build_opener(NoRedirect())
        threading.Thread(target=self._upload_loop, daemon=True).start()

    def configure(self, enabled=True, url='', token=''):
        self.config = (bool(enabled), server_url(url), token.strip())
        self.cooldown = 0

    def note(self, message):
        logging.info('Audiocache: %s', message)
        self.events.put(('diagnostic', time.strftime('%H:%M:%S')+'  Audiocache: '+message))

    def get(self, key):
        enabled, url, _ = self.config
        if not enabled or not url or time.monotonic() < self.cooldown: return None
        try:
            with self.opener.open(url+'/v1/audio/'+key+'.wav', timeout=2) as response:
                data = response.read(MAX_AUDIO+1)
            if not valid_audio(data): raise ValueError('Invalid audio')
            self.note('gedeelde opname gevonden')
            return data
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.note('niet gevonden; provider maakt nieuwe audio')
                return None
        except Exception:
            pass
        self.cooldown = time.monotonic()+30
        self.note('server niet beschikbaar of ongeldig antwoord; provider blijft werken')
        return None

    def put(self, key, data):
        enabled, url, token = self.config
        if not enabled or not url or not token or not valid_audio(data): return
        try: self.uploads.put_nowait((self.config, key, data))
        except queue.Full: self.note('uploadwachtrij vol; deze opname wordt overgeslagen')

    def _upload_loop(self):
        while True:
            config, key, data = self.uploads.get()
            try:
                if config != self.config or not self.config[0]: continue
                _, url, token = config
                req = urllib.request.Request(url+'/v1/audio/'+key+'.wav', data=data, method='PUT',
                    headers={'Authorization': 'Bearer '+token, 'Content-Type': 'audio/wav'})
                with self.opener.open(req, timeout=8) as response: response.read(1024)
                self.note('opname gedeeld')
            except Exception:
                self.note('upload mislukt; voorlezen gaat door')
            finally: self.uploads.task_done()
