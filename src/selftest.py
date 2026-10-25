"""Meaningful offline integration checks; no game, microphone or network access."""
import asyncio
import json
from pathlib import Path
import wave


def run(output):
    import overlay
    from PIL import Image, ImageDraw, ImageFont
    from piper import PiperVoice, SynthesisConfig
    from PySide6.QtWidgets import QApplication, QWidget
    from ctypes import wintypes as W
    import ctypes as C
    overlay.DATA.mkdir(exist_ok=True)
    image = Image.new('RGB', (1100, 270), '#e9dfc3')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 30)
    draw.text((30, 35), 'The heroes travel through the forest.', fill='#181818', font=font)
    draw.text((30, 75), 'An enemy appears near the old bridge.', fill='#181818', font=font)
    draw.text((820, 200), 'Continue', fill='#181818', font=font)
    blocks = asyncio.run(overlay.Recognizer().read(image))
    story = next(b for b in blocks if 'forest' in b.text)
    assert 'bridge' in story.text, [b.text for b in blocks]
    assert any(b.text == 'Continue' for b in blocks)
    translator = overlay.Translator()
    nl = translator.translate(story.text)
    assert 'helden' in nl.lower() and 'bos' in nl.lower(), nl
    assert '▁' not in nl, nl
    assert translator.translate('Continue') == 'Doorgaan'
    assert translator.translate(story.text) == nl
    voice = PiperVoice.load(str(overlay.RESOURCE/'voice'/'nl_NL-mls-medium.onnx'))
    wav = Path(output).with_suffix('.wav')
    with wave.open(str(wav), 'wb') as out:
        voice.synthesize_wav('De helden reizen door het bos.', out,
                             syn_config=SynthesisConfig(speaker_id=0))
    with wave.open(str(wav), 'rb') as inp:
        frames, rate = inp.getnframes(), inp.getframerate()
        assert frames > rate
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    root.setWindowTitle('Overlay self-test')
    root.show()
    app.processEvents()
    hwnd = overlay.native_handle(root)
    assert overlay.user32.SetWindowDisplayAffinity(hwnd, 0x11), C.get_last_error()
    affinity = W.DWORD()
    overlay.user32.GetWindowDisplayAffinity.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    assert overlay.user32.GetWindowDisplayAffinity(hwnd, C.byref(affinity))
    assert affinity.value == 0x11
    from qtui import Overlay
    layer = Overlay()
    assert layer.capture_ok
    for block in blocks:
        block.translated = translator.translate(block.text)
    layer.render_blocks((0, 0, 1100, 270), blocks)
    app.processEvents()
    native_box = overlay.client_box(layer.hwnd)
    assert native_box[2]-native_box[0] == 1100 and native_box[3]-native_box[1] == 270, native_box
    style = overlay.user32.GetWindowLongW(layer.hwnd, -20)
    assert style & 0x20, 'Overlay must pass mouse input to the game'
    # Render only this generated widget, not the user's desktop or game screen.
    layer.grab().save(str(Path(output).with_suffix('.png')))
    layer.close()
    root.close()
    result = {'version': overlay.VERSION, 'ocr': [b.text for b in blocks],
              'translation': nl, 'speech_seconds': round(frames/rate, 2),
              'capture_exclusion': True, 'click_through': True, 'overlay_render': True, 'result': 'PASS'}
    Path(output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result


if __name__ == '__main__':
    import sys
    print(run(sys.argv[1] if len(sys.argv) > 1 else 'self-test.json'))
