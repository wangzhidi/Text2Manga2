import os
import re
from PIL import Image

def natural_key(s):
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r'(\d+)', s)
    ]

def get_sorted_images(folder_path):
    files = [
        f for f in os.listdir(folder_path)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    def _cover_first(name: str):
        stem = os.path.splitext(name)[0]
        return 0 if stem == "000" else 1

    files.sort(key=lambda x: (_cover_first(x), natural_key(x)))
    return files


def get_sorted_pngs(folder_path):
    """兼容旧调用：返回可用图片列表（现已支持 jpg/jpeg/png）。"""
    return get_sorted_images(folder_path)

def merge_images_in_folder(folder_path, target_width=832, separator_height=10):
    files = get_sorted_images(folder_path)

    if not files:
        print(f"[跳过] 无可用图片: {folder_path}")
        return

    images = []

    for file in files:
        img_path = os.path.join(folder_path, file)
        img = Image.open(img_path).convert("RGB")

        if img.width > target_width:
            ratio = target_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((target_width, new_height), Image.LANCZOS)

        images.append(img)

    total_height = sum(img.height for img in images)
    total_height += separator_height * (len(images) - 1)

    merged = Image.new("RGB", (target_width, total_height), (255, 255, 255))

    y_offset = 0
    for i, img in enumerate(images):
        merged.paste(img, (0, y_offset))
        y_offset += img.height
        if i < len(images) - 1:
            y_offset += separator_height

    # 输出路径：上一级目录/文件夹名.jpg
    parent_dir = os.path.dirname(folder_path)
    folder_name = os.path.basename(folder_path)
    output_path = os.path.join(parent_dir, f"{folder_name}.jpg")

    merged.save(output_path, "JPEG", quality=95)
    print(f"[完成] {output_path}")


def process_folder(root_folder):
    folder_name = os.path.basename(root_folder)

    # 情况1：当前目录本身就是 _final
    if folder_name.endswith("_final"):
        merge_images_in_folder(root_folder)
        return

    # 情况2：查找子文件夹中 _final 的
    subfolders = [
        os.path.join(root_folder, d)
        for d in os.listdir(root_folder)
        if os.path.isdir(os.path.join(root_folder, d)) and d.endswith("_final")
    ]

    if subfolders:
        print(f"检测到 {len(subfolders)} 个 _final 子文件夹，开始处理...")
        for sub in subfolders:
            merge_images_in_folder(sub)
    else:
        print("未找到任何 _final 文件夹")


if __name__ == "__main__":
    folder = r"with_text\魔法少女独断万古"
    process_folder(folder)