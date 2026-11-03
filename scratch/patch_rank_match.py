with open('main.py', 'r', encoding='utf-8') as f:
    c = f.read()

target = """                # 命中英雄专属胜率数据
                if match_name in hero_augments:
                    info = hero_augments[match_name]
                    tier = info.get('tier', '?')
                    t_rank = info.get('t_rank', 999)
                    overall_rank = info.get('overall_rank', 999)
                    res["text"] = "【" + match_name + "】\\n总No." + str(overall_rank) + " | " + str(tier) + " No." + str(t_rank)
                    res["valid"] = True
                    res["tier"] = tier
                    res["t_rank"] = t_rank
                    res["overall_rank"] = overall_rank
                else:
                    # 全局收录但该英雄暂未上榜（冷门/新海克斯）：使用全局品质兜底展示，绝不报未识别！
                    std_info = self.dm.all_augments.get(match_name, {})
                    tier = std_info.get('tier', '未知')
                    res["text"] = "【" + match_name + "】\\n" + str(tier) + " | 通用/暂无排名"
                    res["valid"] = True
                    res["tier"] = tier
                    res["t_rank"] = 999
                    res["overall_rank"] = 500  # 通用默认居中推荐"""

replacement = """                # 命中英雄专属胜率数据 (支持精确匹配 + 标点归一化匹配)
                info = hero_augments.get(match_name)
                if not info:
                    norm_m = re.sub(r'[\\s\\.,\\/#!$%\\^&\\*;:{}=\\-_`~()（）！!：:，·]+', '', match_name)
                    for k, v in hero_augments.items():
                        if re.sub(r'[\\s\\.,\\/#!$%\\^&\\*;:{}=\\-_`~()（）！!：:，·]+', '', k) == norm_m:
                            info = v
                            break

                if info:
                    tier = info.get('tier', '?')
                    t_rank = info.get('t_rank', 999)
                    overall_rank = info.get('overall_rank', 999)
                    res["text"] = "【" + match_name + "】\\n总No." + str(overall_rank) + " | " + str(tier) + " No." + str(t_rank)
                    res["valid"] = True
                    res["tier"] = tier
                    res["t_rank"] = t_rank
                    res["overall_rank"] = overall_rank
                else:
                    # 全局收录但该英雄暂未上榜（冷门/非专属海克斯）：使用全局品质兜底展示
                    std_info = self.dm.all_augments.get(match_name, {})
                    tier = std_info.get('tier', '未知')
                    res["text"] = "【" + match_name + "】\\n" + str(tier) + " | 冷门/无排名"
                    res["valid"] = True
                    res["tier"] = tier
                    res["t_rank"] = 999
                    res["overall_rank"] = 500  # 通用默认居中推荐"""

assert target in c, "target block not found in main.py"
c = c.replace(target, replacement, 1)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(c)

print("main.py updated successfully with punctuation normalization!")

