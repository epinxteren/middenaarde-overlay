"""Pack locally detected text strips for Gemini, retaining original coordinates."""
from PIL import Image
from textdetect import detect_text


def pack_text(image, boxes=None):
    boxes = detect_text(image).boxes if boxes is None else boxes
    if not boxes or len(boxes) > 150: return image, None
    crops = []
    for x0, y0, x1, y1 in sorted(boxes, key=lambda b: (b[1], b[0])):
        box = (max(0, int(x0)-6), max(0, int(y0)-6),
               min(image.width, int(x1)+7), min(image.height, int(y1)+7))
        if box[2] > box[0] and box[3] > box[1]: crops.append(box)
    if not crops: return image, None
    width = max(min(image.width, 1024), max(b[2]-b[0] for b in crops)+16)
    x, y, row_height = 8, 8, 0
    mapping = []
    for box in crops:
        w, h = box[2]-box[0], box[3]-box[1]
        if x+w+8 > width:
            x, y, row_height = 8, y+row_height+16, 0
        mapping.append(((x, y, x+w, y+h), box))
        x += w+16; row_height = max(row_height, h)
    height = y+row_height+8
    if width*height >= image.width*image.height*.9: return image, None
    packed = Image.new('RGB', (width, height), 'white')
    for tile, box in mapping: packed.paste(image.crop(box), tile[:2])
    return packed, mapping


def restore_items(items, packed_size, original_size, mapping):
    if mapping is None: return items
    restored = []
    pw, ph = packed_size; ow, oh = original_size
    for item in items:
        y0, x0, y1, x1 = item['box_2d']
        x0, x1, y0, y1 = x0*pw/1000, x1*pw/1000, y0*ph/1000, y1*ph/1000
        matches = []
        for tile, original in mapping:
            tx0, ty0, tx1, ty1 = tile
            intersection = max(0, min(x1, tx1)-max(x0, tx0))*max(0, min(y1, ty1)-max(y0, ty0))
            if intersection: matches.append((intersection, tile, original))
        if not matches: raise ValueError('OCR result outside text strips')
        _, tile, original = max(matches)
        tx0, ty0, tx1, ty1 = tile; ox0, oy0, _, _ = original
        restored.append({'text': item['text'], 'box_2d': [
            (oy0+max(y0, ty0)-ty0)*1000/oh, (ox0+max(x0, tx0)-tx0)*1000/ow,
            (oy0+min(y1, ty1)-ty0)*1000/oh, (ox0+min(x1, tx1)-tx0)*1000/ow]})
    return restored
