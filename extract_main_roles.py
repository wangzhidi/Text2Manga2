import argparse
import re
from pathlib import Path

# 预设排除标签（可按需补充）
EXCLUDED_TAGS = {
    "原神",
    "崩坏星穹铁道",
    "崩坏三",
    "明日方舟",
    "明日方舟终末地",
    "绝区零",
    "鸣潮",
    "莉可丽丝",
    "孤独摇滚",
    "AI生成",
    "同人漫画",
    "如是众生欢笑不已",
    "游戏破壁计划",
    "咕咕嘎嘎",
    "空荧",
    "哲铃",
    "符玄青雀",
    "夕瓜",
    "原神双子",
    "雌小鬼",
    "穹",
    "空",
    "原神空",
    "原神空月之歌",
    "崩铁流萤",
    "崩铁花火",
    "原神少女",
    "A生成",
    "爱可菲复刻",
    "魔女之旅依蕾娜",
    "原神荧",
    "崩坏星穹铁道白露",
    "崩坏星穹铁道克拉拉",
    "崩坏星穹铁道知更鸟",
    "all荧",
    "all空",
    "翁法罗斯",
    "雷电影",
    "崩坏星穹铁道星",
    "崩坏星穹铁道黑天鹅",
    "绝区零哲",
    "终末地管理员",
    "鸣潮卡提西娅",
    "这真的可以发吗",
    "德丽莎世界第一可爱",
    "阿米娅x博士",
    "绀海组",
    "绝区零铃",
    "布洛妮娅与希儿",
    "爻老板",
    "原神心海",
    "布洛妮娅扎伊切克",
    "井之上泷奈cos",
    "崩坏三舰长",
    "鸣潮绯雪",
    "浊心斯卡蒂",

}

# 含有以下关键词的标签会被排除
EXCLUDED_KEYWORDS = (
    "AI",
    "同人",
    "作者",
    "原文",
    "预警",
    "计划",
    "欢笑不已",
)

TAG_PATTERN = re.compile(r"#([^\s#。！，、,.!?？；;：:]+)")


def is_excluded(tag: str) -> bool:
    if tag in EXCLUDED_TAGS:
        return True
    return any(keyword in tag for keyword in EXCLUDED_KEYWORDS)


def extract_roles(line: str, max_roles: int = 2) -> list[str]:
    roles: list[str] = []
    for tag in TAG_PATTERN.findall(line):
        tag = tag.strip()
        if not tag or is_excluded(tag):
            continue
        if tag not in roles:
            roles.append(tag)
        if len(roles) >= max_roles:
            break
    return roles


def main() -> None:
    parser = argparse.ArgumentParser(description="从 names.txt 抽取每行前两个主要角色标签")
    parser.add_argument("input", nargs="?", default="names.txt", help="输入文件路径（默认 names.txt）")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"未找到输入文件: {input_path}")

    with input_path.open("r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                print("")
                continue
            roles = extract_roles(line)
            print(",".join(roles))


if __name__ == "__main__":
    main()
