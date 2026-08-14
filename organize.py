"""
organize.py — 将漫画成品整理到 final 文件夹

使用方式：
    python organize.py <book_id>
    python organize.py 000

逻辑：
1. 扫描 books/<book_id>/ 下所有 txt 文件
2. 旧名称 = txt 文件的 stem（不含扩展名）
3. 新名称 = txt 文件第一条非空行，最长 8 字符；重名则加 _2, _3 ... 后缀
4. 检查 image/<book_id>/<旧名称>/ 是否存在
5. 检查 with_text/<book_id>/<旧名称>_final/ 是否存在
6. 若至少有一个存在，则：
   - 在 final/<book_id>/<新名称>/ 下建好目录
   - 将 image 子文件夹复制为  final/<book_id>/<新名称>/无字幕原图/
   - 将 with_text_final 子文件夹复制为 final/<book_id>/<新名称>/漫画版/
   - 将 books/script 中同名 txt/json 复制到 final/<book_id>脚本/<新名称>/
"""

import argparse
import re
import shutil
from pathlib import Path
from typing import Optional

MAX_FILENAME_LEN = 8

PART_LABEL_PATTERN = re.compile(
    r"(第[零〇一二三四五六七八九十百千万两\d]+[章节话回部集卷篇]|[上中下前后][篇部卷集]|序章|终章|番外)"
)

# ──────────────────────────────────────────────
# 工具函数（与 rename.py 保持一致，独立实现避免互相依赖）
# ──────────────────────────────────────────────

def read_first_non_empty_line(file_path: Path) -> Optional[str]:
    """读取文件中第一条非空行，自动尝试多种编码。"""
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
    # 兜底：忽略坏字符
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            text = line.strip()
            if text:
                return text
    return None


def sanitize_filename(name: str, max_len: int = 8) -> str:
    """清理 Windows 非法文件名字符，并截断到 max_len 个字符。"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
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


def resolve_unique_name(used_names: set, base_name: str, max_len: int = MAX_FILENAME_LEN) -> str:
    """
    在已用名称集合中找到一个不重复的名称。
    重名时追加 _2, _3 ... 并保证总长度不超过 max_len。
    """
    if base_name not in used_names:
        return base_name
    i = 2
    while True:
        suffix = f"_{i}"
        keep = max_len - len(suffix)
        if keep <= 0:
            candidate = str(i)[-max_len:]
        else:
            candidate = base_name[:keep].rstrip(" .") + suffix
        if candidate not in used_names:
            return candidate
        i += 1


# ──────────────────────────────────────────────
# 核心逻辑
# ──────────────────────────────────────────────

def organize(book_id: str, dry_run: bool = False) -> None:
    """
    整理指定 book_id 下的所有成品到 final 文件夹。

    Parameters
    ----------
    book_id : str
        子文件夹名称，如 "000"、"白音miyomi"
    dry_run : bool
        若为 True，只打印计划，不实际复制
    """
    workspace = Path(__file__).resolve().parent

    books_dir     = workspace / "books"     / book_id
    script_dir    = workspace / "script"    / book_id
    image_dir     = workspace / "image"     / book_id
    with_text_dir = workspace / "with_text" / book_id
    final_dir     = workspace / "final"     / book_id
    final_script_dir = workspace / "final" / f"{book_id}脚本"

    if not books_dir.exists():
        print(f"[错误] 找不到 books 目录: {books_dir}")
        return

    # 收集所有 txt 文件
    txt_files = sorted(p for p in books_dir.glob("*.txt") if p.is_file())
    if not txt_files:
        print(f"[提示] {books_dir} 下没有 txt 文件。")
        return

    print(f"\n{'='*60}")
    print(f"整理 book_id = {book_id}")
    print(f"  books     : {books_dir}")
    print(f"  script    : {script_dir}")
    print(f"  image     : {image_dir}")
    print(f"  with_text : {with_text_dir}")
    print(f"  final     : {final_dir}")
    print(f"  scripts   : {final_script_dir}")
    if dry_run:
        print("  [dry-run] 不会实际复制文件")
    print(f"{'='*60}\n")

    used_names: set[str] = set()   # 已分配的新名称
    processed = skipped = 0

    for txt_path in txt_files:
        old_stem = txt_path.stem

        # ── 1. 读取新名称 ──────────────────────────────────
        first_line = read_first_non_empty_line(txt_path)
        if not first_line:
            print(f"[跳过] 内容为空: {txt_path.name}")
            skipped += 1
            continue

        base_name = make_rename_base(first_line, max_len=MAX_FILENAME_LEN)
        new_name  = resolve_unique_name(used_names, base_name, max_len=MAX_FILENAME_LEN)
        used_names.add(new_name)

        # ── 2. 定位源文件夹 ───────────────────────────────
        src_image = image_dir / old_stem
        has_image = src_image.exists() and src_image.is_dir()

        # with_text_final：先直接找，找不到则递归搜索子目录
        final_folder_name = f"{old_stem}_final"
        _direct = with_text_dir / final_folder_name
        if _direct.exists() and _direct.is_dir():
            src_with_text = _direct
            has_with_text = True
        elif with_text_dir.exists():
            _found = [p for p in with_text_dir.rglob(final_folder_name) if p.is_dir()]
            if _found:
                if len(_found) > 1:
                    print(f"        [警告] 找到多个 {final_folder_name}，使用第一个: {_found[0].relative_to(workspace)}")
                src_with_text = _found[0]
                has_with_text = True
            else:
                src_with_text = _direct   # 占位，has_with_text=False 时不会用到
                has_with_text = False
        else:
            src_with_text = _direct
            has_with_text = False

        if not has_image and not has_with_text:
            print(f"[跳过] {old_stem!r} → 未找到 image 或 with_text_final 文件夹")
            skipped += 1
            continue

        # ── 3. 目标路径 ────────────────────────────────────
        dest_root      = final_dir / new_name
        dest_image     = dest_root / "无字幕原图"
        dest_with_text = dest_root / "漫画版"
        dest_script    = final_script_dir / new_name
        src_json       = script_dir / f"{old_stem}.json"

        print(f"[处理] {old_stem!r} → {new_name!r}")
        if has_image:
            print(f"        image      : {src_image.relative_to(workspace)}")
            print(f"               → {dest_image.relative_to(workspace)}")
        else:
            print(f"        image      : 未找到，跳过")
        if has_with_text:
            print(f"        with_text  : {src_with_text.relative_to(workspace)}")
            print(f"               → {dest_with_text.relative_to(workspace)}")
        else:
            print(f"        with_text  : 未找到，跳过")
        print(f"        txt        : {txt_path.relative_to(workspace)}")
        print(f"               → {dest_script.relative_to(workspace) / txt_path.name}")
        if src_json.exists() and src_json.is_file():
            print(f"        json       : {src_json.relative_to(workspace)}")
            print(f"               → {dest_script.relative_to(workspace) / src_json.name}")
        else:
            print(f"        json       : 未找到，跳过")

        if dry_run:
            processed += 1
            continue

        # ── 4. 执行复制 ────────────────────────────────────
        try:
            if has_image:
                if dest_image.exists():
                    print(f"        [警告] 目标已存在，跳过复制: {dest_image.name}")
                else:
                    shutil.copytree(src_image, dest_image)
                    print(f"        ✓ 无字幕原图 复制完成")

            if has_with_text:
                if dest_with_text.exists():
                    print(f"        [警告] 目标已存在，跳过复制: {dest_with_text.name}")
                else:
                    shutil.copytree(src_with_text, dest_with_text)
                    print(f"        ✓ 漫画版 复制完成")

            dest_script.mkdir(parents=True, exist_ok=True)
            dest_txt = dest_script / txt_path.name
            if dest_txt.exists():
                print(f"        [警告] 目标已存在，跳过复制: {dest_txt.name}")
            else:
                shutil.copy2(txt_path, dest_txt)
                print(f"        ✓ txt 脚本复制完成")

            if src_json.exists() and src_json.is_file():
                dest_json = dest_script / src_json.name
                if dest_json.exists():
                    print(f"        [警告] 目标已存在，跳过复制: {dest_json.name}")
                else:
                    shutil.copy2(src_json, dest_json)
                    print(f"        ✓ json 脚本复制完成")

            processed += 1
        except OSError as e:
            print(f"        [失败] 复制出错: {e}")
            skipped += 1

    print(f"\n{'='*60}")
    print(f"完成：处理 {processed} 个，跳过 {skipped} 个。")
    print(f"{'='*60}\n")


# ──────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="将漫画成品整理到 final 文件夹（按 txt 首行命名）"
    )
    parser.add_argument(
        "book_id",
        nargs="?",
        default="6其他"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划，不实际复制文件"
    )
    args = parser.parse_args()
    organize(args.book_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
