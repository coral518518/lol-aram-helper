import json, csv, re, io, sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open('data/all_augments.json', 'r', encoding='utf-8') as f:
    all_augs = json.load(f)

with open('data/hero_augments.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    next(reader)
    hero_aug_keys = defaultdict(set)
    for r in reader:
        if r and len(r) >= 6:
            hero_aug_keys[r[0]].add(r[5])

norm = lambda s: re.sub(r'[\s\.,\/#!$%\^&\*;:{}=\-_`~()（）！!：:，·]+', '', s)

# 找出所有规范化相同但字面不同的海克斯
diff_pairs = set()
for hero, augs in hero_aug_keys.items():
    for aug in augs:
        for std_name in all_augs:
            if aug != std_name and norm(aug) == norm(std_name):
                diff_pairs.add((aug, std_name))

print(f"发现字面微小差异的海克斯对 (共 {len(diff_pairs)} 组):")
for aug, std in diff_pairs:
    print(f"  CSV中: {repr(aug)}  <--->  真理库中: {repr(std)}")

