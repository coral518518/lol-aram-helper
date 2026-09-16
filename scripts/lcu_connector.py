"""
LCU Connector - 英雄联盟客户端本地 API 连接器

通过读取 LeagueClientUx.exe 的进程信息或 lockfile，
连接客户端本地 API 以自动获取当前英雄（全生命周期覆盖）。

支持三种获取模式:
  1. ChampSelect 阶段: /lol-champ-select/v1/session
  2. InProgress  阶段: /lol-gameflow/v1/session (gameData)
  3. InGame     备用:  Live Client Data API (端口 2999)
"""
import base64
import json
import os
import ssl
import threading
import time

import psutil
import requests
import urllib3

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

# 禁用 SSL 自签名证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 常见的英雄联盟安装路径（用于 lockfile 备选读取）
COMMON_INSTALL_PATHS = [
    r"C:\Riot Games\League of Legends",
    r"D:\Riot Games\League of Legends",
    r"E:\Riot Games\League of Legends",
    r"C:\Program Files\Riot Games\League of Legends",
    r"D:\Program Files\Riot Games\League of Legends",
    r"C:\Riot Games\英雄联盟",
    r"D:\Riot Games\英雄联盟",
    r"D:\WeGameApps\英雄联盟",
    r"D:\WeGame\英雄联盟",
    r"E:\WeGame\英雄联盟",
    r"D:\腾讯游戏\英雄联盟",
    r"E:\腾讯游戏\英雄联盟",
]


class LCUConnector:
    """英雄联盟客户端 LCU API 连接器（全生命周期 + WebSocket 事件驱动）"""

    LCU_TIMEOUT = 3       # LCU API 请求超时 (秒)
    LIVE_API_TIMEOUT = 2  # Live Client Data API 超时 (秒)

    def __init__(self, champions_json_path):
        self.port = None
        self.auth_token = None
        self.base_url = None
        self._connected = False
        self._summoner_id = None  # 缓存当前召唤师ID

        # 锁文件路径记忆缓存 (避免反复调用 psutil 遍历进程)
        self._cached_lockfile_path = None
        self._session = requests.Session()
        self._session.verify = False

        # WebSocket 事件监听相关
        self._ws_app = None
        self._ws_thread = None
        self._ws_running = False
        self.on_hero_changed = None   # callback(hero_name, source)
        self.on_phase_changed = None  # callback(phase)
        self.on_hero_reset = None     # callback()

        # 加载 champions.json: 中文名 -> 英文名
        self.cn_to_en = {}
        # 反向映射: 英文名(小写) -> 中文名
        self.en_to_cn = {}
        self._load_champions_map(champions_json_path)

        # 英雄 ID -> 中文名映射 (连接后构建)
        self.id_to_cn = {}

    def _load_champions_map(self, path):
        """加载 champions.json 构建中英文映射"""
        if not os.path.exists(path):
            print(f"   [WARN] LCU: champions.json not found: {path}")
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self.cn_to_en = json.load(f)
            for cn, en in self.cn_to_en.items():
                self.en_to_cn[en.lower()] = cn
            print(f"   [OK] heroes loaded: {len(self.cn_to_en)}")
        except Exception as e:
            print(f"   [WARN] champions.json error: {e}")

    # ==========================================
    # 连接方法
    # ==========================================

    def connect(self):
        """尝试连接到 League 客户端。返回 bool"""
        # 优化: 优先尝试极速且 0 CPU 开销的 lockfile，未命中再尝试扫描进程
        if self._connect_via_lockfile() or self._connect_via_process():
            return self._finalize_connection()
        self._connected = False
        return False

    def _connect_via_process(self):
        """通过扫描进程命令行参数获取连接信息 (优化: 仅匹配进程名后再读取 cmdline，避免大量无谓系统调用)"""
        try:
            for proc in psutil.process_iter(['name']):
                try:
                    p_name = proc.info.get('name')
                    if p_name and p_name.lower() == 'leagueclientux.exe':
                        cmdline = proc.cmdline()
                        if not cmdline:
                            continue
                        port = token = None
                        for arg in cmdline:
                            if '--app-port=' in arg:
                                port = arg.split('=', 1)[1]
                            elif '--remoting-auth-token=' in arg:
                                token = arg.split('=', 1)[1]
                        if port and token:
                            self.port = port
                            self.auth_token = token
                            self.base_url = f"https://127.0.0.1:{port}"
                            try:
                                proc_exe = proc.exe()
                                if proc_exe:
                                    self._cached_lockfile_path = os.path.join(os.path.dirname(proc_exe), 'lockfile')
                            except Exception:
                                pass
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            pass
        return False

    def _connect_via_lockfile(self):
        """通过读取 lockfile 获取连接信息 (优化: 优先检查缓存路径)"""
        paths_to_check = []
        if self._cached_lockfile_path:
            paths_to_check.append(self._cached_lockfile_path)
        paths_to_check.extend([os.path.join(p, 'lockfile') for p in COMMON_INSTALL_PATHS])

        for lockfile_path in paths_to_check:
            if os.path.exists(lockfile_path):
                try:
                    with open(lockfile_path, 'r') as f:
                        content = f.read().strip()
                    parts = content.split(':')
                    if len(parts) >= 5:
                        self.port = parts[2]
                        self.auth_token = parts[3]
                        self.base_url = f"https://127.0.0.1:{self.port}"
                        self._cached_lockfile_path = lockfile_path
                        return True
                except Exception:
                    continue
        return False

    def _finalize_connection(self):
        """连接成功后，配置长连接 Session + 构建英雄 ID 映射 + 缓存召唤师ID"""
        self._connected = True
        if self.auth_token:
            self._session.auth = ('riot', self.auth_token)
        self._build_champion_id_map()
        self._cache_summoner_id()
        print(f"   [OK] LCU connected (port: {self.port})")
        return True

    def _request(self, method, endpoint, **kwargs):
        """向 LCU API 发送请求 (复用长连接 Session)"""
        if not self.base_url or not self.auth_token:
            return None
        try:
            resp = self._session.request(
                method, f"{self.base_url}{endpoint}",
                timeout=self.LCU_TIMEOUT, **kwargs
            )
            return resp
        except requests.exceptions.ConnectionError:
            self._connected = False
            return None
        except Exception:
            return None

    def is_connected(self):
        return self._connected

    # ==========================================
    # 英雄 ID 映射 + 召唤师信息
    # ==========================================

    def _build_champion_id_map(self):
        """从 LCU API 获取英雄数据，构建 ID -> 中文名映射"""
        resp = self._request('GET', '/lol-game-data/assets/v1/champion-summary.json')
        if not resp or resp.status_code != 200:
            return
        try:
            champions = resp.json()
            for champ in champions:
                cid = champ.get('id')
                alias = champ.get('alias', '')
                if cid is None or cid == -1:
                    continue
                cn_name = self.en_to_cn.get(alias.lower())
                if cn_name:
                    self.id_to_cn[cid] = cn_name
            print(f"   [OK] ID map: {len(self.id_to_cn)} champions")
        except Exception:
            pass

    def _cache_summoner_id(self):
        """缓存当前登录玩家的召唤师 ID"""
        resp = self._request('GET', '/lol-summoner/v1/current-summoner')
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                self._summoner_id = data.get('summonerId')
            except Exception:
                pass

    # ==========================================
    # 核心: 获取当前阶段 + 获取英雄
    # ==========================================

    def get_gameflow_phase(self):
        """
        获取当前 gameflow 阶段。

        Returns:
            str | None: "None", "Lobby", "ChampSelect", "GameStart",
                        "InProgress", "WaitingForStats", etc.
        """
        if not self._connected:
            return None
        resp = self._request('GET', '/lol-gameflow/v1/gameflow-phase')
        if resp and resp.status_code == 200:
            try:
                return resp.json()  # 返回字符串，如 "ChampSelect"
            except Exception:
                pass
        return None

    def get_champ_select_champion(self):
        """
        选人阶段获取英雄 (ChampSelect)。
        通过 /lol-champ-select/v1/session 接口。
        """
        if not self._connected:
            return None
        resp = self._request('GET', '/lol-champ-select/v1/session')
        if not resp or resp.status_code != 200:
            return None
        try:
            data = resp.json()
            local_cell_id = data.get('localPlayerCellId')
            if local_cell_id is None:
                return None
            for player in data.get('myTeam', []):
                if player.get('cellId') == local_cell_id:
                    cid = player.get('championId', 0)
                    if cid and cid > 0:
                        return self.id_to_cn.get(cid)
        except Exception:
            pass
        return None

    def get_gameflow_champion(self):
        """
        加载/游戏阶段获取英雄 (InProgress/GameStart)。
        通过 /lol-gameflow/v1/session 的 gameData 字段。
        """
        if not self._connected:
            return None
        resp = self._request('GET', '/lol-gameflow/v1/session')
        if not resp or resp.status_code != 200:
            return None
        try:
            data = resp.json()
            game_data = data.get('gameData', {})

            # 在 teamOne 和 teamTwo 中查找自己的 summonerId
            all_players = game_data.get('teamOne', []) + game_data.get('teamTwo', [])
            for player in all_players:
                if player.get('summonerId') == self._summoner_id:
                    cid = player.get('championId', 0)
                    if cid and cid > 0:
                        return self.id_to_cn.get(cid)

            # 如果 summonerId 匹配失败，尝试用 playerChampionSelections
            selections = game_data.get('playerChampionSelections', [])
            if selections and self._summoner_id:
                for sel in selections:
                    if sel.get('summonerId') == self._summoner_id:
                        cid = sel.get('championId', 0)
                        if cid and cid > 0:
                            return self.id_to_cn.get(cid)

        except Exception:
            pass
        return None

    def get_ingame_champion(self):
        """
        通过 Live Client Data API 获取游戏内英雄 (端口 2999, 免密)。
        仅在游戏进行中（Loading 结束后）可用。
        """
        try:
            resp = requests.get(
                'https://127.0.0.1:2999/liveclientdata/activeplayer',
                verify=False, timeout=self.LIVE_API_TIMEOUT
            )
            if resp.status_code == 200:
                en_name = resp.json().get('championName', '')
                if en_name:
                    cn = self.en_to_cn.get(en_name.lower())
                    if not cn:
                        cn = self.en_to_cn.get(en_name.replace(' ', '').lower())
                    return cn
        except requests.exceptions.ConnectionError:
            pass
        except Exception:
            pass
        return None

    # ==========================================
    # 统一接口: 自动检测英雄 (全阶段)
    # ==========================================

    def get_champion_auto(self):
        """
        全生命周期自动获取当前英雄。

        按优先级依次尝试:
          1. ChampSelect  -> /lol-champ-select/v1/session
          2. InProgress   -> /lol-gameflow/v1/session (gameData)
          3. Live API     -> 127.0.0.1:2999 (免密, 游戏内)

        Returns:
            (str | None, str): (英雄中文名, 数据来源)
        """
        # 确保连接
        if not self._connected:
            if not self.connect():
                # LCU 不可用，只尝试 Live API
                hero = self.get_ingame_champion()
                return (hero, "Live API") if hero else (None, "")

        # 获取当前阶段
        phase = self.get_gameflow_phase()

        # 策略1: ChampSelect 阶段
        if phase == "ChampSelect":
            hero = self.get_champ_select_champion()
            if hero:
                return hero, "ChampSelect"

        # 策略2: InProgress / GameStart 阶段
        if phase in ("InProgress", "GameStart"):
            hero = self.get_gameflow_champion()
            if hero:
                return hero, "GameFlow"
            # 备选: Live Client Data API
            hero = self.get_ingame_champion()
            if hero:
                return hero, "Live API"

        # 策略3: 其他阶段也尝试 champ-select (以防 phase 查询延迟)
        if phase not in ("None", "Lobby", "Matchmaking", "EndOfGame", "WaitingForStats"):
            hero = self.get_champ_select_champion()
            if hero:
                return hero, "ChampSelect"

        return None, phase or ""

    # ==========================================
    # WebSocket 实时事件监听 (0.00% CPU 事件驱动)
    # ==========================================

    def start_ws_listener(self, on_hero_changed=None, on_phase_changed=None, on_hero_reset=None):
        """
        启动后台 LCU WebSocket 事件监听 (0.00% CPU 模式)。
        
        Args:
            on_hero_changed: 回调函数 (hero_name: str, source: str) -> None
            on_phase_changed: 回调函数 (phase: str) -> None
            on_hero_reset: 回调函数 () -> None
        """
        if on_hero_changed:
            self.on_hero_changed = on_hero_changed
        if on_phase_changed:
            self.on_phase_changed = on_phase_changed
        if on_hero_reset:
            self.on_hero_reset = on_hero_reset

        if self._ws_running:
            return

        self._ws_running = True
        self._ws_thread = threading.Thread(target=self._ws_worker, daemon=True, name="LCU-WebSocket")
        self._ws_thread.start()

    def stop_ws_listener(self):
        """停止后台 WebSocket 监听"""
        self._ws_running = False
        if self._ws_app:
            try:
                self._ws_app.close()
            except Exception:
                pass
            self._ws_app = None

    def _ws_worker(self):
        """WebSocket 监听后台工作线程 (0.00% CPU)"""
        while self._ws_running:
            # 1. 确保基础连接
            if not self._connected or not self.port or not self.auth_token:
                if not self.connect():
                    # 客户端未启动，每 3 秒检测一次 lockfile (耗时 0.001ms，0 CPU)
                    for _ in range(6):
                        if not self._ws_running:
                            return
                        time.sleep(0.5)
                    continue

            # 2. 尝试立即获取一次当前英雄 (覆盖启动时已在选人或对局中的情况)
            try:
                cur_hero, src = self.get_champion_auto()
                if cur_hero and self.on_hero_changed:
                    self.on_hero_changed(cur_hero, src)
            except Exception:
                pass

            # 3. 启动 WebSocket 事件驱动
            if HAS_WEBSOCKET:
                ws_url = f"wss://127.0.0.1:{self.port}/"
                auth_str = base64.b64encode(f"riot:{self.auth_token}".encode()).decode()
                headers = {"Authorization": f"Basic {auth_str}"}

                def on_open(ws):
                    try:
                        # 订阅选人事件 (选人、掷骰子、板凳席互换实时触发)
                        ws.send(json.dumps([5, "OnJsonApiEvent_lol_champ_select_v1_session"]))
                        # 订阅游戏流程阶段事件 (ChampSelect, InProgress, EndOfGame 等)
                        ws.send(json.dumps([5, "OnJsonApiEvent_lol_gameflow_v1_gameflow_phase"]))
                        print("   [OK] LCU WebSocket 实时监听已建立 (0.00% CPU 事件推送模式)")
                    except Exception as err:
                        print(f"   [WARN] WebSocket 订阅指令发送失败: {err}")

                def on_message(ws, msg):
                    self._handle_ws_event(msg)

                def on_error(ws, err):
                    pass

                def on_close(ws, close_status_code, close_msg):
                    self._connected = False

                self._ws_app = websocket.WebSocketApp(
                    ws_url,
                    header=headers,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close
                )

                try:
                    # 运行 WebSocket 阻塞监听，内部使用操作系统内核 select/poll 挂起，0 CPU 占用
                    self._ws_app.run_forever(
                        sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
                        ping_interval=15,
                        ping_timeout=10
                    )
                except Exception:
                    pass

                time.sleep(2)
            else:
                self._adaptive_polling_fallback()

    def _handle_ws_event(self, raw_message):
        """解析并处理 Riot WAMP 协议推送事件"""
        if not raw_message:
            return
        try:
            event_data = json.loads(raw_message)
            if not isinstance(event_data, list) or len(event_data) < 3:
                return

            opcode, event_name, payload = event_data[0], event_data[1], event_data[2]
            if opcode != 8 or not isinstance(payload, dict):
                return

            uri = payload.get("uri", "")
            data = payload.get("data")

            # 1. 选人阶段实时事件 (包括选人、掷骰子、板凳席互换)
            if "champ-select" in uri or "champ_select" in event_name:
                if isinstance(data, dict):
                    local_cell_id = data.get("localPlayerCellId")
                    if local_cell_id is not None:
                        for player in data.get("myTeam", []):
                            if player.get("cellId") == local_cell_id:
                                cid = player.get("championId", 0)
                                if cid and cid > 0:
                                    if len(self.id_to_cn) == 0:
                                        self._build_champion_id_map()
                                    hero = self.id_to_cn.get(cid)
                                    if hero and self.on_hero_changed:
                                        self.on_hero_changed(hero, "选人界面(实时推送)")
                                break

            # 2. 游戏阶段流转事件
            elif "gameflow-phase" in uri or "gameflow" in event_name:
                phase = data if isinstance(data, str) else ""
                if phase and self.on_phase_changed:
                    self.on_phase_changed(phase)

                if phase == "ChampSelect":
                    hero = self.get_champ_select_champion()
                    if hero and self.on_hero_changed:
                        self.on_hero_changed(hero, "选人阶段")

                elif phase in ("InProgress", "GameStart"):
                    # 载入游戏，尝试从 gameflow 或 Live API (2999) 提取
                    hero = self.get_gameflow_champion() or self.get_ingame_champion()
                    if hero and self.on_hero_changed:
                        self.on_hero_changed(hero, "对局内")

                elif phase in ("EndOfGame", "PreEndOfGame", "WaitingForStats"):
                    # 本局结束，通知重置
                    if self.on_hero_reset:
                        self.on_hero_reset()

                elif phase in ("None", "Lobby", "Matchmaking"):
                    # 回到大厅
                    if self.on_hero_reset:
                        self.on_hero_reset()

        except Exception:
            pass

    def _adaptive_polling_fallback(self):
        """降级兜底: 自适应低频休眠轮询 (~0.01% CPU)"""
        phase = self.get_gameflow_phase()
        if phase == "ChampSelect":
            hero = self.get_champ_select_champion()
            if hero and self.on_hero_changed:
                self.on_hero_changed(hero, "选人阶段(轮询)")
            time.sleep(1.5)
        elif phase in ("InProgress", "GameStart"):
            hero = self.get_gameflow_champion() or self.get_ingame_champion()
            if hero and self.on_hero_changed:
                self.on_hero_changed(hero, "对局内(轮询)")
            time.sleep(10)
        elif phase in ("EndOfGame", "PreEndOfGame", "None", "Lobby"):
            if self.on_hero_reset:
                self.on_hero_reset()
            time.sleep(5)
        else:
            time.sleep(3)
