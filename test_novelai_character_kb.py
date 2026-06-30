"""
测试 NovelAI 角色知识库是否已更新
从 data.py 中提取所有有reference的角色，生成两张图片：
1. 仅使用 danbooru 角色tag (例如：furina (genshin impact))
2. 使用原本的角色描述 + 参考图像方式
"""

import os
import json
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv
from data import BASE_CHARACTER_MAP
from image import create_Image, save_novelai_image, save_error_message
from novelai.types import CharacterReference

# 加载环境变量
load_dotenv(override=True)


def get_characters_with_reference():
    """获取所有包含reference的角色（即列表长度为3的项）"""
    characters = {}
    for char_name, char_data in BASE_CHARACTER_MAP.items():
        if len(char_data) >= 3:  # 有reference
            characters[char_name] = char_data
    return characters


def extract_game_name(tag: str) -> str:
    """从tag中提取游戏名称，例如从 'furina (genshin impact)' 提取 'genshin impact'"""
    import re
    match = re.search(r'\((.*?)\)', tag)
    if match:
        return match.group(1)
    return "unknown"


def extract_character_name(tag: str) -> str:
    """从tag中提取角色名称，例如从 'furina (genshin impact)' 提取 'furina'"""
    import re
    match = re.search(r'^([^(]+)', tag)
    if match:
        return match.group(1).strip()
    return tag


def generate_test_images(output_base_dir: str = "novelai_test_out"):
    """生成所有测试图片"""
    
    import random
    
    # 从环境变量获取样式
    style_pos = os.getenv("STYLE_POS", "").strip()
    style_neg = os.getenv("STYLE_NEG", "").strip()
    
    print(f"使用样式 STYLE_POS: {style_pos[:50]}..." if len(style_pos) > 50 else f"使用样式 STYLE_POS: {style_pos}")
    print(f"使用样式 STYLE_NEG: {style_neg[:50]}..." if len(style_neg) > 50 else f"使用样式 STYLE_NEG: {style_neg}")
    
    characters = get_characters_with_reference()
    print(f"找到 {len(characters)} 个包含reference的角色\n")
    
    # 创建输出目录
    os.makedirs(output_base_dir, exist_ok=True)
    
    results = []
    
    for idx, (char_name, char_data) in enumerate(tqdm(characters.items(), desc="生成测试图片")):
        # 解析角色数据
        tag = char_data[0]  # danbooru tag, 例如 "furina (genshin impact)"
        description = char_data[1]  # 角色描述，例如 "girl, white hair"
        reference_path = char_data[2]  # 参考图像路径，例如 "reference\\genshin\\columbina.png"
        
        # 获取游戏名称
        game_name = extract_game_name(tag)
        
        # 创建角色目录
        char_dir = os.path.join(output_base_dir, char_name)
        os.makedirs(char_dir, exist_ok=True)
        
        # 生成随机种子
        random_seed = random.randint(0, 2147483647)
        
        # ========== 生成第一张图片：仅使用 danbooru tag ==========
        print(f"\n[{idx+1}/{len(characters)}] {char_name} - 生成第一张（tag only）")
        try:
            # tag 作为 character prompt，使用最小prompt避免API错误
            prompt_1 = "character" + style_pos
            images_1 = create_Image(
                prompt=prompt_1,
                character_tags=[tag],
                orientation="portrait",
                negative_prompt=style_neg,
                seed=random_seed
            )
            
            # 保存图片
            output_path_1 = os.path.join(char_dir, f"{char_name}_tag_only.jpg")
            save_novelai_image(images_1[0], output_path_1)
            print(f"  ✓ 已保存: {output_path_1}")
            
            results.append({
                "character": char_name,
                "method": "tag_only",
                "tag": tag,
                "file": output_path_1,
                "status": "success"
            })
        except Exception as e:
            print(f"  ✗ 错误: {str(e)}")
            error_file = os.path.join(char_dir, f"{char_name}_tag_only_error.txt")
            save_error_message(str(e), error_file)
            results.append({
                "character": char_name,
                "method": "tag_only",
                "tag": tag,
                "file": error_file,
                "status": "error",
                "error": str(e)
            })
        
        # ========== 生成第二张图片：使用描述 + 参考图像 ==========
        print(f"[{idx+1}/{len(characters)}] {char_name} - 生成第二张（描述+参考）")
        try:
            # 验证参考图像是否存在
            if not Path(reference_path).exists():
                raise FileNotFoundError(f"参考图像不存在: {reference_path}")
            
            # 使用参考图像
            char_ref = CharacterReference(
                image=Path(reference_path),
                type="character",
                fidelity=1,
                strength=1
            )
            
            # 创建 character 对象使用原始描述
            prompt_2 = "character" + style_pos
            images_2 = create_Image(
                prompt=prompt_2,
                character_tags=[description],  # 使用描述
                orientation="portrait",
                character_references=[char_ref],  # 添加参考图像
                negative_prompt=style_neg,
                seed=random_seed
            )
            
            # 保存图片
            output_path_2 = os.path.join(char_dir, f"{char_name}_with_reference.jpg")
            save_novelai_image(images_2[0], output_path_2)
            print(f"  ✓ 已保存: {output_path_2}")
            
            results.append({
                "character": char_name,
                "method": "with_reference",
                "description": description,
                "reference": reference_path,
                "file": output_path_2,
                "status": "success"
            })
        except Exception as e:
            print(f"  ✗ 错误: {str(e)}")
            error_file = os.path.join(char_dir, f"{char_name}_with_reference_error.txt")
            save_error_message(str(e), error_file)
            results.append({
                "character": char_name,
                "method": "with_reference",
                "description": description,
                "reference": reference_path,
                "file": error_file,
                "status": "error",
                "error": str(e)
            })
    
    # 保存结果报告
    report_path = os.path.join(output_base_dir, "test_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n\n=== 测试完成 ===")
    print(f"总共生成图片数: {sum(1 for r in results if r['status'] == 'success')}")
    print(f"错误数: {sum(1 for r in results if r['status'] == 'error')}")
    print(f"报告已保存到: {report_path}")


if __name__ == "__main__":
    generate_test_images()
