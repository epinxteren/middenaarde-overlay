"""Compare letter shapes, with start/end anchors and whole-text verification."""
import base64
import zlib
import numpy as np
import cv2


def mask(image):
    gray = np.asarray(image.convert('L'))
    _, binary = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    if binary.mean() > .5: binary = 1-binary
    return binary.astype(np.uint8)


def fingerprint(image):
    binary = mask(image)
    return {'shape':list(binary.shape), 'bits':base64.b64encode(zlib.compress(np.packbits(binary).tobytes())).decode()}


def similarity(saved, image, threshold=.98):
    shape = tuple(saved['shape'])
    actual = mask(image)
    if actual.shape != shape: return 0.
    expected = np.unpackbits(np.frombuffer(zlib.decompress(base64.b64decode(saved['bits'])),dtype=np.uint8))[:shape[0]*shape[1]].reshape(shape)
    if not expected.any(): return 0.
    # At most two pixels of rendering/capture jitter; never resize letters.
    best, aligned = 0., None
    for dy in range(-2,3):
        for dx in range(-2,3):
            moved = cv2.warpAffine(actual,np.float32([[1,0,dx],[0,1,dy]]),(shape[1],shape[0]),flags=cv2.INTER_NEAREST)
            union = np.count_nonzero(expected | moved)
            score = np.count_nonzero(expected & moved)/max(1,union)
            if score>best: best,aligned=score,moved
    if best < threshold: return best
    # Start/end letters shortlist a match, but each significant glyph also has
    # a floor: a changed digit or negation must not hide in a long paragraph.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(expected,8)
    components = sorted((s for s in stats[1:] if s[4]>=5),key=lambda s:(s[1]//12,s[0]))
    if not components: return 0.
    for component in [components[0],components[-1],*components[1:-1]]:
        x,y,w,h,area=component
        a,b=expected[y:y+h,x:x+w],aligned[y:y+h,x:x+w]
        local=np.count_nonzero(a & b)/max(1,np.count_nonzero(a | b))
        if local < max(.85,threshold-.08): return 0.
    return best
