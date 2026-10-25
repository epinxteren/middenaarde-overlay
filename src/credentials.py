"""Current-user Windows DPAPI storage. Never sync this file to GitHub."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path


class Blob(C.Structure):
    _fields_ = [('size', W.DWORD), ('data', C.POINTER(C.c_ubyte))]


def protect(data, decrypt=False):
    buffer = C.create_string_buffer(data)
    source = Blob(len(data), C.cast(buffer, C.POINTER(C.c_ubyte)))
    target = Blob()
    dll = C.WinDLL('crypt32', use_last_error=True)
    function = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    function.argtypes = [C.POINTER(Blob), C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(Blob)]
    function.restype = W.BOOL
    if not function(C.byref(source), None, None, None, None, 1, C.byref(target)):
        raise OSError('Windows kon de API-sleutels niet ontsleutelen.' if decrypt else 'Windows kon de API-sleutels niet beveiligen.')
    try: return C.string_at(target.data, target.size)
    finally:
        kernel = C.WinDLL('kernel32'); kernel.LocalFree.argtypes = [C.c_void_p]; kernel.LocalFree.restype = C.c_void_p
        kernel.LocalFree(target.data)


def credential_path():
    return Path(os.environ['LOCALAPPDATA'])/'MiddenaardeOverlay'/'credentials.dpapi'


def load_keys(path=None):
    path = path or credential_path()
    if not path.exists(): return {}
    return json.loads(protect(path.read_bytes(), decrypt=True).decode('utf-8'))


def save_keys(keys, path=None):
    path = path or credential_path()
    encrypted = protect(json.dumps({k:v for k,v in keys.items() if v}).encode('utf-8'))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encrypted)
    temporary.replace(path)
