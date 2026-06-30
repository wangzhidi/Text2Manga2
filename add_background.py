from pathlib import Path

from PIL import Image


def fill_transparent_with_white(image_path: Path) -> None:
	"""将 PNG 图片透明区域填充为白色，并覆盖原图。"""
	with Image.open(image_path) as img:
		# 统一转为 RGBA，确保有 alpha 通道可处理
		rgba = img.convert("RGBA")
		white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
		merged = Image.alpha_composite(white_bg, rgba).convert("RGB")
		merged.save(image_path, format="PNG")


def process_folder(folder_path: str) -> None:
	folder = Path(folder_path).expanduser()

	if not folder.exists() or not folder.is_dir():
		raise NotADirectoryError(f"无效文件夹路径: {folder}")

	png_files = sorted(folder.glob("*.png"))

	if not png_files:
		print("未找到 PNG 图片。")
		return

	success = 0
	failed = 0

	for file_path in png_files:
		try:
			fill_transparent_with_white(file_path)
			success += 1
			print(f"已处理: {file_path.name}")
		except Exception as exc:
			failed += 1
			print(f"处理失败: {file_path.name} -> {exc}")

	print(f"完成，共 {len(png_files)} 张；成功 {success} 张，失败 {failed} 张。")


if __name__ == "__main__":
	input_path = input("请输入图片文件夹路径: ").strip().strip('"')
	process_folder(input_path)
