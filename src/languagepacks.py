"""Optional, hash-verified Argos packages installed only on explicit user request."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

# Pinned official Argos artifacts, including the legacy package directory names.
PACKAGES = {
    'nl': ('translate-en_nl-1_8', 'translate-en_nl-1_8', 71504559,
           '91406f040619fb519e16af9c304709e9f3195297e626eec6924d62c7c981a33b'),
    'de': ('translate-en_de-1_3', 'translate-en_de-1_3', 150508297,
           '6cd847f0c06c9c66013e6b0932e07fd54a6d90894659c02bf6c5247b72fb25b1'),
    'fr': ('translate-en_fr-1_9', 'translate-en_fr-1_9', 65472327,
           '3a65ed83364f4e7b06e30f9dd823db1934899ed3ce839e63f46dc7b09dc797b4'),
    'es': ('translate-en_es-1_0', 'en_es', 87503191,
           'd698d0ef87ad70d5d184b7fa6965905bf4368f09a2bb9ffb165a79bac96af0c4'),
    'it': ('translate-en_it-1_0', 'en_it', 87660780,
           'dde2180001a47904ecbbd688a41e35db8a040e4fd5b52e4f29b4bb499516ab32'),
    'pt': ('translate-en_pt-1_9', 'translate-en_pt-1_9', 66179184,
           '0c5350a2fa5b923de1346edc0d42e08e38bab2e33cede3a0b9a48eb4281ad8a9'),
}
BASE_URL = 'https://data.argosopentech.com/argospm/v1/'
FILES = ('sentencepiece.model', 'metadata.json', 'README.md', 'model/model.bin',
         'model/config.json', 'model/shared_vocabulary.json', 'model/shared_vocabulary.txt')


def complete(folder):
    return (all((folder / name).is_file() for name in ('sentencepiece.model', 'model/model.bin'))
            and any((folder / name).is_file() for name in
                    ('model/shared_vocabulary.json', 'model/shared_vocabulary.txt')))


def model_folder(language, resource, data):
    name = PACKAGES[language][0]
    for folder in (Path(resource) / 'model' / name, Path(data) / 'language-models' / name):
        if complete(folder): return folder
    raise FileNotFoundError('Taalpakket ontbreekt. Gebruik Download ontbrekende taalpakketten.')


def model_available(language, resource, data):
    if language == 'en': return True
    try:
        model_folder(language, resource, data)
        return True
    except FileNotFoundError:
        return False


def install_language(language, resource, data):
    if model_available(language, resource, data): return
    name, prefix, expected_size, expected_hash = PACKAGES[language]
    root = Path(data) / 'language-models'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as temporary:
        staging = Path(temporary)
        archive = staging / 'package.zip'
        digest, size = hashlib.sha256(), 0
        request = urllib.request.Request(BASE_URL + name + '.argosmodel',
                                         headers={'User-Agent': 'MiddenaardeOverlay'})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > expected_size: raise ValueError('Taalpakket is groter dan verwacht.')
                digest.update(chunk)
                output.write(chunk)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError('Controle van taalpakket mislukt; download is niet gebruikt.')
        folder = staging / name
        with zipfile.ZipFile(archive) as package:
            metadata = json.loads(package.read(prefix + '/metadata.json'))
            if metadata['from_code'] != 'en' or metadata['to_code'] != language:
                raise ValueError('Taalpakket bevat een andere taal.')
            # Never extract arbitrary archive paths or the unused Stanza runtime.
            for relative in FILES:
                member = prefix + '/' + relative
                if member not in package.namelist(): continue
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
        if not complete(folder): raise ValueError('Taalpakket is onvolledig.')
        destination = root / name
        if destination.exists(): shutil.rmtree(destination)
        folder.replace(destination)
