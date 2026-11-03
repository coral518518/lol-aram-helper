import io, sys, os
sys.path.insert(0, os.path.abspath('.'))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from PIL import Image
import numpy as np
from main import DataManager, GameAnalyzer, get_regions

dm = DataManager()
analyzer = GameAnalyzer(dm)
im = Image.open('1.png')
regions = get_regions()

print("--- 验证 1.png 核心卡牌识别 ---")
for k, reg in regions.items():
    cropped = im.crop((reg['left'], reg['top'], reg['left']+reg['width'], reg['top']+reg['height']))
    res = analyzer._ocr_and_match(k, np.array(cropped), '暗裔剑魔')
    t = res.get('text', '').replace('\n', ' ')
    print(f"{k}: valid={res.get('valid')}, tier={res.get('tier')}, overall_rank={res.get('overall_rank')}, display={t}")

