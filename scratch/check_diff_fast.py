import json, csv, re, io, sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open('data/all_augments.json', 'r', encoding='utf-8') as f:
    all_augs = json.load(f)

norm = lambda s: re.sub(r'[\s\.,\/#!$%\^&\*;:{}=\-_`~()（）！!：:，·]+', '', s)
std_norm_map = {norm(name): name for name in all_augs}

with open('data/hero_augments.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    next(reader)
    all_csv_names = set(r[5] for r in reader if r and len(r) >= 6)

diff_pairs = []
for csv_name in all_csv_names:
    n = norm(csv_name)
    if n in std_norm_map:
        std_name = std_norm_map[n]
        if csv_name != std_name:
            diff_pairs.append((csv_name, std_name))

print(f"发现字面微小差异的海克斯对 (共 {len(diff_pairs)} 组):")
for aug, std in diff_pairs:
    print(f"  CSV中: {repr(aug)}  <--->  真理库中: {repr(std)}")

