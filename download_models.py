"""Download and verify the free models. Run only during setup/build, never during play."""
from pathlib import Path
import hashlib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = [
    ('https://data.argosopentech.com/argospm/v1/translate-en_nl-1_8.argosmodel',
     'translate-en_nl.argosmodel', '91406f040619fb519e16af9c304709e9f3195297e626eec6924d62c7c981a33b'),
    ('https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/mls/medium/nl_NL-mls-medium.onnx',
     'voice/nl_NL-mls-medium.onnx', '88312e0fbf505b87caf2373d94c1384892e86b1bf2ee482cf65dc8ba179cc7d3'),
    ('https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/mls/medium/nl_NL-mls-medium.onnx.json',
     'voice/nl_NL-mls-medium.onnx.json', '6ddb215d38f1392ab935ad45441b82ada1eeae0452a2d6849ed71ea4f2e0aa63'),
]


def download(url, relative, digest):
    path = ROOT/relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return path
    temp = path.with_suffix(path.suffix+'.partial')
    print('Downloading', relative, flush=True)
    with urllib.request.urlopen(url, timeout=120) as source, temp.open('wb') as out:
        while chunk := source.read(1024*1024): out.write(chunk)
    if hashlib.sha256(temp.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Checksum mismatch: '+relative+'; the file is not used.')
    temp.replace(path)
    return path


def main():
    archive = None
    for url, relative, digest in FILES:
        path = download(url, relative, digest)
        if relative.endswith('.argosmodel'): archive = path
    allowed = {'sentencepiece.model', 'metadata.json', 'README.md', 'model/model.bin',
               'model/config.json', 'model/shared_vocabulary.json'}
    target = ROOT/'model'/'translate-en_nl-1_8'
    with zipfile.ZipFile(archive) as z:
        for name in allowed:
            destination = target/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(z.read('translate-en_nl-1_8/'+name))
    print('Models ready. Translation and speech work offline.')


if __name__ == '__main__': main()
