with open('main.py', 'r', encoding='utf-8') as f:
    c = f.read()

target = """    def _extract_title_from_ocr(self, res_ocr):
        \"\"\"
        智能解析 OCR 识别出来的多行结果：
        1. 按 y0 纵坐标排序
        2. 过滤分类标签 (伤害、功能、防御等)
        3. 剥离标题末尾误粘连的标签词
        4. 优先选出顶部置信度最高的主标题行
        \"\"\"
        if not res_ocr:
            return ""
        
        tag_words = {"伤害", "功能", "防御", "续航", "控制", "通用", "强化", "攻击", "法术", "坦克", "移动", "金币"}
        sorted_lines = sorted(res_ocr, key=lambda l: l[0][0][1])
        title_candidates = []
        
        for line in sorted_lines:
            box, text, score = line
            text_clean = text.strip().replace(" ", "").replace(".", "")
            if not text_clean:
                continue
            
            # 检查是否整行纯粹由标签词组成
            is_pure_tag = all(part in tag_words for part in [text_clean[i:i+2] for i in range(0, len(text_clean), 2)])
            if is_pure_tag or text_clean in tag_words:
                continue
            
            # 检查是否包含标签后缀（例如"虚幻武器伤害"）并剔除
            for tag in tag_words:
                if text_clean.endswith(tag) and len(text_clean) > len(tag):
                    text_clean = text_clean[:-len(tag)]
            
            title_candidates.append(text_clean)

        if not title_candidates:
            return "".join([l[1] for l in res_ocr]).replace(" ", "").replace(".", "")

        return title_candidates[0]"""

replacement = """    def _extract_title_from_ocr(self, res_ocr):
        \"\"\"
        智能解析 OCR 识别结果：
        1. 针对同一水平行被标点/空格拆碎的文字框（如 ['升级：', '耀光']），基于纵坐标聚类并按 x0 从左到右严格拼接
        2. 各文本行按 y0 从上到下排序
        3. 过滤纯分类标签行（伤害、功能、防御等）
        4. 剥离误粘连在标题末尾的标签词
        5. 选出主标题行进行全量匹配
        \"\"\"
        if not res_ocr:
            return ""

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

        # 计算平均字高与行聚类阈值
        avg_h = sum(b['h'] for b in blocks) / len(blocks)
        line_threshold = max(10, avg_h * 0.6)

        # 聚类为水平文本行
        blocks.sort(key=lambda b: b['y0'])
        lines = []
        for b in blocks:
            matched_line = None
            b_cy = (b['y0'] + b['y1']) / 2
            for l in lines:
                avg_cy = sum((it['y0'] + it['y1']) / 2 for it in l) / len(l)
                if abs(b_cy - avg_cy) <= line_threshold:
                    matched_line = l
                    break
            if matched_line is not None:
                matched_line.append(b)
            else:
                lines.append([b])

        # 针对每一行，按 x0 从左到右排序并无缝拼合
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
            blocks.sort(key=lambda b: (b['y0'] // 20, b['x0']))
            return "".join(b['text'] for b in blocks).replace(" ", "").replace(".", "")

        return line_strings[0]"""

assert target in c, "target block not found in main.py"
c = c.replace(target, replacement, 1)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(c)

print("main.py updated successfully with horizontal line clustering algorithm!")

