import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import cv2
from rapidocr_onnxruntime import RapidOCR
from PIL import Image

ocr = RapidOCR()
for img_name in ['1.png', '2.png', '3.png', '4.png', '5.png']:
    try:
        im = Image.open(img_name)
        print(f"\n=== {img_name} ({im.size}) ===")
        # 裁剪一下标题区域或者全图识别
        res, _ = ocr(img_name)
        if res:
            for line in res:
                box, txt, score = line
                print(f"  [{score:.2f}] {txt} (box: {box[0]})")
        else:
            print("  (No text found)")
    except Exception as e:
        print(f"Error reading {img_name}: {e}")

