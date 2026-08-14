import argparse
import re
from pathlib import Path
from typing import Optional

MAX_FILENAME_LEN = 8

PART_LABEL_PATTERN = re.compile(
    r"(第[零〇一二三四五六七八九十百千万两\d]+[章节话回部集卷篇]|[上中下前后][篇部卷集]|序章|终章|番外)"
)


def read_first_non_empty_line(file_path: Path) -> Optional[str]:
    """读取文件中第一条非空行（第一行为空则继续往下找）。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with file_path.open("r", encoding=enc, errors="strict") as f:
                for line in f:
                    text = line.strip()
                    if text:
                        return text
            return None
        except UnicodeDecodeError:
            continue
    # 最后兜底，忽略坏字符
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            text = line.strip()
            if text:
                return text
    return None


def sanitize_filename(name: str, max_len: int = 6) -> str:
    """清理非法文件名字符，避免 Windows 重命名失败。"""
    # Windows 非法字符: <>:"/\\|?*
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")

    # 避免保留名称
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    if not name:
        name = "untitled"
    if name.upper() in reserved:
        name = f"_{name}"

    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
        if not name:
            name = "untitled"

    return name


def make_rename_base(title: str, max_len: int = MAX_FILENAME_LEN) -> str:
    """Build the shortened filename, preserving chapter/part labels when present."""
    cleaned = sanitize_filename(title, max_len=max(len(title), max_len))
    label_match = PART_LABEL_PATTERN.search(cleaned)
    label = label_match.group(1) if label_match else ""

    if not label or label in cleaned[:max_len]:
        return sanitize_filename(cleaned, max_len=max_len)

    if len(label) >= max_len:
        return sanitize_filename(label, max_len=max_len)

    prefix_len = max_len - len(label)
    prefix = cleaned[:prefix_len].rstrip(" .")
    if not prefix:
        return sanitize_filename(label, max_len=max_len)

    return sanitize_filename(f"{prefix}{label}", max_len=max_len)


def get_available_target(directory: Path, base_name: str, suffix: str, source: Path, max_len: int = 6) -> Path:
    """避免重名：已存在时自动添加 _2, _3 ..."""
    base_name = base_name[:max_len].rstrip(" .") or "untitled"
    target = directory / f"{base_name}{suffix}"
    if target == source:
        return target

    i = 2
    while target.exists():
        suffix_part = f"_{i}"
        keep_len = max_len - len(suffix_part)
        if keep_len <= 0:
            numbered_base = str(i)[-max_len:]
        else:
            numbered_base = (base_name[:keep_len].rstrip(" .") or "x") + suffix_part
        target = directory / f"{numbered_base}{suffix}"
        if target == source:
            return target
        i += 1
    return target


def find_related_items(txt_path: Path, old_stem: str, workspace_root: Path) -> dict:
    """查找 txt 文件对应的所有关联文件和文件夹。基于旧名字查找。"""
    books_root = workspace_root / "books"
    script_root = workspace_root / "script"
    image_root = workspace_root / "image"
    with_text_root = workspace_root / "with_text"

    try:
        rel_dir = txt_path.parent.relative_to(books_root)
    except ValueError:
        return {}

    related = {}

    # script 下的 json 文件（基于旧名字）
    json_path = script_root / rel_dir / f"{old_stem}.json"
    if json_path.exists() and json_path.is_file():
        related["json"] = json_path

    # image 下的文件夹（基于旧名字）
    image_folder = image_root / rel_dir / old_stem
    if image_folder.exists() and image_folder.is_dir():
        related["image"] = image_folder

    # with_text 下的文件夹（基于旧名字）
    with_text_folder = with_text_root / rel_dir / old_stem
    if with_text_folder.exists() and with_text_folder.is_dir():
        related["with_text"] = with_text_folder

    # with_text 下的 _final 文件夹（基于旧名字）
    with_text_final_folder = with_text_root / rel_dir / f"{old_stem}_final"
    if with_text_final_folder.exists() and with_text_final_folder.is_dir():
        related["with_text_final"] = with_text_final_folder

    return related


def rename_txt_files(folder: Path, recursive: bool = False) -> None:
    if not folder.exists() or not folder.is_dir():
        raise NotADirectoryError(f"无效文件夹: {folder}")

    files = folder.rglob("*.txt") if recursive else folder.glob("*.txt")
    files = sorted([p for p in files if p.is_file()])

    if not files:
        print("未找到 txt 文件。")
        return

    renamed, skipped = 0, 0
    workspace_root = Path(__file__).resolve().parent
    folder = folder.resolve()  # 转换为绝对路径

    for file_path in files:
        old_stem = file_path.stem
        file_path = file_path.resolve()  # 确保绝对路径
        related_items = find_related_items(file_path, old_stem, workspace_root)
        first_line = read_first_non_empty_line(file_path)
        if not first_line:
            print(f"[跳过] 文件内容为空: {file_path.name}")
            skipped += 1
            continue

        new_base = make_rename_base(first_line, max_len=MAX_FILENAME_LEN)
        target = get_available_target(file_path.parent, new_base, file_path.suffix, file_path, max_len=MAX_FILENAME_LEN)

        if target == file_path:
            print(f"[保持] 名称不变: {file_path.name}")
            continue

        try:
            file_path.rename(target)
            print(f"[完成] txt: {file_path.name} -> {target.name}")
            renamed += 1

            # 重命名 script 下的 json 文件
            if "json" in related_items:
                json_path = related_items["json"]
                json_target = json_path.with_name(f"{target.stem}.json")
                if json_target == json_path:
                    print(f"  [JSON保持] {json_path.name}")
                elif json_target.exists():
                    print(f"  [JSON失败] 目标已存在: {json_target.name}")
                    skipped += 1
                else:
                    json_path.rename(json_target)
                    print(f"  [JSON完成] {json_path.name} -> {json_target.name}")

            # 重命名 image 下的同名文件夹
            if "image" in related_items:
                image_folder = related_items["image"]
                image_target = image_folder.with_name(target.stem)
                if image_target == image_folder:
                    print(f"  [image保持] {image_folder.name}")
                elif image_target.exists():
                    print(f"  [image失败] 目标已存在: {image_target.name}")
                    skipped += 1
                else:
                    image_folder.rename(image_target)
                    print(f"  [image完成] {image_folder.name} -> {image_target.name}")

            # 重命名 with_text 下的同名文件夹
            if "with_text" in related_items:
                with_text_folder = related_items["with_text"]
                with_text_target = with_text_folder.with_name(target.stem)
                if with_text_target == with_text_folder:
                    print(f"  [with_text保持] {with_text_folder.name}")
                elif with_text_target.exists():
                    print(f"  [with_text失败] 目标已存在: {with_text_target.name}")
                    skipped += 1
                else:
                    with_text_folder.rename(with_text_target)
                    print(f"  [with_text完成] {with_text_folder.name} -> {with_text_target.name}")

            # 重命名 with_text 下的 _final 文件夹
            if "with_text_final" in related_items:
                with_text_final_folder = related_items["with_text_final"]
                with_text_final_target = with_text_final_folder.with_name(f"{target.stem}_final")
                if with_text_final_target == with_text_final_folder:
                    print(f"  [with_text_final保持] {with_text_final_folder.name}")
                elif with_text_final_target.exists():
                    print(f"  [with_text_final失败] 目标已存在: {with_text_final_target.name}")
                    skipped += 1
                else:
                    with_text_final_folder.rename(with_text_final_target)
                    print(f"  [with_text_final完成] {with_text_final_folder.name} -> {with_text_final_target.name}")

        except OSError as e:
            print(f"[失败] {file_path.name}: {e}")
            skipped += 1

    print(f"\n处理结束：重命名 {renamed} 个，跳过 {skipped} 个。")


def rename_files_in_book(book_id: str) -> None:
    """重命名指定书籍文件夹下的所有txt文件及其关联文件。"""
    folder = Path("books") / book_id
    rename_txt_files(folder, recursive=False)


def main() -> None:
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="根据文件内容首行重命名 txt 文件及关联文件"
    )
    parser.add_argument(
        "book_id",
        nargs="?",
        default="白音miyomi",
        help="书籍ID"
    )
    
    args = parser.parse_args()
    rename_files_in_book(args.book_id)


if __name__ == "__main__":
    main()
