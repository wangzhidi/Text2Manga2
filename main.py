"""
SFWManga 漫画生成工作流的统一入口
依次调用：board.py -> image.py -> dialog.py
"""

import argparse
import shutil
from pathlib import Path
from board import generate_storyboard
from image import generate_images
from dialog import add_dialog_and_text
from rename import rename_files_in_book


book=["8Mr-白熊", "8sjz-凌风", "8ZeroEdge"]

chapters=None
force=False    

def main(): 
    """主入口函数，依次执行三个步骤"""   
    parser = argparse.ArgumentParser(
        description="从文本到漫画的完整工作流：分镜 -> 图像 -> 对话框"
    )
    parser.add_argument("book_id", nargs="*", default=book, help="书籍ID (可传入多个，将依次处理)")
    parser.add_argument("-c", "--chapter", help="特定章节ID (可选)", default=chapters)
    parser.add_argument(
        "--skip-storyboard",
        action="store_true",
        help="跳过分镜生成步骤"
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",  
        help="跳过图像生成步骤"
    )
    parser.add_argument(
        "--skip-dialog",
        action="store_true",
        help="跳过对话框添加步骤"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=force,
        help="强制删除章节旧数据后重新执行（chapter 为 None 时忽略）"
    )
    
    args = parser.parse_args()
    
    book_id_list = args.book_id if isinstance(args.book_id, list) else [args.book_id]
    chapter = args.chapter

    # 循环处理每个 book_id
    for book_id in book_id_list:
        process_book(book_id, chapter, args)
    


def process_book(book_id, chapter, args):
    """处理单个 book_id 的工作流"""
    # force 模式：仅在指定 chapter 时生效
    if chapter is not None and args.force:
        root = Path(__file__).resolve().parent
        targets = [
            root / "image" / book_id / chapter,
            root / "script" / book_id / f"{chapter}.json",
            root / "with_text" / book_id / chapter,
            root / "with_text" / book_id / f"{chapter}_final",
        ]

        print("\n【预处理】force 模式：删除旧数据")
        print("-"*70)
        for target in targets:
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                    print(f"🗑️  已删除目录: {target}")
                elif target.is_file():
                    target.unlink()
                    print(f"🗑️  已删除文件: {target}")
                else:
                    print(f"ℹ️  跳过不存在路径: {target}")
            except Exception as e:
                print(f"⚠️  删除失败: {target} -> {str(e)}")
    
    print("\n" + "="*70)
    print("SFWManga 漫画生成工作流")
    print("="*70)
    print(f"📚 Book ID: {book_id}")
    if chapter:
        print(f"📖 Chapter: {chapter}")
    print()
    
    # 如果 chapter 为 None，先重命名文件
    if chapter is None:
        print("\n【步骤0】重命名文件 (rename.py)")
        print("-"*70)
        try:
            rename_files_in_book(book_id=book_id)
            print("✅ 文件重命名完成！\n")
        except Exception as e:
            print(f"⚠️  文件重命名出错: {str(e)}\n")
    
    # 步骤1: 生成分镜脚本
    if not args.skip_storyboard:
        print("\n【步骤1】生成分镜脚本 (board.py)")
        print("-"*70)

        generate_storyboard(book_id=book_id, chapter=chapter)
        print("✅ 分镜生成完成！\n")


    else:
        print("\n⏭️  跳过分镜生成步骤\n")
    
    # 步骤2: 生成图像
    if not args.skip_images:
        print("\n【步骤2】生成图像 (image.py)")
        print("-"*70)

        generate_images(book_id=book_id, chapter=chapter)
        print("✅ 图像生成完成！\n")

    else:
        print("\n⏭️  跳过图像生成步骤\n")
    
    # 步骤3: 添加对话框和文本
    if not args.skip_dialog:
        print("\n【步骤3】添加对话框和文本 (dialog.py)")
        print("-"*70)
        try:
            # 复用 dialog.py 内部逻辑：chapter=None 时自动处理该目录下全部章节
            add_dialog_and_text(book_id=book_id, chapter=chapter)

            print("✅ 对话框添加完成！\n")
        except Exception as e:
            print(f"❌ 对话框添加失败: {str(e)}\n")
            return
    else:
        print("\n⏭️  跳过对话框添加步骤\n")
    
    # 完成
    print("="*70)
    print("✨ 工作流全部完成！")
    print("="*70)


if __name__ == "__main__":
    main()
