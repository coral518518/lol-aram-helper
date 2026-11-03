"""
ARAM 海克斯助手 - 一键打包脚本
运行: python build.py
输出: dist/最新版_ARAMHelper/ 以及 dist/build_<timestamp>/ARAMHelper/
"""
import subprocess
import shutil
import os
import sys
import time
import glob


APP_NAME = "ARAMHelper"
ENTRY_POINT = "gui_launcher.py"
ICON_PATH = os.path.join("assets", "icon.ico")
DIST_DIR = os.path.join("dist", APP_NAME)


def check_dependencies():
    """检查打包依赖"""
    try:
        import PyInstaller
        print(f"✅ PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("❌ PyInstaller 未安装。运行: pip install pyinstaller")
        return False

    if not os.path.exists(ENTRY_POINT):
        print(f"❌ 入口文件不存在: {ENTRY_POINT}")
        return False

    if not os.path.exists(ICON_PATH):
        print(f"⚠ 图标文件不存在: {ICON_PATH}，将使用默认图标")

    return True


def build():
    """执行 PyInstaller 打包"""
    global DIST_DIR
    print(f"\n{'='*50}")
    print(f"  打包 {APP_NAME}")
    print(f"  入口: {ENTRY_POINT}")
    print(f"  模式: --onedir (单目录)")
    print(f"{'='*50}\n")

    # 排除不需要的大型包 (避免 Anaconda/全局环境拖入无关重型库)
    EXCLUDES = [
        "torch", "torchvision", "torchaudio",
        "scipy", "pandas", "matplotlib",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy",
        "IPython", "jupyter", "notebook", "nbformat", "nbconvert",
        "pytest", "black", "yapf", "jedi", "parso",
        "dask", "distributed", "numba", "llvmlite",
        "h5py", "tables", "sqlalchemy", "botocore", "boto3",
        "pyarrow", "openpyxl", "lxml",
        "docutils", "sphinx", "pygments",
        "zmq", "tornado",
        "lib2to3", "blib2to3",
        "nacl", "cloudpickle",
        "onnxruntime.transformers",      # 这个子包依赖 torch
    ]

    # 使用唯一的时间戳生成一个新的分发父目录, 彻底避开旧目录的文件锁定
    timestamp = int(time.time())
    unique_dist = os.path.join("dist", f"build_{timestamp}")
    DIST_DIR = os.path.join(unique_dist, APP_NAME)

    # 构建命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--noconsole",
        "--noupx",               # 禁用 UPX 压缩，彻底杜绝 MSVCP140/vcruntime 符号表损坏与 0xC0000005 崩溃
        "--uac-admin",           # 默认请求管理员权限 (热键与前台获取必需)
        "--name", APP_NAME,
        "--noconfirm",           # 不确认覆盖
        "--clean",               # 清理缓存
        "--distpath", unique_dist,
        
        # 收集 rapidocr 完整包 (含 ONNX 模型与字典)
        "--collect-all", "rapidocr_onnxruntime",

        # numpy: 完整收集
        "--collect-all", "numpy",

        # onnxruntime: 收集完整 native bindings 与配置
        "--collect-all", "onnxruntime",

        # 运行时 hook: 修复 numpy 冻结环境检测
        "--runtime-hook", os.path.join("runtime_hooks", "fix_numpy.py"),

        # 隐式导入
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "thefuzz",
        "--hidden-import", "rapidfuzz",
        "--hidden-import", "onnxruntime",
        "--hidden-import", "keyboard",
        "--hidden-import", "websocket",
        "--hidden-import", "scripts",
        "--hidden-import", "scripts.lcu_connector",
        "--hidden-import", "scripts.hero_scraper",
        "--hidden-import", "scripts.updater",
        "--hidden-import", "scripts.utils",
        "--hidden-import", "main",

        ENTRY_POINT,
    ]

    # 添加排除项
    for pkg in EXCLUDES:
        cmd.insert(-1, "--exclude-module")
        cmd.insert(-1, pkg)

    # 添加图标
    if os.path.exists(ICON_PATH):
        cmd.insert(-1, "--icon")
        cmd.insert(-1, ICON_PATH)

    print("执行命令:")
    print(" ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)) or ".")
    if result.returncode != 0:
        print(f"\n❌ PyInstaller 打包失败 (exit code: {result.returncode})")
        return False

    print("\n✅ PyInstaller 打包完成")
    return True


def copy_runtime_files():
    """复制运行时需要的数据文件与权威 VC 运行时动态库到 dist 目录"""
    print("\n--- 复制运行时文件 ---")

    # 1. 复制 data/ 目录 (CSV, JSON - 用户可更新与离线纠错真理库)
    src_data = "data"
    dst_data = os.path.join(DIST_DIR, "data")
    if os.path.exists(src_data):
        if os.path.exists(dst_data):
            shutil.rmtree(dst_data)
        shutil.copytree(src_data, dst_data)
        print(f"✅ data/ → {dst_data}")
    else:
        print(f"⚠ data/ 不存在，跳过")

    # 2. 复制 assets/ 目录 (图标与素材)
    src_assets = "assets"
    dst_assets = os.path.join(DIST_DIR, "assets")
    if os.path.exists(src_assets):
        if os.path.exists(dst_assets):
            shutil.rmtree(dst_assets)
        shutil.copytree(src_assets, dst_assets)
        print(f"✅ assets/ → {dst_assets}")
    else:
        print(f"⚠ assets/ 不存在，跳过")

    # 3. 注入系统权威最新的 Visual C++ 2015-2022 运行时动态库
    # 彻底杜绝 PyInstaller 抓取旧版 MSVCP140.dll (14.36) 导致的 0xC0000005 访问违规闪退
    internal_dir = os.path.join(DIST_DIR, "_internal")
    if os.path.exists(internal_dir):
        sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        vc_patterns = ["msvcp140*.dll", "vcruntime140*.dll", "vcomp140*.dll"]
        copied_vc = 0
        for pat in vc_patterns:
            for f in glob.glob(os.path.join(sys32, pat)):
                try:
                    shutil.copy2(f, internal_dir)
                    copied_vc += 1
                except Exception as e:
                    print(f"⚠ 复制 VC 运行时提示: {e}")
        if copied_vc > 0:
            print(f"✅ 已注入系统最新 VC++ 2015-2022 运行时动态库 ({copied_vc} 个 DLL)")

    # 4. 生成一键控制台调试启动脚本
    bat_content = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================
echo  ARAM 海克斯助手 (控制台排错启动器)
echo  如遇异常，请保留此控制台窗口并复制报错信息
echo ====================================================
echo 正在启动 ARAMHelper.exe ...
ARAMHelper.exe
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ 程序发生异常退出，退出码: %ERRORLEVEL%
    pause
)
"""
    bat_path = os.path.join(DIST_DIR, "启动助手_带调试控制台.bat")
    try:
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        print(f"✅ 生成排错批处理: {bat_path}")
    except Exception as e:
        print(f"⚠ 写入排错批处理提示: {e}")

    print("✅ 运行时文件配置完成")


def sync_to_latest_dist():
    """自动同步一份到 dist/最新版_ARAMHelper 目录，方便用户直接运行"""
    try:
        latest_dir = os.path.join("dist", "最新版_ARAMHelper")
        print(f"\n--- 正在同步到直接运行目录: {latest_dir} ---")
        if os.path.exists(latest_dir):
            shutil.rmtree(latest_dir, ignore_errors=True)
        shutil.copytree(DIST_DIR, latest_dir)
        print(f"✅ 最新免安装版本已就绪: {os.path.abspath(latest_dir)}\\ARAMHelper.exe")
    except Exception as e:
        print(f"⚠ 同步到最新版目录提示: {e}")


def print_summary():
    """打印打包结果摘要"""
    print(f"\n{'='*50}")
    print(f"  ✅ 打包完成!")
    print(f"{'='*50}")
    print(f"\n  输出目录: {os.path.abspath(DIST_DIR)}")
    print(f"  可执行文件: {os.path.join(DIST_DIR, APP_NAME + '.exe')}")

    # 计算目录大小
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(DIST_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    size_mb = total_size / (1024 * 1024)
    print(f"  总大小: {size_mb:.1f} MB")

    latest_dir = os.path.join("dist", "最新版_ARAMHelper")
    print(f"\n  ⭐ 用户直接测试推荐目录: {os.path.abspath(latest_dir)}")
    print(f"  可双击: {os.path.join(latest_dir, APP_NAME + '.exe')}")
    print(f"  或者排错双击: {os.path.join(latest_dir, '启动助手_带调试控制台.bat')}\n")


def main():
    # 修复 Windows 控制台编码
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    os.system('chcp 65001 >nul 2>&1')

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

    if not check_dependencies():
        return

    if not build():
        return

    copy_runtime_files()
    sync_to_latest_dist()
    print_summary()


if __name__ == "__main__":
    main()
