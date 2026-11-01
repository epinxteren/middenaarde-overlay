"""Repeatable offline desktop pipeline test. Run with the app's Python dependencies.

Real Qt overlay, local OCR and translation; controlled slow AI and speech responses.
No credentials or external services. Writes simulator-report.json and screenshots.
"""
import sys
import faulthandler
faulthandler.enable()
faulthandler.dump_traceback_later(35, repeat=True)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import json
import logging
import tempfile
import threading
import time
from contextlib import ExitStack
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import QTimer, Qt, QPoint
from PySide6.QtTest import QTest
import overlay
import qtui
import speech


def main():
    output = Path(sys.argv[1] if len(sys.argv)>1 else 'simulator-results').resolve()
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    dummy = QLabel()
    dummy.setWindowTitle('Middle-earth — lokaal testspel')
    dummy.setGeometry(50, 50, 1000, 480)
    state = {'page':0, 'phase':'ready', 'since':time.monotonic(), 'checks':[], 'error':None, 'ai_calls':0}
    font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 27)
    def scene(page, animation=False):
        frame = Image.new('RGB', (1000,480), '#231f19')
        draw = ImageDraw.Draw(frame)
        draw.rectangle((30,30,970,360), fill='#eee1bb')
        lines = (['The heroes travel through the forest.',
                  'An enemy appears near the old bridge.',
                  'The brave heroes must protect the village.'] if page == 0 else
                 ['The heroes enter the ancient tower.',
                  'They discover a hidden door behind the stairs.',
                  'Use deduction to solve the mystery.'])
        for i,line in enumerate(lines): draw.text((65,70+i*38),line,font=font,fill='#181818')
        if animation: draw.rectangle((50,400,900,450), fill='#41694b')
        state['frame'] = frame
        data = frame.tobytes()
        dummy.setPixmap(QPixmap.fromImage(QImage(data,1000,480,3000,QImage.Format.Format_RGB888).copy()))
    scene(0); dummy.show()
    hwnd = overlay.native_handle(dummy)
    def slow_ai(key, model, blocks, instruction, out, narration, history=()):
        state['ai_calls'] += 1
        time.sleep(2)
        return {'explanation':'Offline testsimulatie', 'items':[
            {'id':str(i),'translation':b.translated,'narration':b.narrated} for i,b in enumerate(blocks)]}
    def slow_audio(*args):
        time.sleep(2)
        return speech.wav_bytes(b'\0'*(24000*2*12))
    with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
        data = Path(folder)
        for target, value in [('qtui.DATA',data),('overlay.DATA',data)]: stack.enter_context(patch(target,value))
        stack.enter_context(patch('credentials.load_keys',return_value={}))
        stack.enter_context(patch('qtui.find_game',return_value=hwnd))
        stack.enter_context(patch('qtui.client_box',return_value=(50,50,1050,530)))
        stack.enter_context(patch('qtui.active_game',return_value=True))
        stack.enter_context(patch('qtui.ImageGrab.grab',side_effect=lambda **_:state['frame'].copy()))
        stack.enter_context(patch('translationhelp.review',side_effect=slow_ai))
        stack.enter_context(patch('speech.synthesize',side_effect=slow_audio))
        stack.enter_context(patch('speech.winsound.PlaySound'))
        with patch.object(qtui.App,'worker'):
            app = qtui.App()
        app.read_aloud.setChecked(False)
        app.auto_review_box.setChecked(True)
        app.click_to_speak.setChecked(True)
        worker = threading.Thread(target=app.worker,daemon=True); worker.start()
        start = time.monotonic()
        def check(condition, message):
            if not condition: raise AssertionError(message)
        def advance(phase):
            state['phase'],state['since'] = phase,time.monotonic()
        def tick():
            try:
                check(time.monotonic()-start < 100, 'Simulator timeout: '+state['phase'])
                elapsed = time.monotonic()-state['since']
                if state['phase']=='ready' and app.ready:
                    app.toggle(); advance('first')
                elif state['phase']=='first' and app.overlay.blocks:
                    app.overlay.repaint(); application.processEvents()
                    state['blocks']=app.overlay.blocks
                    app.overlay.grab().save(str(output/'01-translated.png'))
                    rect,text=app.overlay.click_regions[0]
                    QTest.mouseClick(app.overlay,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,
                                     QPoint(rect.center().x(),rect.center().y()))
                    check(app.speaker.busy,'Overlay click did not start speech')
                    state['generation']=app.speaker.generation
                    scene(0,True); advance('animation')
                elif state['phase']=='animation':
                    check(app.overlay.blocks is state['blocks'],'Animation erased/replaced the translation')
                    check(app.speaker.generation==state['generation'],'Animation cancelled pending speech')
                    if elapsed>4:
                        state['checks'].append('Click + delayed speech + background animation preserve translation and speech')
                        scene(1); advance('newpage')
                elif state['phase']=='newpage':
                    if not app.overlay.blocks:
                        if not state.get('cleared_new_page'):
                            state['clear_seconds'] = round(elapsed,2)
                        state['cleared_new_page']=True
                    elif app.overlay.blocks is not state['blocks']:
                        check('tower' in ' '.join(b.text for b in app.overlay.blocks),'Wrong replacement page')
                        check(state.get('cleared_new_page'), 'Old page was not cleared before replacement')
                        state['checks'].append('Scenario change clears old text before slow AI; unchanged-page animation preserves narration')
                        app.overlay.grab().save(str(output/'02-next-page.png'))
                        scene(0); advance('return')
                elif state['phase']=='return' and 'forest' in ' '.join(b.text for b in app.overlay.blocks):
                    check(state['ai_calls']==2,'Returning to page repeated the AI request')
                    state['checks'].append('Returning to original page reuses persisted AI result')
                    state['phase']='done'
                    timer.stop(); application.quit()
            except Exception as exc:
                state['error']=str(exc); timer.stop(); application.quit()
        timer=QTimer(); timer.timeout.connect(tick); timer.start(100)
        application.exec()
        app.closing.set(); app.enabled.clear(); worker.join(timeout=10)
        app.speaker.close(); app.timer.stop()
        application.removeNativeEventFilter(app.hotkey_filter)
        app.overlay.close(); dummy.close(); app.root.close()
        report={'result':'FAIL' if state['error'] else 'PASS','checks':state['checks'],
                'error':state['error'],'ai_requests':state['ai_calls'],
                'scenario_clear_seconds':state.get('clear_seconds'),
                'real_components':['Qt overlay and click handler','local OCR','local translation','speech worker','disk caches'],
                'simulated_components':['Gemini response (2 seconds)','speech generation (2 seconds)','audio device','game frames/focus']}
        (output/'simulator-report.json').write_text(json.dumps(report,indent=2), 'utf-8')
        print(json.dumps(report,indent=2))
        logging.shutdown()
        faulthandler.cancel_dump_traceback_later()
        return 1 if state['error'] else 0


if __name__=='__main__': sys.exit(main())
