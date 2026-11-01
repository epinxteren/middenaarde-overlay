"""Bundled CPU OCR; no Windows language pack or runtime model downloads."""
from types import SimpleNamespace
import re


class PortableRecognizer:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1)

    async def read(self, image):
        import numpy as np
        from overlay import group_lines
        factor = min(1.0, 2500 / max(image.size))
        if factor < 1:
            image = image.resize((int(image.width*factor), int(image.height*factor)))
        # RapidOCR expects OpenCV's BGR ordering for ndarray input.
        pixels = np.asarray(image.convert('RGB'))[:, :, ::-1].copy()
        result, _timing = self.engine(pixels)
        lines = []
        for polygon, text, confidence in result or []:
            if confidence < .5 or not text.strip():
                continue
            xs, ys = zip(*polygon)
            x, y = min(xs)/factor, min(ys)/factor
            rect = SimpleNamespace(x=x, y=y, width=(max(xs)-min(xs))/factor,
                                   height=(max(ys)-min(ys))/factor)
            lines.append(SimpleNamespace(words=[SimpleNamespace(text=text, bounding_rect=rect)]))
        blocks = group_lines(lines)
        for block in blocks:
            block.text = re.sub(r'([.!?])(?=[A-Za-zÀ-ÿ])', r'\1 ', block.text)
        return blocks
