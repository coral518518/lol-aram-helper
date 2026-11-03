# 限制底层数学库并发线程数，避免 ONNX / OpenBLAS 吃满多核影响游戏帧率
import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import time
import json
import csv
import sys
import re
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
import threading
import queue
import tkinter as tk
import ctypes
import msvcrt  # 用于清除输入缓冲区
import numpy as np
from PIL import Image
import mss
import keyboard
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from thefuzz import process, fuzz
from scripts.config import BASE_DIR, DATA_DIR, ALL_AUGMENTS_FILE
from scripts.lcu_connector import LCUConnector

# 确保 Windows 下 PyInstaller 冻结环境优先检索 C++ 运行时及 ONNX Runtime 原生 DLL
if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    internal_dir = os.path.join(exe_dir, '_internal')
    if os.path.exists(internal_dir):
        try:
            os.add_dll_directory(internal_dir)
        except Exception:
            pass
        capi_dir = os.path.join(internal_dir, 'onnxruntime', 'capi')
        if os.path.exists(capi_dir):
            try:
                os.add_dll_directory(capi_dir)
            except Exception:
                pass
    try:
        sys32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
        if os.path.exists(sys32):
            os.add_dll_directory(sys32)
    except Exception:
        pass

RapidOCR = None
cv2 = None
HAS_CV2 = False

def _ensure_ocr_modules():
    """按需延迟加载 RapidOCR 与 OpenCV，彻底杜绝数据加载或轻量启动时的 C++ CRT 崩溃"""
    global RapidOCR, cv2, HAS_CV2
    if cv2 is None and not HAS_CV2:
        try:
            import cv2 as _cv2
            cv2 = _cv2
            HAS_CV2 = True
        except ImportError:
            HAS_CV2 = False
    if RapidOCR is None:
        from rapidocr_onnxruntime import RapidOCR as _RapidOCR
        RapidOCR = _RapidOCR


def set_low_priority():
    """将当前进程优先级设为低于普通 (BELOW_NORMAL)，确保 LOL 游戏渲染独占 CPU 核心"""
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        try:
            # 0x00004000 为 BELOW_NORMAL_PRIORITY_CLASS
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        except Exception:
            pass

# ================= 配置与常量 =================

_CACHED_REGIONS = None

def get_regions():
    """根据当前屏幕分辨率动态计算海克斯文字截取区域 (垂直居中完整包裹卡片标题，避开底部标签)"""
    global _CACHED_REGIONS
    if _CACHED_REGIONS is None:
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                W, H = mon['width'], mon['height']
        except Exception:
            W, H = 1920, 1080  # 默认 1080p 安全回退
        _CACHED_REGIONS = {
            "hex_1": {'top': int(H * 0.380), 'left': int(W * 0.250), 'width': int(W * 0.132), 'height': int(H * 0.046)},
            "hex_2": {'top': int(H * 0.380), 'left': int(W * 0.437), 'width': int(W * 0.132), 'height': int(H * 0.046)},
            "hex_3": {'top': int(H * 0.380), 'left': int(W * 0.621), 'width': int(W * 0.132), 'height': int(H * 0.046)},
        }
    return _CACHED_REGIONS

REGIONS = get_regions()

_CACHED_BANNER_LAYOUT = None

def get_banner_layout():
    """计算紧凑型浮窗几何参数 (仅覆盖三张海克斯卡牌上方区域，面积缩减 96% 以上，从根本上杜绝 DWM 全屏合成掉帧)"""
    global _CACHED_BANNER_LAYOUT
    if _CACHED_BANNER_LAYOUT is None:
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                W, H = mon['width'], mon['height']
                off_x, off_y = mon['left'], mon['top']
        except Exception:
            W, H, off_x, off_y = 1920, 1080, 0, 0

        regs = get_regions()
        min_left = max(0, int(regs['hex_1']['left'] - W * 0.015))
        max_right = min(W, int(regs['hex_3']['left'] + regs['hex_3']['width'] + W * 0.025))
        banner_w = max_right - min_left
        banner_h = max(75, int(H * 0.08))  # 约 80~110px 高度，足够显示推荐
        banner_y = max(0, int(regs['hex_1']['top'] - banner_h - 15))

        _CACHED_BANNER_LAYOUT = {
            "x": off_x + min_left,
            "y": off_y + banner_y,
            "width": banner_w,
            "height": banner_h,
            "min_left": min_left,
            "off_x": off_x,
            "off_y": off_y,
        }
    return _CACHED_BANNER_LAYOUT

BANNER_LAYOUT = get_banner_layout()

COLORS = {
    "normal": "#00FF00",  # 绿色
    "best":   "#FFD700",  # 金色
    "status": "yellow",   # 黄色
    "error":  "#FF3333",  # 红色
    "bg":     "#000000"   # 背景黑
}

# ================= 1. 数据管理 (Model) =================

class DataManager:
    """负责加载和管理静态数据"""
    def __init__(self):
        self.hero_data = {}
        self.all_augments = {}          # 全量海克斯真理库: {std_name: {tier: ...}}
        self.normalized_aug_map = {}    # 规范化名称 -> 标准名称
        self.augments_by_len = defaultdict(list) # 长度索引
        # 拼音映射改为 defaultdict(list)，支持一个拼音对应多个英雄
        self.pinyin_map = defaultdict(list)

        self.base_dir = BASE_DIR
        self.data_dir = DATA_DIR
        self._load_data()

    def _load_data(self):
        print("--- 正在加载数据资源 ---")



        # 2. 加载英雄数据 (CSV - 带多级路径回退保证)
        csv_path = os.path.join(self.data_dir, 'hero_augments.csv')
        if not os.path.exists(csv_path):
            alt = os.path.join(self.base_dir, 'data', 'hero_augments.csv')
            if os.path.exists(alt):
                csv_path = alt
            else:
                alt2 = os.path.join(os.getcwd(), 'data', 'hero_augments.csv')
                if os.path.exists(alt2):
                    csv_path = alt2
        if not os.path.exists(csv_path):
            print(f"❌ 错误: 找不到文件 {csv_path}")
            print(f"   请确认该文件位于: {self.data_dir}")
        else:
            try:
                encoding = 'utf-8-sig'
                
                raw_hero_list = defaultdict(list)
                with open(csv_path, 'r', encoding=encoding) as f:
                    reader = csv.reader(f)
                    header = next(reader, None) # 跳过表头
                    is_new_format = header and "等级" in header
                    has_overall_rank = header and "总排名" in header
                    
                    for row in reader:
                        if not row: continue
                        hero = row[0].strip()
                        
                        if has_overall_rank and len(row) >= 6:
                            # 最新格式: 中文名,英文名,等级,总排名,等级内序号,海克斯名称
                            tier = row[2].strip()
                            try: overall_rank = int(row[3])
                            except (ValueError, IndexError): overall_rank = 999
                            try: t_rank = int(row[4])
                            except (ValueError, IndexError): t_rank = 999
                            name = row[5].strip()
                            
                            if hero not in self.hero_data: self.hero_data[hero] = {}
                            self.hero_data[hero][name] = {
                                "tier": tier,
                                "overall_rank": overall_rank,
                                "t_rank": t_rank
                            }
                        elif is_new_format and len(row) >= 5:
                            # 旧新格式: 中文名,英文名,等级,等级内序号,海克斯名称 (无总排名)
                            tier = row[2].strip()
                            try: t_rank = int(row[3])
                            except (ValueError, IndexError): t_rank = 999
                            name = row[4].strip()
                            
                            if hero not in self.hero_data: self.hero_data[hero] = {}
                            self.hero_data[hero][name] = {
                                "tier": tier,
                                "overall_rank": 999,
                                "t_rank": t_rank
                            }
                        elif not is_new_format and len(row) >= 4:
                            try: rank = int(row[2])
                            except (ValueError, IndexError): rank = 999
                            aug = row[3].strip()
                            raw_hero_list[hero].append((rank, aug))
                
                # 如果存在旧格式的数据，走旧的合并逻辑
                if raw_hero_list:
                    for hero, aug_list in raw_hero_list.items():
                        if hero in self.hero_data: continue # 跳过已被新格式处理的
                        aug_list.sort(key=lambda x: x[0])
                        counters = {"白银": 1, "黄金": 1, "棱彩": 1, "未知": 1}
                        h_dict = {}
                        for rank, name in aug_list:
                            tier = "未知"
                            h_dict[name] = {
                                "tier": tier, 
                                "overall_rank": 999,
                                "t_rank": counters.get(tier, 1)
                            }
                            if tier in counters: counters[tier] += 1
                        self.hero_data[hero] = h_dict
                
                print(f"✅ 英雄数据加载完毕: 共 {len(self.hero_data)} 个英雄")
            except Exception as e:
                print(f"❌ CSV 读取严重失败: {e}")

        # 3. 加载拼音映射 (构建一对多关系)
        pinyin_file = os.path.join(self.data_dir, 'pinyin_map.json')
        if os.path.exists(pinyin_file):
            try:
                with open(pinyin_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for cn, py in data.items():
                        if cn not in self.pinyin_map[py]:
                            self.pinyin_map[py].append(cn)
                        if cn not in self.pinyin_map[cn]:
                            self.pinyin_map[cn].append(cn)
            except Exception as e:
                print(f"⚠️ {pinyin_file} 加载异常: {e}")
        
        # 4. 加载全量海克斯真理库 (用于零遗漏纠错与兜底)
        self._load_all_augments()

        print("-> 数据初始化完成")

    def search_hero(self, query):
        """
        英雄搜索逻辑 (增强模糊匹配)
        返回: (匹配列表, 是否精确匹配)
        """
        query = query.strip().lower()
        
        # 1. 尝试拼音/中文直接匹配 (O(1))，返回的是一个列表
        if query in self.pinyin_map:
            return self.pinyin_map[query], True
        
        # 2. 如果没找到，在数据Key中模糊搜索
        if self.hero_data:
            result = process.extractOne(query, list(self.hero_data.keys()))
            if result and result[1] > 60:
                return [result[0]], False

        return [], False

    def validate_hero(self, name, threshold=80):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        if name in self.hero_data:
            return name
        if not self.hero_data:
            return None
        result = process.extractOne(name, list(self.hero_data.keys()))
        if result and result[1] > threshold:
            return result[0]
        return None

    def _load_all_augments(self):
        """加载全量海克斯字典，并构建用于超高召回纠错的索引"""
        all_aug_path = os.path.join(self.data_dir, 'all_augments.json')
        if not os.path.exists(all_aug_path):
            alt = os.path.join(self.base_dir, 'data', 'all_augments.json')
            if os.path.exists(alt):
                all_aug_path = alt
            elif 'ALL_AUGMENTS_FILE' in globals() and os.path.exists(ALL_AUGMENTS_FILE):
                all_aug_path = ALL_AUGMENTS_FILE
        if os.path.exists(all_aug_path):
            try:
                with open(all_aug_path, 'r', encoding='utf-8') as f:
                    self.all_augments = json.load(f)
            except Exception as e:
                print(f"⚠️ {all_aug_path} 加载异常: {e}")
        
        # 若文件不存在或为空，从 hero_augments.csv 自动提取
        if not self.all_augments and self.hero_data:
            tier_counters = defaultdict(lambda: defaultdict(int))
            for hero, augs in self.hero_data.items():
                for name, info in augs.items():
                    t = info.get('tier', '未知')
                    if t and t != '未知':
                        tier_counters[name][t] += 1
            for name, tc in tier_counters.items():
                best_t = max(tc.items(), key=lambda x: x[1])[0] if tc else '未知'
                self.all_augments[name] = {"tier": best_t}

        # 构建规范化反向索引
        self.normalized_aug_map.clear()
        self.augments_by_len.clear()
        for std_name in self.all_augments:
            norm = re.sub(r'[\s\.,\/#!$%\^&\*;:{}=\-_`~()（）！!：:，·]+', '', std_name).strip()
            if norm:
                self.normalized_aug_map[norm] = std_name
                self.augments_by_len[len(norm)].append(norm)
        
        print(f"✅ 全量海克斯真理库加载完毕: 共 {len(self.all_augments)} 个标准海克斯")

    def match_augment_name(self, raw_text):
        """
        全量海克斯多级智能纠错召回引擎：
        1. 精准规范化直出 (O(1))
        2. 常见标签剥离匹配
        3. 短词编辑距离精准纠错
        4. 自适应长度模糊纠错 (长词高容错，短词高保真)
        """
        if not raw_text:
            return None, 0
        
        tag_words = {"伤害", "功能", "防御", "续航", "控制", "通用", "强化", "攻击", "法术", "坦克", "移动", "金币"}

        # 1. 直接精确命中
        if raw_text in self.all_augments:
            return raw_text, 100
        
        # 2. 规范化字符命中 (去除一切冒号、感叹号、空格)
        norm_raw = re.sub(r'[\s\.,\/#!$%\^&\*;:{}=\-_`~()（）！!：:，·]+', '', raw_text).strip()
        if not norm_raw:
            return None, 0
            
        if norm_raw in self.normalized_aug_map:
            return self.normalized_aug_map[norm_raw], 100
        
        # 3. 剥离末尾标签词重试
        stripped_norm = norm_raw
        for tag in tag_words:
            if stripped_norm.endswith(tag) and len(stripped_norm) > len(tag):
                candidate = stripped_norm[:-len(tag)]
                if candidate in self.normalized_aug_map:
                    return self.normalized_aug_map[candidate], 95
                stripped_norm = candidate

        # 4. 候选集过滤 (优先取长度 ±1 的海克斯)
        raw_len = len(stripped_norm)
        candidates = []
        for delta in [0, -1, 1, -2, 2]:
            candidates.extend(self.augments_by_len.get(raw_len + delta, []))
        if not candidates:
            candidates = list(self.normalized_aug_map.keys())

        # 5. 针对 2 字超短词的编辑距离容错 (只要错1字即召回)
        if raw_len == 2:
            two_char_candidates = [c for c in candidates if len(c) == 2]
            best_c = None
            best_diff = 999
            for c in two_char_candidates:
                diff = sum(1 for a, b in zip(stripped_norm, c) if a != b)
                if diff < best_diff:
                    best_diff = diff
                    best_c = c
            if best_diff <= 1 and best_c:
                return self.normalized_aug_map[best_c], 85

        # 6. 动态阈值模糊匹配
        best_match, score = process.extractOne(stripped_norm, candidates, scorer=fuzz.ratio)
        min_score = 65 if raw_len >= 3 else 70
        if score >= min_score:
            return self.normalized_aug_map[best_match], score

        return None, score

# ================= 2. 图像分析 (Core Logic) =================

class GameAnalyzer:
    """负责 OCR 和 图像处理"""
    TIER_PRIORITY = {"棱彩": 0, "黄金": 1, "白银": 2, "未知": 3}

    def __init__(self, data_manager):
        self.dm = data_manager
        # 延迟初始化 OCR 依赖模块
        _ensure_ocr_modules()
        # 初始化 OCR 引擎
        try:
            self.ocr = RapidOCR(use_angle_cls=False)
        except Exception:
            try:
                self.ocr = RapidOCR()
            except Exception as e:
                py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
                print(f"\n❌ OCR 引擎初始化失败: {e}")
                print(f"   当前 Python 版本: {py_ver}")
                if sys.version_info >= (3, 13):
                    print(f"   ⚠ rapidocr_onnxruntime 仅支持 Python <3.13，请使用 Python 3.10~3.12")
                else:
                    print(f"   请尝试: pip install rapidocr_onnxruntime<=1.4.4")
                raise
        # 根据 CPU 逻辑核心数决定并发策略
        self._cpu_count = os.cpu_count() or 4
        self._use_parallel = self._cpu_count >= 12
        self.executor = ThreadPoolExecutor(max_workers=3)
        self._match_cache = {}  # 模糊匹配结果缓存 (hero_cn, text) -> match_name
        # 预热 OCR 引擎 (消除首次推理的模型加载和内存分配延迟)
        self._warmup()

    def _warmup(self):
        """用小图预热 OCR 引擎, 消除首次 F6 的冷启动延迟"""
        try:
            dummy = np.zeros((48, 320), dtype=np.uint8)
            self.ocr(dummy)
            print(f"OCR 引擎预热完成 (CPU: {self._cpu_count} 线程, {'并发' if self._use_parallel else '串行'}模式)")
        except Exception:
            pass

    def capture_all_regions(self):
        """批量截图: 复用单个 mss 上下文, 优先使用硬件级 cv2 处理以降低 CPU 开销"""
        _ensure_ocr_modules()
        images = {}
        try:
            with mss.mss() as sct:
                for key, region in REGIONS.items():
                    monitor = {
                        "top": int(region["top"]),
                        "left": int(region["left"]),
                        "width": int(region["width"]),
                        "height": int(region["height"]),
                        "mon": 0
                    }
                    raw = sct.grab(monitor)
                    if HAS_CV2:
                        # 零拷贝/低开销转换: 直接从 mss 缓冲区转为 numpy 矩阵 (BGRA)
                        arr = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))
                        gray = cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
                        # CLAHE 对比度自适应直方图均衡化，显著提升弱光发光文字笔画边缘与深色底纹对比
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                        enhanced = clahe.apply(gray)
                        # SIMD 硬件加速双线性插值放大 2 倍
                        images[key] = cv2.resize(enhanced, (raw.width * 2, raw.height * 2), interpolation=cv2.INTER_LINEAR)
                    else:
                        img = Image.frombytes("RGB", raw.size, raw.rgb)
                        gray = img.convert("L")
                        w, h = gray.size
                        resized = gray.resize((w * 2, h * 2), Image.BICUBIC)
                        images[key] = np.array(resized)
        except Exception as e:
            print(f"批量截图失败: {e}")
        return images

    def _extract_title_from_ocr(self, res_ocr):
        """
        智能解析 OCR 识别结果：
        1. 针对同一水平行被标点/空格拆碎的文字框（如 ['升级：', '耀光']），基于纵坐标聚类并按 x0 从左到右严格拼接
        2. 各文本行按 y0 从上到下排序
        3. 过滤纯分类标签行（伤害、功能、防御等）
        4. 剥离误粘连在标题末尾的标签词
        5. 选出主标题行进行全量匹配
        """
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

        return line_strings[0]

    def _ocr_and_match(self, key, img, hero_cn):
        """对单张已截取的图片执行 OCR 识别 + 全局真理库匹配 + 英雄胜率关联"""
        try:
            if img is None:
                return {"key": key, "text": "截图错误", "error": True}

            res_ocr, _ = self.ocr(img)
            txt = self._extract_title_from_ocr(res_ocr)

            res = {
                "key": key, "valid": False, "rank": 999, 
                "text": "", "highlight": False, "error": False
            }

            if not txt:
                res["text"] = "❌ 无文字"
                res["error"] = True
                return res

            # 1. 优先查缓存
            cache_key = (hero_cn or "", txt)
            if cache_key in self._match_cache:
                cached = self._match_cache[cache_key]
                res.update(cached)
                return res

            # 2. 全局海克斯真理库召回
            match_name, score = self.dm.match_augment_name(txt)

            if match_name:
                hero_augments = self.dm.hero_data.get(hero_cn, {}) if hero_cn else {}
                
                # 命中英雄专属胜率数据 (支持精确匹配 + 标点归一化匹配)
                info = hero_augments.get(match_name)
                if not info:
                    norm_m = re.sub(r'[\s\.,\/#!$%\^&\*;:{}=\-_`~()（）！!：:，·]+', '', match_name)
                    for k, v in hero_augments.items():
                        if re.sub(r'[\s\.,\/#!$%\^&\*;:{}=\-_`~()（）！!：:，·]+', '', k) == norm_m:
                            info = v
                            break

                if info:
                    tier = info.get('tier', '?')
                    t_rank = info.get('t_rank', 999)
                    overall_rank = info.get('overall_rank', 999)
                    res["text"] = "【" + match_name + "】\n总No." + str(overall_rank) + " | " + str(tier) + " No." + str(t_rank)
                    res["valid"] = True
                    res["tier"] = tier
                    res["t_rank"] = t_rank
                    res["overall_rank"] = overall_rank
                else:
                    # 全局收录但该英雄暂未上榜（冷门/非专属海克斯）：使用全局品质兜底展示
                    std_info = self.dm.all_augments.get(match_name, {})
                    tier = std_info.get('tier', '未知')
                    res["text"] = "【" + match_name + "】\n" + str(tier) + " | 冷门/无排名"
                    res["valid"] = True
                    res["tier"] = tier
                    res["t_rank"] = 999
                    res["overall_rank"] = 500  # 通用默认居中推荐
                
                # 写入缓存
                self._match_cache[cache_key] = {
                    "valid": res["valid"],
                    "text": res["text"],
                    "tier": res.get("tier", "未知"),
                    "t_rank": res.get("t_rank", 999),
                    "overall_rank": res.get("overall_rank", 999),
                    "error": False
                }
            else:
                res["text"] = f"❌ 未识别: {txt[:6]}"
                res["error"] = True
            
            return res
        except Exception as e:
            print(f"处理异常 ({key}): {e}")
            return {"key": key, "text": "Error", "error": True}

    def analyze(self, hero_cn):
        if not hero_cn: return {}
        print(f"正在分析: {hero_cn}...")
        
        # 阶段1: 批量截图 (复用 mss 上下文, 总耗时 ~18ms)
        images = self.capture_all_regions()
        
        # 阶段2: OCR 识别 + 匹配 (自适应并发策略)
        results = {}
        valid_matches = []

        if self._use_parallel:
            # 高端 CPU (>=12 线程): 并发 OCR, 充分利用多核
            futures = []
            for key in images:
                futures.append(self.executor.submit(self._ocr_and_match, key, images[key], hero_cn))
            for f in futures:
                try:
                    data = f.result()
                    results[data["key"]] = data
                    if data.get("valid"): valid_matches.append(data)
                except Exception as e:
                    print(f"并发任务异常: {e}")
        else:
            # 低端 CPU (<12 线程): 串行 OCR, 避免缓存争抢和线程切换开销
            for key, img in images.items():
                data = self._ocr_and_match(key, img, hero_cn)
                results[data["key"]] = data
                if data.get("valid"): valid_matches.append(data)

        # 计算最优推荐：总排名优先（越小越好），总排名相同则按等级排序
        if valid_matches:
            def sort_key(item):
                o_rank = item.get('overall_rank', 999)
                tp = self.TIER_PRIORITY.get(item.get('tier', '未知'), 3)
                tr = item.get('t_rank', 999)
                return (o_rank, tp, tr)
            
            best = min(valid_matches, key=sort_key)
            best_key = sort_key(best)
            for item in valid_matches:
                if sort_key(item) == best_key:
                    results[item['key']]["highlight"] = True
        
        return results


# ================= 3. UI 界面 (View) =================

class OverlayApp:
    def __init__(self, root, queue):
        self.root = root
        self.queue = queue
        self.labels = {}
        self.status_lbl = None
        self.hide_timer = None
        self.layout = BANNER_LAYOUT
        
        # 优化: 保持彻底隐藏 (withdraw)，仅在展示推荐时无焦点唤醒
        self.root.withdraw()
        self._setup_window()
        self._setup_labels()
        
        # 启动队列消息监听 (优化: 120ms 轮询, 减少后台空闲 CPU 唤醒)
        self.root.after(120, self.process_queue)

    def _get_hwnd(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            return hwnd if hwnd else self.root.winfo_id()
        except Exception:
            return None

    def _setup_window(self):
        self.root.title("ARAM Overlay")
        self.root.overrideredirect(True) # 无边框
        self.root.attributes("-topmost", True) # 置顶
        self.root.config(bg=COLORS["bg"])
        self.root.attributes("-transparentcolor", COLORS["bg"]) # 背景透明
        
        # 关键优化: 紧凑几何定位 (仅覆盖三张卡牌上方 ~1000x85 像素，面积缩减 96% 以上，彻底杜绝全屏 DWM 合成引发的掉帧)
        self.root.geometry(f"{self.layout['width']}x{self.layout['height']}+{self.layout['x']}+{self.layout['y']}")

        # 核心 Windows 扩展样式:
        # WS_EX_LAYERED (0x00080000): 分层半透明
        # WS_EX_NOACTIVATE (0x08000000): 绝不抢夺焦点/激活状态，彻底杜绝打断 LOL DirectX 独占渲染管线
        # WS_EX_TOOLWINDOW (0x00000080): 工具浮窗，不在任务栏显示
        # 说明: 透明背景自动穿透鼠标到游戏；未设置全局 0x00000020 以允许点击右上角 [✕ 关闭] 按钮
        try:
            self.root.update_idletasks()
            hwnd = self._get_hwnd()
            if hwnd:
                GWL_EXSTYLE = -20
                old_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                new_style = old_style | 0x00080000 | 0x08000000 | 0x00000080
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        except Exception as e:
            print(f"窗口样式配置警告: {e}")

    def _setup_labels(self):
        font_style = ("Microsoft YaHei", 13, "bold")
        for key in REGIONS:
            lbl = tk.Label(self.root, text="", font=font_style, bg=COLORS["bg"], justify="left")
            self.labels[key] = lbl
        
        # 独立居中状态提示标签
        self.status_lbl = tk.Label(self.root, text="", font=("Microsoft YaHei", 13, "bold"), bg=COLORS["bg"], justify="center")

        # 立即关闭按钮: 选完海克斯后可秒速点击关闭，立刻销毁隐藏释放渲染
        self.close_btn = tk.Button(
            self.root,
            text="✕ 关闭",
            font=("Microsoft YaHei", 9, "bold"),
            bg="#21262d",
            fg="#f85149",
            activebackground="#da3633",
            activeforeground="#ffffff",
            relief="solid",
            bd=1,
            cursor="hand2",
            padx=6,
            pady=1,
            command=self.clear_display
        )
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.config(bg="#da3633", fg="#ffffff"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.config(bg="#21262d", fg="#f85149"))

    def _show_no_activate(self):
        """以无焦点无激活的方式显示窗口，绝不让 LOL 丢失焦点引发掉帧"""
        try:
            self.root.deiconify()
            hwnd = self._get_hwnd()
            if hwnd:
                # SWP_NOSIZE(1) | SWP_NOMOVE(2) | SWP_NOACTIVATE(16) | SWP_SHOWWINDOW(64) = 0x53
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0053)
        except Exception:
            pass

    def process_queue(self):
        """主线程轮询：处理来自后台线程的指令 (优化: 自适应低频休眠)"""
        has_msg = False
        try:
            while True:
                msg = self.queue.get_nowait()
                has_msg = True
                cmd = msg.get("cmd")
                data = msg.get("data")
                
                if cmd == "UPDATE":
                    self.update_display(data)
                elif cmd == "STATUS":
                    self.show_status(data)
                elif cmd == "CLEAR":
                    self.clear_display()
        except queue.Empty:
            pass
        finally:
            try:
                is_visible = self.root.winfo_viewable()
            except Exception:
                is_visible = False
            # 浮窗不可见且无任务时保持 250ms 静默休眠，展示时提升至 80ms
            interval = 80 if (is_visible or has_msg) else 250
            self.root.after(interval, self.process_queue)

    def clear_display(self):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        for lbl in self.labels.values():
            lbl.place_forget()
        if self.status_lbl:
            self.status_lbl.place_forget()
        if hasattr(self, 'close_btn') and self.close_btn:
            self.close_btn.place_forget()
        # 彻底隐藏窗口，完全撤出 DWM 渲染树，恢复游戏 DirectFlip 极速渲染
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show_status(self, text):
        self.clear_display()
        self.status_lbl.config(text=text, fg=COLORS["status"])
        self.status_lbl.place(relx=0.5, rely=0.5, anchor="center")
        self._show_no_activate()
        # 状态提示2秒后消失
        self.hide_timer = self.root.after(2000, self.clear_display)

    def update_display(self, results):
        self.clear_display()
        has_content = any(info.get("text") for info in results.values())
        if not has_content:
            return

        min_left = self.layout['min_left']
        for key, info in results.items():
            if not info.get("text"): continue
            
            lbl = self.labels[key]
            # 颜色逻辑
            if info["error"]:
                fg = COLORS["error"]
            elif info["highlight"]:
                fg = COLORS["best"]
            else:
                fg = COLORS["normal"]
            
            lbl.config(text=info["text"], fg=fg)
            rel_x = REGIONS[key]['left'] - min_left
            lbl.place(x=rel_x, y=8, anchor="nw")

        # 右上角放置 [✕ 关闭] 按钮
        if hasattr(self, 'close_btn') and self.close_btn:
            self.close_btn.place(relx=1.0, rely=0.08, x=-8, y=0, anchor="ne")

        self._show_no_activate()
        # 结果显示 10 秒后自动消失 (亦可点击右上角 [✕ 关闭] 秒关)
        self.hide_timer = self.root.after(10000, self.clear_display)

# ================= 4. 控制逻辑 (Controller) =================

class InputController(threading.Thread):
    def __init__(self, app_queue, data_manager, analyzer, lcu_connector=None):
        super().__init__(daemon=True)
        self.queue = app_queue
        self.dm = data_manager
        self.analyzer = analyzer
        self.lcu = lcu_connector
        self.current_hero = None
        self.paused = False
        self.action_queue = queue.Queue()
        self._hotkeys_registered = False
        self._last_f5 = 0
        self._last_f6 = 0
        self._last_f7 = 0
        self._last_f8 = 0
        self._last_auto_check = 0
        self._last_game_check = 0

    def _on_hotkey(self, key_name):
        """全局热键事件回调 (防抖)"""
        now = time.time()
        if key_name == 'f5':
            if now - self._last_f5 > 0.8:
                self._last_f5 = now
                self.action_queue.put('f5')
        elif key_name == 'f6':
            if now - self._last_f6 > 0.8:
                self._last_f6 = now
                self.action_queue.put('f6')
        elif key_name == 'f7':
            if now - self._last_f7 > 0.8:
                self._last_f7 = now
                self.action_queue.put('f7')
        elif key_name == 'f8':
            if now - self._last_f8 > 0.8:
                self._last_f8 = now
                self.action_queue.put('f8')

    def _register_hotkeys(self):
        """基于 Windows 底层钩子的事件驱动热键 (0% 算力，0 漏键)"""
        if self._hotkeys_registered:
            return
        try:
            keyboard.add_hotkey('f5', lambda: self._on_hotkey('f5'), suppress=False)
            keyboard.add_hotkey('f6', lambda: self._on_hotkey('f6'), suppress=False)
            keyboard.add_hotkey('f7', lambda: self._on_hotkey('f7'), suppress=False)
            keyboard.add_hotkey('f8', lambda: self._on_hotkey('f8'), suppress=False)
            self._hotkeys_registered = True
        except Exception as e:
            print(f"热键注册提示: {e}")

    def _unregister_hotkeys(self):
        """注销热键钩子"""
        if not self._hotkeys_registered:
            return
        for k in ('f5', 'f6', 'f7', 'f8'):
            try:
                keyboard.remove_hotkey(k)
            except Exception:
                pass
        self._hotkeys_registered = False

    def run(self):
        if self.lcu:
            self.lcu.start_ws_listener(
                on_hero_changed=self._on_ws_hero_changed,
                on_phase_changed=self._on_ws_phase_changed,
                on_hero_reset=self._on_ws_hero_reset
            )
        while True:
            self.select_hero_phase()
            self.listening_phase()

    def _apply_hero(self, hero, source):
        validated = self._validate_hero(hero)
        if not validated:
            return
        if validated != self.current_hero:
            old = self.current_hero
            self.current_hero = validated
            if old:
                print(f"\n🔄 英雄已自动切换 ({source}): {old} -> {validated}")
            else:
                print(f"\n✅ 自动识别到英雄: [{validated}] (数据源: {source})")
            print(f">>> F5=总开关 | F6=分析 | F7=刷新 | F8=手动")
            self.queue.put({"cmd": "STATUS", "data": f"当前: {validated}\n按 F6 分析 | F7 刷新"})
            self.hide_console_window()

    def _on_ws_hero_changed(self, hero, source):
        self._apply_hero(hero, source)

    def _on_ws_phase_changed(self, phase):
        pass

    def _on_ws_hero_reset(self):
        if self.current_hero:
            print(f"\n🏁 对局结束或返回大厅，自动重置英雄 [{self.current_hero}]，等待下局选人")
            self.current_hero = None
            self.queue.put({"cmd": "STATUS", "data": "对局结束\n等待下局选人..."})

    def _check_game_ended(self):
        """检测当前对局是否已结束，或选人阶段是否变更了英雄"""
        if not self.lcu:
            return

        try:
            if self.lcu.is_connected():
                phase = self.lcu.get_gameflow_phase()
                if phase in ("EndOfGame", "PreEndOfGame", "WaitingForStats", "None", "Lobby", "Matchmaking"):
                    self._on_ws_hero_reset()
                    return
                elif phase == "ChampSelect":
                    hero = self.lcu.get_champ_select_champion()
                    if hero and hero != self.current_hero:
                        self._apply_hero(hero, "选人阶段(更新)")
                    return

            live_hero = self.lcu.get_ingame_champion()
            if not live_hero:
                phase = self.lcu.get_gameflow_phase() if self.lcu.is_connected() else None
                if phase in ("EndOfGame", "PreEndOfGame", "WaitingForStats", "None", "Lobby", "Matchmaking"):
                    self._on_ws_hero_reset()
        except Exception:
            pass

    def flush_input(self):
        """强制清空标准输入缓冲区"""
        while msvcrt.kbhit():
            msvcrt.getch()

    def _validate_hero(self, name):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        return self.dm.validate_hero(name)

    def _try_auto_detect(self):
        """使用 LCU 统一接口自动获取英雄，返回 (英雄中文名|None, 来源)"""
        if not self.lcu:
            return None, ""
        hero, source = self.lcu.get_champion_auto()
        if hero:
            validated = self._validate_hero(hero)
            if validated:
                return validated, source
        return None, source

    # ==========================================
    # 阶段1: 选择英雄 (自动轮询 + 手动备用)
    # ==========================================

    def select_hero_phase(self):
        self.queue.put({"cmd": "CLEAR"})
        self.show_console_window()

        time.sleep(0.1)
        os.system('cls')
        self.flush_input()

        print("=== ARAM Hextech Helper ===")
        print("    F5=总开关 | F6=分析 | F7=刷新英雄 | F8=手动输入\n")

        # ====== 尝试自动检测 (轮询最多30秒) ======
        if self.lcu:
            print("[Auto] 正在连接英雄联盟客户端...")

            for attempt in range(8):  # 每2秒检测，共30秒
                hero, source = self._try_auto_detect()
                if hero:
                    self.current_hero = hero
                    print(f"\n>>> 自动识别到英雄: [{hero}] (数据源: {source})")
                    print(f">>> F5=总开关 | F6=分析 | F7=刷新 | F8=手动")
                    self.queue.put({"cmd": "STATUS", "data": f"当前: {hero}\n按 F6 分析 | F7 刷新"})
                    self.hide_console_window()
                    return

                # F8 跳过自动检测并手动输入
                if keyboard.is_pressed('f8'):
                    print("\n[F8] 切换至手动输入...")
                    time.sleep(0.5)
                    break

                dots = "." * ((attempt % 3) + 1)
                print(f"\r[Auto] 等待选取英雄{dots}   ", end="", flush=True)
                time.sleep(2)
            else:
                # 30秒后没搜到，不锁在死循环里，直接进入监听模式
                print("\n[Auto] 暂未自动识别到英雄。将切入后台继续运行。")
                print(">>> 随时按 [F7] 重新获取，或按 [F8] 呼出控制台手动输入。\n")
                self.current_hero = None
                self.queue.put({"cmd": "STATUS", "data": "暂无英雄\n按 F7 自动获取本局英雄"})
                self.hide_console_window()
                return

        # ====== 手动输入 (仅在按下 F8 时，或 LCU 完全异常时进入) ======
        print(">>> 请输入英雄名称 (拼音/中文):")

        while True:
            try:
                self.flush_input()
                raw = input("Input: ").strip()
            except EOFError:
                continue
            if not raw:
                continue

            matches, is_exact = self.dm.search_hero(raw)
            selected_name = None

            if not matches:
                print("❌ 未找到，请重试")
                continue

            if len(matches) > 1:
                print(f"🤔 发现多个匹配项，请选择:")
                for idx, name in enumerate(matches):
                    print(f"   {idx + 1}. {name}")
                print(">>> 请输入序号:")
                self.flush_input()
                try:
                    idx = int(input("Select: ").strip()) - 1
                    if 0 <= idx < len(matches):
                        selected_name = matches[idx]
                    else:
                        print("无效选项，请重试")
                        continue
                except ValueError:
                    print("无效输入，请重试")
                    continue
            else:
                candidate = matches[0]
                if is_exact:
                    selected_name = candidate
                else:
                    print(f"   猜你是: {candidate}? (Enter确认 / n重输)")
                    self.flush_input()
                    if input().strip().lower() == 'n':
                        continue
                    selected_name = candidate

            if selected_name:
                validated = self._validate_hero(selected_name)
                if not validated:
                    print(f"数据库暂无【{selected_name}】的数据")
                    continue
                self.current_hero = validated
                print(f">>> 已锁定: {validated}")
                print(f">>> F5=总开关 | F6=分析 | F7=刷新 | F8=手动")
                self.queue.put({"cmd": "STATUS", "data": f"当前: {validated}\n按 F6 分析 | F7 刷新"})
                self.hide_console_window()
                break

    # ==========================================
    # 阶段2: 监听热键
    # ==========================================

    def listening_phase(self):
        self.flush_input()
        print(f"[监听中...] 当前英雄: {self.current_hero} | F5开关 / F6分析 / F7刷新 / F8手动")
        self._register_hotkeys()

        while True:
            # 核心优化: 操作系统事件驱动挂起，无按键时阻塞等待，0.00% CPU 占用
            try:
                action = self.action_queue.get(timeout=2.0)
            except queue.Empty:
                action = None

            now = time.time()

            if action == 'f5':
                self.paused = not self.paused
                if self.paused:
                    print(">>> [F5] 助手已暂停 (0% 算力占用)")
                    self.queue.put({"cmd": "STATUS", "data": "⏸ 助手已暂停 (F5)"})
                else:
                    print(">>> [F5] 助手已恢复")
                    self.queue.put({"cmd": "STATUS", "data": f"▶ 助手已恢复 (F5)\n当前: {self.current_hero or '未知'}"})

            if self.paused:
                continue

            if action == 'f6':
                if not self.current_hero:
                    hero, source = self._try_auto_detect()
                    if hero:
                        self._apply_hero(hero, source)

                if not self.current_hero:
                    self.queue.put({"cmd": "STATUS", "data": "⚠ 尚未锁定英雄\n请按 F7 自动获取或 F8 手动输入"})
                    continue
                
                print(f"🔎 正在分析 [{self.current_hero}]...")
                results = self.analyzer.analyze(self.current_hero)
                self.queue.put({"cmd": "UPDATE", "data": results})

            elif action == 'f7':
                self.queue.put({"cmd": "STATUS", "data": "刷新英雄..."})
                hero, source = self._try_auto_detect()
                if hero:
                    self._apply_hero(hero, source)
                else:
                    self.queue.put({"cmd": "STATUS", "data": f"当前: {self.current_hero or '未知'}\n按 F6 分析"})

            elif action == 'f8':
                self._unregister_hotkeys()
                time.sleep(0.3)
                return  # 退出监听，回到 select_hero_phase

            # 自动探查与流转监控 (未锁定英雄时每 2 秒静默探查，已锁定后每 5 秒轻量检测)
            if not self.current_hero:
                if now - self._last_auto_check >= 2.0:
                    self._last_auto_check = now
                    hero, source = self._try_auto_detect()
                    if hero:
                        self._apply_hero(hero, source)
            else:
                if now - self._last_game_check >= 5.0:
                    self._last_game_check = now
                    self._check_game_ended()


    @staticmethod
    def show_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def hide_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass

# ================= 5. 主入口 =================

def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)
    
    # 强制设置工作目录为应用根目录 (兼容打包)
    os.chdir(BASE_DIR)
    os.system('title ARAM 海克斯助手')
    os.system('chcp 65001 >nul')
    print(f"Working Directory: {BASE_DIR}")

    # 降低进程优先级，确保 LOL 游戏独占 CPU
    set_low_priority()

    # 1. 初始化核心数据与逻辑
    dm = DataManager()
    
    if not dm.hero_data:
        print("❌ 警告: 未加载到任何英雄数据，请检查CSV文件。")
        input("按任意键退出...")
        return

    analyzer = GameAnalyzer(dm)
    
    # 2. 初始化 LCU 客户端连接器
    champions_json = os.path.join(dm.data_dir, 'champions.json')
    lcu = LCUConnector(champions_json)
    
    # 3. 初始化 UI 与 通信队列
    root = tk.Tk()
    msg_queue = queue.Queue()
    app = OverlayApp(root, msg_queue)
    
    # 4. 启动后台控制线程
    controller = InputController(msg_queue, dm, analyzer, lcu_connector=lcu)
    controller.start()
    
    # 4. 进入 UI 主循环
    print("程序已启动...")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        os._exit(0)

if __name__ == "__main__":
    main()