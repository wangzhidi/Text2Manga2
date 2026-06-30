import json
import re
import os, glob

def extract_unique_tags(json_file):
    # 读取json文件
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 用集合去重
    unique_tags = set()

    for item in data:
        main_tags = item.get("main_tags", "")
        character_tags = item.get("character_tags", [])
        
        # 匹配中括号里的内容
        matches = re.findall(r"<(.*?)>", main_tags)
        unique_tags.update(matches)

        for character_tag in character_tags:
            matches = re.findall(r"<(.*?)>", character_tag)
            unique_tags.update(matches)
        unique_tags.update(matches)

    # 打印结果
    for tag in sorted(unique_tags):
        print(tag)

if __name__ == "__main__":
    dir_path = os.path.join("script", "000")
    for json_file in glob.glob(os.path.join(dir_path, "*.json")):
        extract_unique_tags(json_file)

