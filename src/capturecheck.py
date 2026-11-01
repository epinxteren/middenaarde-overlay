"""Check OCR text regions without rejecting animation elsewhere in the game."""
def text_regions_unchanged(before, after, blocks, offset=(0, 0)):
    from PIL import ImageChops, ImageFilter
    if before.size != after.size:
        return False
    for block in blocks:
        x, y = block.x - offset[0], block.y - offset[1]
        box = (max(0, int(x)), max(0, int(y)),
               min(before.width, int(x + block.width + 1)),
               min(before.height, int(y + block.height + 1)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return False
        a = before.crop(box).convert('L').filter(ImageFilter.GaussianBlur(.5))
        b = after.crop(box).convert('L').filter(ImageFilter.GaussianBlur(.5))
        histogram = ImageChops.difference(a, b).histogram()
        if sum(histogram[25:]) > a.width * a.height * .01:
            return False
    return True
