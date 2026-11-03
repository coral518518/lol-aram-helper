"""
ARAM 海克斯数据抓取引擎 (权威源 OP.GG 原生结构化数据流)
高性能免浏览器版本：基于 HTTP/Next.js 数据流解析，无需 Chrome 与 Selenium
"""

import sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import time
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 官方/OP.GG 固有阶级掩码映射 (1=白银, 4=黄金, 8=棱彩)
RARITY_MAP = {
    1: "白银",
    4: "黄金",
    8: "棱彩"
}

def scrape_single_champion(cn_name, en_name, session=None, timeout=12, max_retries=2):
    """
    单英雄抓取逻辑 (权威数据源: OP.GG 原生 Next.js RSC 数据流)
    
    返回: (valid_augments, status_code)
        valid_augments: [
            {"name": "...", "tier": "黄金", "overall_rank": 1, "t_rank": 1},
            ...
        ]
        status_code: "clean" | "empty" | "error"
    """
    url = f"https://op.gg/zh-cn/lol/modes/aram-mayhem/{en_name}/augments"
    http = session or requests

    for attempt in range(1, max_retries + 1):
        try:
            r = http.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 404:
                return [], "not_found"
            if r.status_code != 200:
                if attempt < max_retries:
                    time.sleep(1)
                    continue
                return [], f"http_{r.status_code}"

            text = r.content.decode('utf-8', errors='replace')

            # 匹配转义与未转义模式的完整海克斯对象
            pattern_escaped = r'\{\\"id\\":(\d+),\\"tier\\":(\d+),\\"performance\\":([0-9.]+),\\"popular\\":([0-9.]+),\\"name\\":\\"([^"\\]+)\\",\\"key\\":\\"([^"\\]+)\\",.*?\\"rarity\\":(\d+)'
            items = re.findall(pattern_escaped, text)

            if not items:
                pattern_raw = r'\{"id":(\d+),"tier":(\d+),"performance":([0-9.]+),"popular":([0-9.]+),"name":"([^"]+)","key":"([^"]+)",.*?"rarity":(\d+)'
                items = re.findall(pattern_raw, text)

            if not items:
                # 页面可能暂无该英雄数据
                return [], "empty"

            # 去重并以 performance (胜率评分) 降序排列
            parsed = []
            seen = set()
            for it in items:
                aug_id, op_tier, perf, pop, name, key, rarity = it
                if name in seen:
                    continue
                seen.add(name)
                tier_str = RARITY_MAP.get(int(rarity), "未知")
                parsed.append({
                    "name": name,
                    "tier": tier_str,
                    "performance": float(perf),
                    "popular": float(pop)
                })

            # 按 performance 从高到低排序，生成真实准确的排位
            parsed.sort(key=lambda x: x["performance"], reverse=True)

            tier_counters = {"白银": 1, "黄金": 1, "棱彩": 1, "未知": 1}
            final_list = []
            for overall_idx, aug in enumerate(parsed, 1):
                t = aug["tier"]
                t_idx = tier_counters.get(t, 1)
                tier_counters[t] = t_idx + 1
                final_list.append({
                    "name": aug["name"],
                    "tier": t,
                    "overall_rank": overall_idx,
                    "t_rank": t_idx
                })

            return final_list, "clean"

        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
                continue
            return [], "error"

    return [], "error"


def crawl_champions(target_list, early_stop_func=None, max_workers=6, log_func=None):
    """
    高并发极速抓取入口 (兼容旧版调用接口)
    
    Args:
        target_list: [(cn_name, en_name), ...]
        early_stop_func: 接收 (cn_name, crawled_data) 返回 bool，若返回 True 则提前终止抓取
        max_workers: 并发工作线程数 (默认 6)
        log_func: 日志回调函数
    
    Returns:
        (success_data, failed_list)
        success_data: {cn_name: [list of augments]}
        failed_list: [failed_cn_names]
    """
    _log = log_func or print
    total = len(target_list)
    _log(f"--- 开始极速拉取权威数据 (共 {total} 个英雄, {max_workers} 线程并发) ---")

    success_data = {}
    failed_list = []

    # 如果是单个英雄或抽样比对模式（长度 <= 5 且有 early_stop_func），使用串行方式以支持即时中断
    if early_stop_func and len(target_list) <= 5:
        with requests.Session() as session:
            for idx, (cn_name, en_name) in enumerate(target_list, 1):
                _log(f"[{idx}/{total}] 校验英雄: {cn_name} ({en_name})...")
                data, status = scrape_single_champion(cn_name, en_name, session=session)
                if status == "clean" and data:
                    success_data[cn_name] = data
                    _log(f"   > 成功抓取 {len(data)} 条数据")
                    if early_stop_func(cn_name, data):
                        _log(f"   > 提示: 触发抽样差异回调，提前结束抽样")
                        break
                else:
                    _log(f"   > 校验未成功 (状态: {status})")
                    failed_list.append(cn_name)
        return success_data, failed_list

    # 批量并发拉取模式
    completed_count = 0
    t0 = time.time()

    def _worker(item):
        cn, en = item
        with requests.Session() as s:
            data, status = scrape_single_champion(cn, en, session=s)
        return cn, en, data, status

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_worker, item): item for item in target_list}
        for future in as_completed(future_map):
            completed_count += 1
            cn, en = future_map[future]
            try:
                cn_name, en_name, data, status = future.result()
                if status == "clean" and data:
                    success_data[cn_name] = data
                    _log(f"[{completed_count}/{total}] [OK] {cn_name}: {len(data)} 条海克斯")
                    if early_stop_func and early_stop_func(cn_name, data):
                        _log("提示: 触发提前结束")
                        break
                else:
                    _log(f"[{completed_count}/{total}] [FAIL] {cn_name} 失败 (状态: {status})")
                    failed_list.append(cn_name)
            except Exception as e:
                _log(f"[{completed_count}/{total}] [ERROR] {cn} 异常: {e}")
                failed_list.append(cn)

    elapsed = time.time() - t0
    _log(f"--- 拉取完成: 成功 {len(success_data)} 个, 失败 {len(failed_list)} 个, 耗时 {elapsed:.1f}s ---")
    return success_data, failed_list


if __name__ == "__main__":
    t0 = time.time()
    res, fail = crawl_champions([("暗裔剑魔", "Aatrox"), ("寒冰射手", "Ashe")])
    print(f"耗时: {time.time() - t0:.2f}s")
    print(f"结果: {len(res)} 个英雄")
