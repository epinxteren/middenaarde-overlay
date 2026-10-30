"""Allow platform-independent checks on Linux; native Win32 checks stay Windows-only."""
import ctypes
import os
from pathlib import Path
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if sys.platform != 'win32':
    sys.modules.setdefault('winsound', types.SimpleNamespace(PlaySound=MagicMock(),
        SND_FILENAME=0x20000, SND_ASYNC=1, SND_NODEFAULT=2, SND_PURGE=64))
    with patch.object(ctypes, 'WinDLL', create=True, return_value=MagicMock()):
        import overlay
else:
    import overlay
