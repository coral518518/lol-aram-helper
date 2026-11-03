import io, sys, os
sys.path.insert(0, os.path.abspath('.'))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image
from main import DataManager, GameAnalyzer, get_regions

dm = DataManager()
analyzer = GameAnalyzer(dm)

# 加载 1.png
im = Image.open('1.png')
W, H = im.size
print(f"Image size: {W}x{H}")

regions = get_regions()
images = {}
for key, reg in regions.items():
    crop_box = (
        reg['left'],
        reg['top'],
        reg['left'] + reg['width'],
        reg['top'] + reg['height']
    )
    cropped = im.crop(crop_box)
    images[key] = np.array(cropped)
    cropped.save(f"scratch/crop_{key}.png")

print(f"Regions: {regions}")

# 对这三个区域执行 OCR 和匹配
for key, img in images.items():
    res_ocr, _ = analyzer.ocr(img)
    print(f"\n--- Region {key} ---")
    print(f"Raw OCR: {res_ocr}")
    extracted = analyzer._extract_title_from_ocr(res_ocr)
    print(f"Extracted title: {repr(extracted)}")
    match_name, score = dm.match_augment_name(extracted)
    print(f"Matched name: {repr(match_name)}, score: {score}")
