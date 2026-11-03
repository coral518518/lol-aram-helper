import csv, json, io, sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open('data/hero_augments.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    next(reader)
    aug_hero_count = defaultdict(int)
    for r in reader:
        if r and len(r) >= 6:
            aug_hero_count[r[5]] += 1

with open('data/all_augments.json', 'r', encoding='utf-8') as f:
    all_augs = json.load(f)

print(f"Total champions: 173")
print(f"Total augments in truth table: {len(all_augs)}")
print(f"Augments appearing in 0 heroes: {len([n for n in all_augs if n not in aug_hero_count])}")

low_heroes = [(name, count) for name, count in aug_hero_count.items() if count < 60]
print(f"Augments appearing in < 60 heroes: {len(low_heroes)} 个")
for name, c in sorted(low_heroes, key=lambda x: x[1])[:25]:
    print(f"  - {name}: 仅出现在 {c} 个英雄中 (品质: {all_augs.get(name, {}).get('tier', '未知')})")

