"""Temporarily lower only JiME's audio session, never the system master volume."""
from contextlib import contextmanager
import logging


@contextmanager
def attenuate_sessions(sessions, percent):
    changed = []
    try:
        for session in sessions:
            try:
                if not session.Process or session.Process.name().lower() != 'jime.exe': continue
                volume = session.SimpleAudioVolume
                original = volume.GetMasterVolume()
                lowered = original * max(0, min(100, percent)) / 100
                volume.SetMasterVolume(lowered, None)
                changed.append((volume, original, lowered))
            except Exception:
                logging.warning('Spelvolume kon niet worden aangepast voor een audiosessie')
        yield len(changed)
    finally:
        for volume, original, lowered in changed:
            try:
                # Preserve a manual volume change made while narration was playing.
                if abs(volume.GetMasterVolume()-lowered) < .0001:
                    volume.SetMasterVolume(original, None)
            except Exception:
                logging.warning('Spelvolume kon niet worden hersteld; audiosessie mogelijk gesloten')


@contextmanager
def duck_game(enabled, percent):
    if not enabled:
        yield
        return
    initialized = False
    sessions = []
    try:
        import comtypes
        comtypes.CoInitialize(); initialized = True
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        logging.warning('Spelgeluid zachter zetten niet beschikbaar; voorlezen gaat door')
    try:
        with attenuate_sessions(sessions, percent): yield
    finally:
        sessions.clear()
        if initialized: comtypes.CoUninitialize()
