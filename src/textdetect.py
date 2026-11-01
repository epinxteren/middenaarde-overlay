"""Fast local text-line candidate detection, without OCR or network calls."""
from dataclasses import dataclass


@dataclass
class Detection:
    lines: int
    layout: tuple


def detect_text(image):
    import cv2
    import numpy as np
    factor = min(1.0, 1280/max(image.size))
    if factor < 1:
        image = image.resize((round(image.width*factor), round(image.height*factor)))
    gray = np.asarray(image.convert('L'))
    edges = cv2.Canny(gray, 70, 160)
    # Join nearby character edges into word/line candidates; reject large illustrations.
    joined = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not (7 <= height <= 55 and width >= 35 and width/height >= 2.5): continue
        density = cv2.countNonZero(edges[y:y+height, x:x+width])/(width*height)
        if .035 <= density <= .5:
            candidates.append((x, y, width, height))
    # Nearby words at the same baseline count as one text line.
    rows = []
    for x, y, width, height in sorted(candidates, key=lambda b: (b[1], b[0])):
        center = y+height/2
        for row in rows:
            if abs(row[0]-center) <= max(4, min(height, row[1])*.4):
                row[2] = min(row[2], x); row[3] = max(row[3], x+width)
                break
        else: rows.append([center, height, x, x+width])
    layout = tuple((round(y/12), round(left/20), round(right/20)) for y, _, left, right in rows)
    return Detection(len(rows), layout)
