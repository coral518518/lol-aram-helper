with open('main.py', 'r', encoding='utf-8') as f:
    c = f.read()

top_old = """from scripts.lcu_connector import LCUConnector
from rapidocr_onnxruntime import RapidOCR

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False"""

top_new = """from scripts.lcu_connector import LCUConnector

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
        sys32 = os.path.join(os.environ.get('SystemRoot', r'C:\\Windows'), 'System32')
        if os.path.exists(sys32):
            os.add_dll_directory(sys32)
    except Exception:
        pass

RapidOCR = None
cv2 = None
HAS_CV2 = False

def _ensure_ocr_modules():
    \"\"\"按需延迟加载 RapidOCR 与 OpenCV，彻底杜绝数据加载或轻量启动时的 C++ CRT 崩溃\"\"\"
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
        RapidOCR = _RapidOCR"""

assert top_old in c, "top_old not found"
c = c.replace(top_old, top_new, 1)

init_old = """    def __init__(self, data_manager):
        self.dm = data_manager
        # 初始化 OCR 引擎
        try:"""

init_new = """    def __init__(self, data_manager):
        self.dm = data_manager
        # 延迟初始化 OCR 依赖模块
        _ensure_ocr_modules()
        # 初始化 OCR 引擎
        try:"""

assert init_old in c, "init_old not found"
c = c.replace(init_old, init_new, 1)

cap_old = """    def capture_all_regions(self):
        \"\"\"批量截图: 复用单个 mss 上下文, 优先使用硬件级 cv2 处理以降低 CPU 开销\"\"\"
        images = {}"""

cap_new = """    def capture_all_regions(self):
        \"\"\"批量截图: 复用单个 mss 上下文, 优先使用硬件级 cv2 处理以降低 CPU 开销\"\"\"
        _ensure_ocr_modules()
        images = {}"""

assert cap_old in c, "cap_old not found"
c = c.replace(cap_old, cap_new, 1)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(c)

print("main.py updated successfully!")

