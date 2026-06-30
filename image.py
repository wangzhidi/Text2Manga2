import json
import os
import time


import gc
from dotenv import load_dotenv
from tqdm import tqdm
from novelai import NovelAI
from novelai.types import GenerateImageParams, Character, CharacterReference
from data import *
import re
from pathlib import Path
from key_pool import acquire_api_key, release_api_key

# 加载环境变量
load_dotenv(override=True)


# =========================
# NovelAI API
# =========================
def create_Image(
    prompt: str,
    character_tags: list = None,
    orientation: str = "portrait",
    negative_prompt: str = "",
    character_references: list = None,
    seed: int = None,
) -> list:
    """
    使用NovelAI API生成图像
    
    Args:
        prompt: 主要描述词（已处理过的标签）
        character_tags: 角色标签列表（已处理过的标签）
        orientation: 图片方向 ("landscape" 或 "portrait")
        negative_prompt: 负面描述词
    
    Returns:
        包含生成图像的列表
    """

    if character_tags:
        print(f"Character Tags: {character_tags}")


    # 构建character对象列表
    characters = []
    if character_tags:
        for i, char_tag in enumerate(character_tags):
            char = Character(
                prompt=char_tag,
                enabled=True
            )
            characters.append(char)

    # 构建生成参数 (n_samples固定为1)
    params = GenerateImageParams(
        prompt=prompt,
        model=os.getenv("NOVELAI_MODEL", "nai-diffusion-4-5-full"),
        size=orientation,
        steps=int(os.getenv("NOVELAI_STEPS", 28)),
        scale=float(os.getenv("NOVELAI_SCALE", 5.0)),
        sampler=os.getenv("NOVELAI_SAMPLER", "k_euler_ancestral"),
        characters=characters if characters else None,
        character_references=character_references if character_references else None,
        n_samples=1,
        negative_prompt=negative_prompt if negative_prompt else None,
        seed=seed
    )

    na_key = acquire_api_key("NA_API_KEY")
    if not na_key:
        raise Exception("暂无可用 NA KEY，当前请求排队中，请稍后重试")

    try:
        client = NovelAI(api_key=na_key)
        images = client.image.generate(params)
        return images
    except Exception as e:
        raise Exception(f"NovelAI API 错误: {str(e)}")
    finally:
        release_api_key("NA_API_KEY", na_key)


def save_novelai_image(image_obj, save_path: str):
    """保存NovelAI返回的PIL Image对象"""
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    img = image_obj.convert("RGB") if getattr(image_obj, "mode", "RGB") != "RGB" else image_obj
    img.save(save_path, format="JPEG", quality=95, optimize=True)


def save_error_message(error_msg: str, save_path: str):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(error_msg)


# 每个位次的默认固定种子（可通过 NOVELAI_SEED_1, NOVELAI_SEED_2, ... 环境变量覆盖）
_DEFAULT_SEEDS = [123456789, 987654321, 111222333, 444555666, 222333444, 555666777, 888999000, 135792468]

def _get_position_seed(pos_index: int, random_seed: bool) -> int:
    """返回指定位次（0-based）的种子。random_seed=True 时返回随机整数。"""
    if random_seed:
        import random
        return random.randint(0, 2**32 - 1)
    # 尝试从环境变量读取，键名为 NOVELAI_SEED_1, NOVELAI_SEED_2, ...
    env_key = f"NOVELAI_SEED_{pos_index + 1}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    # 回退到内置默认列表
    if pos_index < len(_DEFAULT_SEEDS):
        return _DEFAULT_SEEDS[pos_index]
    # 超出列表范围时用位次衍生一个确定值
    return (pos_index + 1) * 111111111 % (2**32)


# =========================
# 单个 item 的完整任务
# =========================
def process_item(
    item,
    main_prompt,
    character_tags,
    orientation,
    image_paths,
    target_paths,
    error_path,
    character_references=None,
    json_name="",
    item_id="",
    random_seed=False,
):
    """
    处理单个 item，生成 len(image_paths) 张图片。

    Args:
        image_paths:  各张图片的绝对保存路径列表，决定生成数量
        target_paths: 各张图片的相对 URL 路径列表（写回 item["image_path"]）
        random_seed:  True = 使用随机种子（用于重新生成），False = 使用按位次固定种子
    """
    if not image_paths:
        return False, "image_paths 为空"

    retry = 0
    last_error = ""
    n = len(image_paths)

    # 读取 STYLE_POS 和 STYLE_NEG
    style_pos = os.getenv("STYLE_POS", "")
    style_neg = os.getenv("STYLE_NEG", "")
    print(f"Main Prompt: {main_prompt}")
    # 附加 STYLE_POS 到 main_prompt 后面
    if style_pos:
        main_prompt = f"{main_prompt},{style_pos}"

    while retry < 3:
        try:
            for idx in range(n):
                seed = _get_position_seed(idx, random_seed)
                try:
                    images = create_Image(
                        main_prompt,
                        character_tags,
                        orientation,
                        style_neg,
                        character_references,
                        seed=seed,
                    )
                    if not images or len(images) < 1:
                        raise Exception(f"第 {idx+1} 张图 API 返回空结果")
                    save_novelai_image(images[0], image_paths[idx])
                except Exception as e:
                    print(f"\n[错误] 章节: {json_name}, ID: {item_id}, 第 {idx+1} 张图生成失败: {e}")
                    raise e

            item["image_path"] = target_paths
            return True, ""

        except Exception as e:
            last_error = str(e)
            retry += 1
            if retry < 3:
                time.sleep(2)

    save_error_message(last_error, error_path)
    return False, last_error

# =========================
# 主流程
# =========================
def get_book_content(book_id: str, chapter: str = None):
    storyboard_dir = f"script/{book_id}"
    # 如果script/book_id目录不存在，使用script目录（兼容旧结构）
    if not os.path.isdir(storyboard_dir):
        storyboard_dir = "script"
    
    # 当 chapter 不为 None 时，仅处理指定章节
    if chapter:
        chapter_file = chapter if str(chapter).endswith(".json") else f"{chapter}.json"
        chapter_path = os.path.join(storyboard_dir, chapter_file)
        if not os.path.isfile(chapter_path):
            print(f"警告: 指定章节不存在，已跳过: {chapter_path}")
            return
        chapter_files = [chapter_file]
    else:
        # 处理全部章节
        if storyboard_dir == "script":
            chapter_files = ["000.json"]
        else:
            chapter_files = sorted(os.listdir(storyboard_dir))
    
    chapter_paths = [os.path.join(storyboard_dir, f) for f in chapter_files if f.endswith(".json")]

    # 统计总 item 数
    total_items = 0
    for path in chapter_paths:
        with open(path, "r", encoding="utf-8") as f:
            total_items += len(json.load(f))

    # 顺序执行（无线程池）
    with tqdm(total=total_items, desc="总进度", unit="组") as pbar:

        for chapter_path in chapter_paths:
            json_name = os.path.basename(chapter_path).split(".")[0]

            try:
                with open(chapter_path, "r", encoding="utf-8") as f:
                    chapter_data = json.load(f)

                for item in chapter_data:
                    item_id = item["id"]
                    
                    # 从main_tags获取主描述
                    main_prompt = item.get("main_tags", "")
                    
                    # 获取角色标签
                    character_tags = item.get("character_tags", [])

                    # 获取参考图
                    reference_list = item.get("reference")
                    character_references = None
                    if isinstance(reference_list, list) and reference_list:
                        character_references = [
                            CharacterReference(
                                image=ref_path,
                                type="character",
                                fidelity=1,
                                strength=1,
                            )
                            for ref_path in reference_list
                            if isinstance(ref_path, str) and ref_path
                        ]
                        if not character_references:
                            character_references = None
                    
                    # 清理尖括号
                    def remove_angle_brackets(text):
                        return re.sub(r'<([^>]+)>', r'\1', text)
                    
                    main_prompt = remove_angle_brackets(main_prompt)
                    character_tags = [remove_angle_brackets(tag) for tag in character_tags]
                    
                    # 获取方向（用于确定size）
                    orientation = item.get("orientation", "portrait")
                    
                    if not main_prompt:
                        print(f"警告: item {item_id} 缺少 main_tags")
                        pbar.update(1)
                        continue
                    
                    n_images = int(os.getenv("NOVELAI_N_IMAGES", 2))
                    base_dir = f"image/{book_id}/{json_name}"
                    err = os.path.join(base_dir, f"{item_id}.txt")

                    img_paths = [os.path.join(base_dir, f"{item_id}_{k+1}.jpg") for k in range(n_images)]
                    rel_paths = [f"image/{book_id}/{json_name}/{item_id}_{k+1}.jpg" for k in range(n_images)]

                    if all(os.path.exists(p) for p in img_paths):
                        if "image_path" not in item:
                            item["image_path"] = rel_paths
                        pbar.update(1)
                        continue

                    # 顺序执行 process_item
                    process_item(
                        item,
                        main_prompt,
                        character_tags,
                        orientation,
                        img_paths,
                        rel_paths,
                        err,
                        character_references,
                        json_name,
                        str(item_id),
                    )
                    pbar.update(1)

                with open(chapter_path, "w", encoding="utf-8") as f:
                    json.dump(chapter_data, f, ensure_ascii=False, indent=2)

                gc.collect()

            except Exception as e:
                print(f"\n[错误] 处理章节 {json_name} 时发生系统性异常: {e}")

def generate_images(book_id: str = "000", chapter: str = None):
    """
    为指定的分镜脚本生成图像
    
    Args:
        book_id: 书籍ID (默认: 000)
        chapter: 特定章节ID (可选)
    """
    get_book_content(book_id, chapter)


# =========================
# 入口
# =========================
if __name__ == "__main__":
    # 使用默认的000.json脚本
    generate_images("001")
