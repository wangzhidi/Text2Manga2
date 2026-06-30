import sys
import json
import os
import shutil
from pathlib import Path




from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QProgressBar
)
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from make_cover import make_manga_cover, make_manga_cover_landscape


INPUT_DIR = r"with_text\7川一KULA\被不良少_2"
FINAL_DIR = f"{INPUT_DIR}_final"
OUTPUT_COVER = f"{FINAL_DIR}\\000.png"
OUTPUT_COVER_LANDSCAPE = f"{FINAL_DIR}\\Z01.png"


def _resolve_img(directory, stem):
    """Return the first existing path for stem with .jpg or .png; falls back to .png."""
    for ext in ("jpg", "png"):
        p = os.path.join(directory, f"{stem}.{ext}")
        if os.path.exists(p):
            return p
    return os.path.join(directory, f"{stem}.png")


def _resolve_json_path(input_dir):
    """优先在 input_dir 查找 .json；若没有则回退到 script 对应目录查找。"""
    json_files = sorted(f for f in os.listdir(input_dir) if f.endswith(".json"))
    if json_files:
        return os.path.join(input_dir, json_files[0])

    p = Path(input_dir)
    parts = list(p.parts)
    with_text_idx = next((i for i, x in enumerate(parts) if x.lower() == "with_text"), -1)
    if with_text_idx >= 0:
        script_parts = parts[:]
        script_parts[with_text_idx] = "script"
        script_dir = Path(*script_parts)

        # 1) 尝试 script 同层目录（兼容原有思路）
        if script_dir.is_dir():
            script_json_files = sorted(
                f for f in os.listdir(str(script_dir)) if f.endswith(".json")
            )
            if script_json_files:
                return str(script_dir / script_json_files[0])

        # 2) 尝试 script 父目录下同名 json：script/.../<当前目录名>.json
        parent_dir = script_dir.parent
        if parent_dir.is_dir():
            same_name_json = parent_dir / f"{script_dir.name}.json"
            if same_name_json.exists():
                return str(same_name_json)

            # 3) 兜底：父目录任意 json
            parent_json_files = sorted(
                f for f in os.listdir(str(parent_dir)) if f.endswith(".json")
            )
            if parent_json_files:
                return str(parent_dir / parent_json_files[0])

    return None

class ImageLabel(QLabel):
    def __init__(self, parent, side):
        super().__init__(parent)
        self.parent = parent
        self.side = side
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)

        self.preview_pos = None
        self.img_offset_x = 0
        self.img_offset_y = 0
        self.display_scale = 1.0

    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)

        if not pixmap:
            return

        pix_w = pixmap.width()
        pix_h = pixmap.height()
        if pix_w == 0 or pix_h == 0:
            return

        label_w = self.width()
        label_h = self.height()

        scale = min(label_w / pix_w, label_h / pix_h)
        self.display_scale = scale

        shown_w = pix_w * scale
        shown_h = pix_h * scale

        self.img_offset_x = int((label_w - shown_w) / 2)
        self.img_offset_y = int((label_h - shown_h) / 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.parent.apply_mosaic(self.side, event.x(), event.y())
        elif event.button() == Qt.RightButton:
            self.parent.choose_image(self.side)

    def mouseMoveEvent(self, event):
        self.preview_pos = event.pos()
        self.update()

    def leaveEvent(self, event):
        self.preview_pos = None
        self.update()

    def wheelEvent(self, event):
        self.parent.adjust_mosaic_size(event.angleDelta().y())

    def paintEvent(self, event):
        super().paintEvent(event)

        if not self.preview_pos:
            return

        painter = QPainter(self)
        pen = QPen(QColor(255, 0, 0))
        pen.setWidth(2)
        painter.setPen(pen)

        size = self.parent.MOSAIC_SIZE * self.display_scale
        x = self.preview_pos.x() - size / 2
        y = self.preview_pos.y() - size / 2

        painter.drawRect(int(x), int(y), int(size), int(size))
        painter.end()


class ImageSelector(QWidget):
    MOSAIC_SIZE = 100
    MOSAIC_LEVEL = 10

    def __init__(self, json_path):
        super().__init__()
        self.setWindowTitle("Image Selector")
        self.resize(1600, 1200)

        os.makedirs(FINAL_DIR, exist_ok=True)

        self.json_path = json_path
        with open(json_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        # 跳过第一项元信息
        self.data = raw_data[1:] if raw_data else []

        self.index = 0
        self.cover_image = None
        self.landscape_cover_image = None
        self.paths = []
        self.current_image_paths = []

        self.current_pil_image = None
        self.current_path = None

        self.init_ui()
        self.load_current()

    def init_ui(self):
        self.left_label = ImageLabel(self, "left")
        self.right_label = ImageLabel(self, "right")

        self.desc_label = QLabel()
        self.tags_label = QLabel()

        self.desc_label.setWordWrap(True)
        self.tags_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setMaximum(len(self.data))

        img_layout = QHBoxLayout()
        img_layout.addWidget(self.left_label)
        img_layout.addWidget(self.right_label)

        text_layout = QVBoxLayout()
        text_layout.addWidget(self.desc_label)
        text_layout.addWidget(self.tags_label)
        text_layout.addWidget(self.progress)

        main = QVBoxLayout()
        main.addLayout(img_layout, 4)
        main.addLayout(text_layout, 1)

        self.setLayout(main)

    def load_current(self):
        if self.index >= len(self.data):

            if self.cover_image is None:
                self.cover_image = self.current_image_paths[0] if self.current_image_paths else None

            if self.cover_image:
                make_manga_cover(
                    self.cover_image,
                    self.json_path,
                    OUTPUT_COVER
                )

            if self.landscape_cover_image:
                make_manga_cover_landscape(
                    self.landscape_cover_image,
                    self.json_path,
                    OUTPUT_COVER_LANDSCAPE
                )
            self.close()
            return

        entry = self.data[self.index]
        self.paths = []
        self.current_image_paths = []

        entry_id = entry.get("id", "unknown")
        self.paths = [
            _resolve_img(INPUT_DIR, f"{entry_id}_1"),
            _resolve_img(INPUT_DIR, f"{entry_id}_2"),
        ]

        # 不再依赖 json 中的 image_path，直接由 with_text 路径推导 image 路径
        self.current_image_paths = [self.to_image_path(p) for p in self.paths]
        if not all(os.path.exists(p) for p in self.current_image_paths):
            self.index += 1
            self.load_current()
            return

        self.desc_label.setText(entry["description"])
        
        # 拼接main_tags和character_tags
        main_tags = entry.get("main_tags", "")
        character_tags = entry.get("character_tags", [])
        tags_text = main_tags
        if character_tags:
            if isinstance(character_tags, list):
                tags_text += ", " + ", ".join(character_tags)
            else:
                tags_text += ", " + character_tags
        
        self.tags_label.setText(tags_text)
        # 设置desc_label字体大小
        font = self.desc_label.font()
        font.setPointSize(24)
        self.desc_label.setFont(font)
        self.tags_label.setFont(font)

        self.load_image(self.left_label, self.paths[0])
        self.load_image(self.right_label, self.paths[1])

        self.progress.setValue(self.index + 1)

    def to_image_path(self, with_text_path):
        p = Path(with_text_path)
        parts = list(p.parts)
        if parts and parts[0].lower() == "with_text":
            parts[0] = "image"
            return str(Path(*parts))
        return with_text_path

    def load_image(self, label, path):
        pix = QPixmap(path)
        if pix.isNull():
            label.setPixmap(QPixmap())
            return
        # 记录原始比例缩放后的结果
        scaled_pix = pix.scaled(
            label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        label.setPixmap(scaled_pix)

    def resizeEvent(self, event):
        self.load_current()

    def choose_image(self, side):
        chosen = self.paths[0] if side == "left" else self.paths[1]
        shutil.copy(chosen, FINAL_DIR)
        self.index += 1
        self.load_current()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_X:
            self.index += 1
            self.load_current()

        elif event.key() == Qt.Key_Z:
            self.cover_image = self.get_hovered_image()

        elif event.key() == Qt.Key_C:
            self.landscape_cover_image = self.get_hovered_image()
            if self.landscape_cover_image is None and self.current_image_paths:
                self.landscape_cover_image = self.current_image_paths[0]

    def get_hovered_image(self):
        if self.left_label.preview_pos:
            return self.current_image_paths[0] if self.current_image_paths else None
        if self.right_label.preview_pos:
            return self.current_image_paths[1] if self.current_image_paths else None
        return None

    def adjust_mosaic_size(self, delta):
        if delta > 0:
            self.MOSAIC_SIZE += 5
        else:
            self.MOSAIC_SIZE = max(10, self.MOSAIC_SIZE - 5)

    def apply_mosaic(self, side, ui_x, ui_y):
        label = self.left_label if side == "left" else self.right_label
        path = self.paths[0] if side == "left" else self.paths[1]

        # 1. 重新打开原图获取尺寸
        img = Image.open(path)
        img_w, img_h = img.size

        # 2. 获取当前 Label 实际显示的图片尺寸和偏移
        # 注意：label.pixmap() 是已经缩放过的图
        pix = label.pixmap()
        if not pix: return
        
        pix_w = pix.width()
        pix_h = pix.height()

        # 计算图片在 Label 内部居中后的起始坐标
        offset_x = (label.width() - pix_w) // 2
        offset_y = (label.height() - pix_h) // 2

        # 3. 检查点击是否在图片范围内
        if not (offset_x <= ui_x <= offset_x + pix_w and
                offset_y <= ui_y <= offset_y + pix_h):
            return

        # 4. 将 UI 坐标转换为原始图片坐标
        # (当前坐标 - 偏移) / 缩放比例
        scale = pix_w / img_w  # 缩放比 = 显示宽度 / 原始宽度
        real_x = int((ui_x - offset_x) / scale)
        real_y = int((ui_y - offset_y) / scale)

        # 5. 执行马赛克逻辑
        half = self.MOSAIC_SIZE // 2
        box = (
            max(0, real_x - half),
            max(0, real_y - half),
            min(img_w, real_x + half),
            min(img_h, real_y + half)
        )

        if box[2] > box[0] and box[3] > box[1]:
            region = img.crop(box)
            # 这里的 MOSAIC_LEVEL 越大越模糊
            small = region.resize(
                (max(1, region.width // self.MOSAIC_LEVEL),
                 max(1, region.height // self.MOSAIC_LEVEL)),
                Image.NEAREST
            )
            mosaic = small.resize(region.size, Image.NEAREST)
            img.paste(mosaic, box)

            # 保存并刷新
            img.save(path)
            self.load_current()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    json_path = _resolve_json_path(INPUT_DIR)
    if not json_path:
        raise FileNotFoundError(
            f"在 {INPUT_DIR} 及其对应 script 目录中都未找到 .json 文件"
        )
    w = ImageSelector(json_path)
    w.show()
    sys.exit(app.exec_())

# Z = 设为封面图
# C = 设为横屏封面图（中央裁切 4:3）
# X = 跳过当前，选择下一组
# 鼠标左键点击 = 对当前鼠标位置进行马赛克处理
# 鼠标右键点击 = 选择当前图片并保存