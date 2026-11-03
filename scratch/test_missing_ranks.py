import sys
import io
import json
import re
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

url = 'https://op.gg/zh-cn/lol/modes/aram-mayhem/ezreal/augments'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9'
}

r = requests.get(url, headers=headers, timeout=10)
text = r.text

pattern_escaped = r'\{\\"id\\":(\d+),\\"tier\\":(\d+),\\"performance\\":([0-9.]+),\\"popular\\":([0-9.]+),\\"name\\":\\"([^"\\]+)\\",\\"key\\":\\"([^"\\]+)\\",.*?\\"rarity\\":(\d+)'
items = re.findall(pattern_escaped, text)
if not items:
    pattern_raw = r'\{"id":(\d+),"tier":(\d+),"performance":([0-9.]+),"popular":([0-9.]+),"name":"([^"]+)","key":"([^"]+)",.*?"rarity":(\d+)'
    items = re.findall(pattern_raw, text)

print(f"OP.GG Ezreal augments raw matches: {len(items)}")

with open('data/all_augments.json', 'r', encoding='utf-8') as f:
    all_augs = json.load(f)

op_names = set(it[4] for it in items)
missing = [name for name in all_augs if name not in op_names]

print(f"Total augments in truth table: {len(all_augs)}")
print(f"Ezreal has rank on OP.GG: {len(op_names)}")
print(f"Missing augments count: {len(missing)}")
print("\nEzreal 未上榜的海克斯抽样 (前 20 个):")
for m in missing[:20]:
    print(f"  - {m} (品质: {all_augs[m]['tier']})")
