from pathlib import Path
import importlib.metadata
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files, collect_dynamic_libs

root = Path(SPECPATH)
datas = []
binaries = []
hiddenimports = []
for package in ['ctranslate2', 'sentencepiece', 'winrt', 'piper', 'edge_tts', 'miniaudio', 'certifi']:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files('onnxruntime')
binaries += collect_dynamic_libs('onnxruntime')
hiddenimports += ['onnxruntime', 'onnxruntime.capi.onnxruntime_pybind11_state', 'selftest', 'qtui', 'speech', 'voiceui', '_cffi_backend']
datas += [(str(root/'model'/'translate-en_nl-1_8'/'model'), 'model/translate-en_nl-1_8/model'),
          (str(root/'model'/'translate-en_nl-1_8'/'sentencepiece.model'), 'model/translate-en_nl-1_8'),
          (str(root/'voice'/'nl_NL-mls-medium.onnx'), 'voice'),
          (str(root/'voice'/'nl_NL-mls-medium.onnx.json'), 'voice')]
for package in ['piper-tts', 'PySide6-Essentials', 'shiboken6', 'ctranslate2', 'sentencepiece',
                'onnxruntime', 'numpy', 'Pillow', 'winrt-runtime', 'PyYAML', 'protobuf',
                'edge-tts', 'miniaudio', 'aiohttp', 'certifi', 'cffi', 'pycparser', 'tabulate', 'aiohappyeyeballs', 'aiosignal', 'attrs', 'frozenlist', 'multidict', 'propcache', 'yarl', 'idna', 'flatbuffers', 'pathvalidate', 'packaging', 'typing_extensions', 'pyinstaller']:
    distribution = importlib.metadata.distribution(package)
    for file in distribution.files or []:
        if '/licenses/' in str(file).replace('\\', '/') or Path(file).name.lower() in ['license', 'license.txt', 'copying']:
            source = Path(distribution.locate_file(file))
            if source.is_file():
                datas.append((str(source), 'licenses/'+package+'/'+str(Path(file).parent).replace('..', '_')))
python_license = Path(sys.base_prefix)/'LICENSE.txt'
if python_license.is_file(): datas.append((str(python_license), 'licenses/CPython'))

a = Analysis([str(root/'src'/'launcher.py')], pathex=[str(root/'src')],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=['tkinter', 'torch', 'tensorflow', 'transformers', 'matplotlib',
                       'win32com', 'pythoncom', 'PySide6.QtWebEngineCore', 'PySide6.QtQml'],
             noarchive=False)
# Qt uses Windows' unversioned ICU ABI. Never collect an unrelated Conda/Poppler
# icuuc.dll found on the build machine PATH: its exports use versioned suffixes.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() != 'icuuc.dll']
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MiddenaardeOverlay',
          console=False, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MiddenaardeOverlay')
