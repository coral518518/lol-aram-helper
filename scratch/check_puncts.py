import csv, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open('data/hero_augments.csv', 'r', encoding='utf-8-sig') as f:
    r = csv.reader(f)
    next(r)
    p_names = set()
    for row in r:
        if row and len(row) >= 6:
            name = row[5]
            if any(p in name for p in '：:！!，, '):
                p_names.add(name)

print(f"带标点的海克斯共有 {len(p_names)} 个:")
for n in sorted(p_names):
    print("  -", repr(n))

