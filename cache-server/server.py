"""Small public-read, authenticated-write audio cache behind an HTTPS proxy."""
import hmac
import os
from pathlib import Path
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sharedcache import MAX_AUDIO, valid_audio


def make_server(address, directory, token, max_bytes=5_000_000_000):
    if len(token) < 32: raise ValueError('UPLOAD_TOKEN must contain at least 32 characters')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    used = sum(p.stat().st_size for p in directory.glob('*.wav'))

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup(); self.connection.settimeout(15)

        def log_message(self, *_): pass  # Do not log credentials or client identifiers.

        def reply(self, status, body=b'', content_type='text/plain'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers(); self.wfile.write(body)
            self.close_connection = True

        def audio_path(self):
            match = re.fullmatch(r'/v1/audio/([a-f0-9]{64})\.wav', self.path)
            return directory/(match[1]+'.wav') if match else None

        def do_GET(self):
            if self.path == '/health': return self.reply(200, b'ok')
            path = self.audio_path()
            if not path or not path.is_file(): return self.reply(404)
            self.reply(200, path.read_bytes(), 'audio/wav')

        def do_PUT(self):
            nonlocal used
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token):
                return self.reply(401)
            path = self.audio_path()
            if not path: return self.reply(404)
            try: length = int(self.headers.get('Content-Length', '0'))
            except ValueError: return self.reply(400)
            if not 0 < length <= MAX_AUDIO: return self.reply(413)
            if self.headers.get('Transfer-Encoding'): return self.reply(400)
            data = self.rfile.read(length)
            if len(data) != length or not valid_audio(data): return self.reply(415)
            with lock:
                if path.exists(): return self.reply(200)  # Immutable, first upload wins.
                if used+length > max_bytes: return self.reply(507)
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(data); temporary.replace(path)
                used += length
            self.reply(201)

    return ThreadingHTTPServer(address, Handler)


if __name__ == '__main__':
    make_server(('0.0.0.0', 8080), os.environ.get('CACHE_DIR', '/data'),
                os.environ.get('UPLOAD_TOKEN', ''),
                int(os.environ.get('CACHE_MAX_BYTES', '5000000000'))).serve_forever()
