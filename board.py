import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from json_repair import repair_json
from key_pool import acquire_api_key, release_api_key

from data import replace_tags_prompt, replace_tags_speaker, replace_tags_character, get_character_map

load_dotenv(override=True) 
BASE_PATH = Path(__file__).parent
BOOKS_PATH = BASE_PATH / "books"
SCRIPT_PATH = BASE_PATH / "script"


def generate_storyboard(book_id: str = "000", chapter: str = None, custom_data: dict | None = None):
    """
    根据小说文本生成分镜脚本
    
    Args:
        book_id: 书籍ID (默认: 000)
        chapter: 特定章节ID (可选)
    """
    prefix = open(BASE_PATH / "prefix.txt", encoding="utf-8").read()
    prompt = open(BASE_PATH / "prompt.txt", encoding="utf-8").read()
    
    llm_api_url = os.getenv("GEMINI_API_URL")
    print(f"使用 API URL: {llm_api_url}")
    book_dir = BOOKS_PATH / str(book_id)
    
    if chapter:
        files = sorted(book_dir.glob(f"*{chapter}*.txt"))
    else:
        files = sorted(book_dir.glob("*.txt"))
    
    print(f"处理 book_id={book_id}")
    print("-" * 50)
    active_character_map = get_character_map(custom_data)

    def has_id_minus_one(obj):
        if isinstance(obj, dict):
            if obj.get("id") == -1:
                return True
            return any(has_id_minus_one(v) for v in obj.values())
        if isinstance(obj, list):
            return any(has_id_minus_one(item) for item in obj)
        return False

    def build_meta_block_from_novel(novel_file: Path):
        raw_text = novel_file.read_text(encoding="utf-8")
        non_empty_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        if len(non_empty_lines) < 2:
            raise ValueError("小说文本不足两行，无法解析 title / author,platform,url")

        title = non_empty_lines[0]
        second_line = non_empty_lines[1]

        raw_parts = re.split(r"[，,]\s*", second_line)
        original_author = raw_parts[0].strip() if len(raw_parts) > 0 else ""
        platform = raw_parts[1].strip() if len(raw_parts) > 1 else ""
        url = raw_parts[2].strip() if len(raw_parts) > 2 else ""

        return {
            "id": -1,
            "original_author": original_author,
            "platform": platform,
            "title": title,
            "url": url,
        }
    
    for file_path in files:
        chapter_name = file_path.stem
        print(f"\n处理章节: {chapter_name}")
        
        output_dir = SCRIPT_PATH / str(book_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{chapter_name}.json"

        if output_file.exists():
            print(f"  ✓ 已存在，跳过 LLM: {output_file}")
            try:
                storyboard_data = json.loads(output_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  ! 读取已存在的 JSON 失败，跳过该章节: {e}")
                continue
        else:
            chapter_content = file_path.read_text(encoding="utf-8")
            chapter_lines = chapter_content.splitlines()
            prompt_lines = []
            for index, line in enumerate(chapter_lines):
                if index == 1:
                    continue
                if index == 0:
                    prompt_lines.append(f"文章标题：{line}")
                else:
                    prompt_lines.append(line)
            chapter_content = "\n".join(prompt_lines)
            user_content = f"{prefix}\n\n{chapter_content}"

            llm_key = acquire_api_key("LLM_API_KEY")
            if not llm_key:
                raise RuntimeError("暂无可用 LLM KEY，当前请求排队中，请稍后重试")

            client = OpenAI(
                api_key=llm_key,
                base_url=llm_api_url,
            )

            masked_key = f"{llm_key[:6]}...{llm_key[-4:]}" if len(llm_key) > 10 else "***"
            print(f"使用 LLM API Key: {masked_key}")
            
            try:
                response = client.chat.completions.create(
                    # model="gemini-3.1-pro",
                    model="claude-opus-4-6",
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_content},
                    ],
                )
            finally:
                release_api_key("LLM_API_KEY", llm_key)

            content = response.choices[0].message.content.strip()

            
            
            try:

                # 如果内容被 ```json ... ``` 包裹，先提取
                json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL | re.IGNORECASE)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    # 回退：提取首个 JSON 对象/数组
                    json_match = re.search(r'(\[.*\]|\{.*\})', content, re.DOTALL)
                    json_str = json_match.group(1) if json_match else content

                storyboard_data = json.loads(json_str)
            except json.JSONDecodeError as e:
                print(f"  ⚠ 标准 JSON 解析失败: {e}，尝试自动修复...")
                try:
                    storyboard_data = json.loads(repair_json(json_str))
                    print("  ✓ JSON 自动修复成功")
                except Exception as e2:
                    print(f"  ❌ 分镜生成失败: {e2}")
                    _lines = json_str.splitlines()
                    _err_line = getattr(e2, 'lineno', None) or getattr(e, 'lineno', None)
                    if _err_line:
                        _start = max(0, _err_line - 51)
                        _end = min(len(_lines), _err_line + 50)
                        print(f"  --- 报错行附近内容 (行 {_start+1}~{_end}) ---")
                        for i, l in enumerate(_lines[_start:_end], start=_start+1):
                            marker = " >>>" if i == _err_line else "    "
                            print(f"  {marker} {i:4d} | {l}")
                    else:
                        print(f"  --- 原始响应内容 ---")
                        print(json_str)
                    print(f"  --------------------------")
                    continue
            except Exception as e:
                print(f"  ❌ 分镜生成失败: {e}")
                _lines = json_str.splitlines() if 'json_str' in dir() else content.splitlines()
                _err_line = getattr(e, 'lineno', None)
                if _err_line:
                    _start = max(0, _err_line - 51)
                    _end = min(len(_lines), _err_line + 50)
                    print(f"  --- 报错行附近内容 (行 {_start+1}~{_end}) ---")
                    for i, l in enumerate(_lines[_start:_end], start=_start+1):
                        marker = " >>>" if i == _err_line else "    "
                        print(f"  {marker} {i:4d} | {l}")
                else:
                    print(f"  --- 原始响应内容 ---")
                    print(content)
                print(f"  --------------------------")
                continue
            
            output_file.write_text(json.dumps(storyboard_data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ 已保存: {output_file}")

        # 自定义全局替换：将字典中的键替换为值（会替换所有相符子串）
        custom_replacements = {
            "pout": "",
            "puffed cheeks": "",
            "screaming": "open mouth",
            "shouting": "",
            "blood": "",
            

        }

        replacement_stats = {k: 0 for k in custom_replacements}

        def apply_custom_replacements(obj):
            if isinstance(obj, str):
                for old, new in custom_replacements.items():
                    hit_count = obj.count(old)
                    if hit_count:
                        replacement_stats[old] += hit_count
                    obj = obj.replace(old, new)
                return obj
            if isinstance(obj, list):
                return [apply_custom_replacements(item) for item in obj]
            if isinstance(obj, dict):
                return {k: apply_custom_replacements(v) for k, v in obj.items()}
            return obj

        if custom_replacements:
            storyboard_data = apply_custom_replacements(storyboard_data)
            replaced_items = [
                f'    - {old}->{json.dumps(custom_replacements[old], ensure_ascii=False)}: {count}处'
                for old, count in replacement_stats.items()
                if count > 0
            ]
            if replaced_items:
                print("后处理替换内容:")
                for line in replaced_items:
                    print(line)
            else:
                print("后处理未发现需要替换的内容")

        # 保存后处理: 替换 speaker/main_tags/character_tags 中尖括号的占位符
        missing_characters = set()
        allowed_orientations = {"portrait", "square", "landscape"}

        def collect_missing_tags(text: str):
            for key in re.findall(r"<([^>]+)>", text):
                lookup_key = key.replace("_", " ")
                if lookup_key not in active_character_map:
                    missing_characters.add(lookup_key)

        def ensure_list_fields(obj):
            if isinstance(obj, dict):
                for k in ["text", "text_type", "speaker"]:
                    if k in obj and not isinstance(obj[k], list):
                        obj[k] = [obj[k]]
                for v in obj.values():
                    ensure_list_fields(v)
            elif isinstance(obj, list):
                for item in obj:
                    ensure_list_fields(item)

        def process_obj(obj):
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    if k == "speaker":
                        if isinstance(v, list):
                            for x in v:
                                if isinstance(x, str):
                                    collect_missing_tags(x)
                            obj[k] = [replace_tags_speaker(x, active_character_map) if isinstance(x, str) else x for x in v]
                        elif isinstance(v, str):
                            collect_missing_tags(v)
                            obj[k] = replace_tags_speaker(v, active_character_map)
                    elif k == "main_tags":
                        if isinstance(v, str):
                            collect_missing_tags(v)
                            obj[k] = replace_tags_prompt(v, active_character_map)
                    elif k == "character_tags":
                        if isinstance(v, list):
                            new_v = []
                            for x in v:
                                if isinstance(x, str):
                                    collect_missing_tags(x)
                                    tag, ref = replace_tags_character(x, active_character_map)
                                    new_v.append(tag)
                                    if ref:
                                        ref_list = obj.get("reference")
                                        if not isinstance(ref_list, list):
                                            ref_list = []
                                        if ref not in ref_list:
                                            ref_list.append(ref)
                                        obj["reference"] = ref_list
                                else:
                                    new_v.append(x)
                            obj[k] = new_v
                        elif isinstance(v, str):
                            collect_missing_tags(v)
                            tag, ref = replace_tags_character(v, active_character_map)
                            obj[k] = tag
                            if ref:
                                ref_list = obj.get("reference")
                                if not isinstance(ref_list, list):
                                    ref_list = []
                                if ref not in ref_list:
                                    ref_list.append(ref)
                                obj["reference"] = ref_list
                    elif k == "orientation":
                        if v not in allowed_orientations:
                            obj[k] = "portrait"
                    else:
                        process_obj(v)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    if isinstance(item, (dict, list)):
                        process_obj(item)

        try:
            ensure_list_fields(storyboard_data)
            process_obj(storyboard_data)

            # 新增后处理：若不存在 id=-1 的块，则根据原小说前两行插入到最前方
            if not has_id_minus_one(storyboard_data):
                try:
                    meta_block = build_meta_block_from_novel(file_path)
                    if isinstance(storyboard_data, list):
                        storyboard_data.insert(0, meta_block)
                        print("  ✓ 已插入 id=-1 元数据块")
                    else:
                        raise TypeError("当前 JSON 顶层不是列表，无法在最前方插入 id=-1 块")
                except Exception as e:
                    print(f"  ! 插入 id=-1 元数据块失败: {e}")

            output_file.write_text(json.dumps(storyboard_data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  ✓ 已更新替换字段并保存: {output_file}")
            if missing_characters:
                missing_list = "、".join(sorted(missing_characters))
                print(f"  ! 未找到的角色，请补充到 character_map: {missing_list}")
        except Exception as e:
            print(f"  ! 替换字段时出错: {e}")
    
    print("\n" + "-" * 50)
    print("完成！")


def reprocess_characters(book_id: str, chapter: str, custom_data: dict | None = None) -> list:
    """
    对已有剧本 JSON 重新执行角色标签替换，不调用 LLM。
    返回仍未找到的角色名列表（已排序）。
    可通过 custom_data 传入用户自定义角色（不会修改全局状态）。
    """
    from data import replace_tags_prompt, replace_tags_speaker, replace_tags_character, get_character_map

    active_character_map = get_character_map(custom_data)

    output_file = SCRIPT_PATH / str(book_id) / f"{chapter}.json"
    if not output_file.exists():
        raise FileNotFoundError(f"剧本文件不存在: {output_file}")

    storyboard_data = json.loads(output_file.read_text(encoding="utf-8"))

    missing_characters: set = set()
    allowed_orientations = {"portrait", "square", "landscape"}

    def collect_missing_tags(text: str):
        for key in re.findall(r"<([^>]+)>", text):
            lookup_key = key.replace("_", " ")
            if lookup_key not in active_character_map:
                missing_characters.add(lookup_key)

    def ensure_list_fields(obj):
        if isinstance(obj, dict):
            for k in ["text", "text_type", "speaker"]:
                if k in obj and not isinstance(obj[k], list):
                    obj[k] = [obj[k]]
            for v in obj.values():
                ensure_list_fields(v)
        elif isinstance(obj, list):
            for item in obj:
                ensure_list_fields(item)

    def process_obj(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k == "speaker":
                    if isinstance(v, list):
                        for x in v:
                            if isinstance(x, str):
                                collect_missing_tags(x)
                        obj[k] = [replace_tags_speaker(x, active_character_map) if isinstance(x, str) else x for x in v]
                    elif isinstance(v, str):
                        collect_missing_tags(v)
                        obj[k] = replace_tags_speaker(v, active_character_map)
                elif k == "main_tags":
                    if isinstance(v, str):
                        collect_missing_tags(v)
                        obj[k] = replace_tags_prompt(v, active_character_map)
                elif k == "character_tags":
                    if isinstance(v, list):
                        new_v = []
                        for x in v:
                            if isinstance(x, str):
                                collect_missing_tags(x)
                                tag, ref = replace_tags_character(x, active_character_map)
                                new_v.append(tag)
                                if ref:
                                    ref_list = obj.get("reference")
                                    if not isinstance(ref_list, list):
                                        ref_list = []
                                    if ref not in ref_list:
                                        ref_list.append(ref)
                                    obj["reference"] = ref_list
                            else:
                                new_v.append(x)
                        obj[k] = new_v
                    elif isinstance(v, str):
                        collect_missing_tags(v)
                        tag, ref = replace_tags_character(v, active_character_map)
                        obj[k] = tag
                        if ref:
                            ref_list = obj.get("reference")
                            if not isinstance(ref_list, list):
                                ref_list = []
                            if ref not in ref_list:
                                ref_list.append(ref)
                            obj["reference"] = ref_list
                elif k == "orientation":
                    if v not in allowed_orientations:
                        obj[k] = "portrait"
                else:
                    process_obj(v)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, (dict, list)):
                    process_obj(item)

    ensure_list_fields(storyboard_data)
    process_obj(storyboard_data)

    output_file.write_text(json.dumps(storyboard_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 角色标签重新处理完成: {output_file}")
    remaining = sorted(missing_characters)
    if remaining:
        print(f"  ! 仍有未找到的角色: {'、'.join(remaining)}")
    return remaining


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="根据小说文本生成分镜脚本")
    parser.add_argument("book_id", nargs="?", default="001", help="书籍ID (默认: 000)")
    parser.add_argument("-c", "--chapter", help="特定章节ID (可选)", default=None)
    
    args = parser.parse_args()
    
    generate_storyboard(book_id=args.book_id, chapter=args.chapter)


if __name__ == "__main__":
    main()
