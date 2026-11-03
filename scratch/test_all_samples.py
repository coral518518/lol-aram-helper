import io, sys, os
sys.path.insert(0, os.path.abspath('.'))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
from PIL import Image
from main import DataManager, GameAnalyzer, get_regions

dm = DataManager()
analyzer = GameAnalyzer(dm)
regions = get_regions()

def smart_merge_ocr_lines(res_ocr):
    """
    智能水平聚类与阅读顺序拼接算法：
    1. 将纵坐标相近 (y 偏差 <= 行高的一半) 的相邻文本框聚类为同一文本行
    2. 同一行内的文本框按 x0 严格从左到右排序并拼接
    3. 各文本行按 y0 从上到下排序
    4. 过滤分类标签 (伤害、功能等)
    5. 返回拼接后最完整、最高置信度的标题
    """
    if not res_ocr:
        return ""
    
    # 提取所有文本块: (x0, y0, x1, y1, text, score)
    blocks = []
    for line in res_ocr:
        box, text, score = line
        text_clean = text.strip()
        if not text_clean:
            continue
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        blocks.append({
            'x0': min(xs),
            'y0': min(ys),
            'x1': max(xs),
            'y1': max(ys),
            'h': max(ys) - min(ys),
            'text': text_clean,
            'score': score
        })
    
    if not blocks:
        return ""

    # 计算平均字高
    avg_h = sum(b['h'] for b in blocks) / len(blocks)
    line_threshold = max(10, avg_h * 0.6)

    # 聚类为行
    # 按 y0 先粗排序
    blocks.sort(key=lambda b: b['y0'])
    lines = [] # 每个元素是一个 block 列表
    for b in blocks:
        matched_line = None
        for l in lines:
            # 如果当前 block 的中心 y 与该行平均中心 y 相差小于阈值，归为同一行
            avg_cy = sum((it['y0'] + it['y1']) / 2 for it in l) / len(l)
            b_cy = (b['y0'] + b['y1']) / 2
            if abs(b_cy - avg_cy) <= line_threshold:
                matched_line = l
                break
        if matched_line is not None:
            matched_line.append(b)
        else:
            lines.append([b])
    
    # 每一行按 x0 从左到右排序并拼接
    tag_words = {"伤害", "功能", "防御", "续航", "控制", "通用", "强化", "攻击", "法术", "坦克", "移动", "金币"}
    line_strings = []
    for l in lines:
        l.sort(key=lambda b: b['x0'])
        full_line_text = "".join(b['text'] for b in l).replace(" ", "").replace(".", "")
        if not full_line_text:
            continue
        # 过滤整行纯标签词
        is_pure_tag = all(part in tag_words for part in [full_line_text[i:i+2] for i in range(0, len(full_line_text), 2)])
        if is_pure_tag or full_line_text in tag_words:
            continue
        # 剥离标签后缀
        for tag in tag_words:
            if full_line_text.endswith(tag) and len(full_line_text) > len(tag):
                full_line_text = full_line_text[:-len(tag)]
        line_strings.append(full_line_text)
    
    if not line_strings:
        # 兜底：全部按左到右拼接
        blocks.sort(key=lambda b: (b['y0'] // 20, b['x0']))
        return "".join(b['text'] for b in blocks).replace(" ", "").replace(".", "")
    
    return line_strings[0]

print("=== 测试所有 5 张真实游戏截图 ===")
for img_name in ['1.png', '2.png', '3.png', '4.png', '5.png']:
    im = Image.open(img_name)
    print(f"\n>>>> 截图: {img_name}")
    for key, reg in regions.items():
        crop_box = (reg['left'], reg['top'], reg['left'] + reg['width'], reg['top'] + reg['height'])
        cropped = im.crop(crop_box)
        res_ocr, _ = analyzer.ocr(np.array(cropped))
        
        old_title = analyzer._extract_title_from_ocr(res_ocr)
        old_match, old_score = dm.match_augment_name(old_title)
        
        new_title = smart_merge_ocr_lines(res_ocr)
        new_match, new_score = dm.match_augment_name(new_title)
        
        print(f"  [{key}] Raw: {[line[1] for line in res_ocr]}")
        print(f"        旧版: 提取='{old_title}' -> 匹配='{old_match}' ({old_score})")
        print(f"        新版: 提取='{new_title}' -> 匹配='{new_match}' ({new_score})")

