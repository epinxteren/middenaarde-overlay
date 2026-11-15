"""Bundled CPU OCR; no Windows language pack or runtime model downloads."""
from types import SimpleNamespace
import re


def restore_spaces(original, candidate):
    """Accept only added word boundaries, never changed letters or punctuation."""
    original, candidate = original.strip(), candidate.strip()
    if ''.join(original.split()) != ''.join(candidate.split()):
        return original
    def boundaries(text):
        positions, offset = set(), 0
        for char in text:
            if char.isspace(): positions.add(offset)
            else: offset += 1
        return positions
    old, new = boundaries(original), boundaries(candidate)
    return candidate if old < new else original


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
            # Whole-page recognition can lose spaces in short italic lines.
            # Re-read only suspicious lines with their surrounding pixels intact.
            if re.search(r'[A-Za-z]{12,}', text):
                left, top = max(0, int(min(xs))-4), max(0, int(min(ys))-4)
                right = min(pixels.shape[1], int(max(xs))+5)
                bottom = min(pixels.shape[0], int(max(ys))+5)
                if right > left and bottom > top:
                    retry, _ = self.engine(pixels[top:bottom, left:right].copy(),
                                           use_det=False, use_cls=False)
                    if retry and retry[0][1] >= .9:
                        text = restore_spaces(text, retry[0][0])
            x, y = min(xs)/factor, min(ys)/factor
            rect = SimpleNamespace(x=x, y=y, width=(max(xs)-min(xs))/factor,
                                   height=(max(ys)-min(ys))/factor)
            lines.append(SimpleNamespace(words=[SimpleNamespace(text=text, bounding_rect=rect)]))
        blocks = group_lines(lines)
        for block in blocks:
            block.text = re.sub(r'([.!?])(?=[A-Za-zÀ-ÿ])', r'\1 ', block.text)
        return blocks
