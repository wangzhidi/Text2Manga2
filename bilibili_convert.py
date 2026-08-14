import json
import random
import re
import shutil
import hashlib
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PyQt5.QtCore import QObject, Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import data
from concate import get_sorted_images
from manga_to_video import make_video



INPUT_FOLDER = r"C:\UserTemp\Visual Studio Code\Text2Manga2\final\原图四期\调休，遇到淋雨卡"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
IMAGE_GRID_COLUMNS = 4
IMAGE_TILE_MIN_WIDTH = 220
IMAGE_PREVIEW_HEIGHT = 250
DEFAULT_TAGS = ["宁静"]
MANGA_AUTHOR = "很大只狸花"
PREVIEW_CACHE_DIR = Path("__bilibili_preview_cache__")


def natural_key(text):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(text))]


def get_project_root(path):
    path = Path(path).resolve()
    if path.name in {"无字幕原图", "漫画版"}:
        return path.parent
    return path


def get_manga_version_dir(project_root):
    manga_dir = project_root / "漫画版"
    manga_dir.mkdir(parents=True, exist_ok=True)
    return manga_dir


def copy_project_to_upload(project_root):
    upload_root = Path("final") / "待上传"
    upload_root.mkdir(parents=True, exist_ok=True)
    source = Path(project_root).resolve()
    target = (upload_root / source.name).resolve()
    if source == target:
        return target
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__bilibili_convert_tmp__*.mp4"))
    return target


def find_no_subtitle_dir(root):
    root = Path(root)
    if root.name == "无字幕原图":
        return root
    direct = root / "无字幕原图"
    if direct.is_dir():
        return direct
    exact = [p for p in root.rglob("*") if p.is_dir() and p.name == "无字幕原图"]
    if exact:
        return sorted(exact, key=lambda p: len(p.parts))[0]
    raise FileNotFoundError(f"未找到“无字幕原图”文件夹: {root}")


def find_json_path(root):
    root = get_project_root(root)
    candidates = [
        root,
        root.with_name(root.name + "脚本"),
        root.parent.with_name(root.parent.name + "脚本") / root.name,
        root.parent / (root.name + "脚本"),
    ]
    jsons = []
    for folder in candidates:
        if folder.exists():
            jsons.extend(sorted(folder.glob("*.json"), key=lambda p: natural_key(p.name)))
    if not jsons:
        raise FileNotFoundError(f"未找到脚本 JSON: {root}")
    return jsons[0]


def load_json(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_cover_meta(items):
    meta = next((item for item in items if item.get("id") == -1), items[0] if items else {})
    title = str(meta.get("title") or "").strip()
    if not title:
        title = "未命名"
    return {
        "title": title,
        "original_author": str(meta.get("original_author") or "").strip(),
        "platform": str(meta.get("platform") or "").strip(),
        "url": str(meta.get("url") or "").strip(),
    }


def split_title_one_or_two_lines(text):
    text = str(text or "").strip()
    if len(text) <= 14:
        return [text]
    for sep in [",", "，", " ", "_", "。", "、", "-", ":", "："]:
        if sep in text:
            left, right = text.split(sep, 1)
            if left.strip() and right.strip():
                return [left.strip(), right.strip()]
    mid = len(text) // 2
    return [text[:mid].strip(), text[mid:].strip()]


def load_cover_font(size):
    candidates = [
        Path("z其他") / "SIMHEI.TTF",
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def draw_right_aligned_text(draw, xy_right, text, font, fill, stroke_fill, stroke_width):
    right, y = xy_right
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    width = bbox[2] - bbox[0]
    draw.text(
        (right - width, y),
        text,
        font=font,
        fill=fill,
        stroke_fill=stroke_fill,
        stroke_width=stroke_width,
    )


def make_landscape_cover_fallback(img_path, items, output_path):
    meta = load_cover_meta(items)
    src = Image.open(img_path).convert("RGB")
    src_w, src_h = src.size
    target_ratio = 4 / 3
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        crop_h = src_h
        crop_w = int(round(crop_h * target_ratio))
        left, top = (src_w - crop_w) // 2, 0
    else:
        crop_w = src_w
        crop_h = int(round(crop_w / target_ratio))
        left, top = 0, (src_h - crop_h) // 2

    img = src.crop((left, top, left + crop_w, top + crop_h))
    width, height = img.size
    draw = ImageDraw.Draw(img)

    title_lines = [line for line in split_title_one_or_two_lines(meta["title"]) if line]
    title_size = int(height * (0.11 if len(title_lines) == 1 else 0.09))
    title_font = load_cover_font(title_size)
    footer_font = load_cover_font(int(height * 0.06))

    right = int(width * 0.94)
    line_height = int(title_size * 1.2)
    first_y = int(height * 0.84) - (len(title_lines) - 1) * line_height - title_size
    for i, line in enumerate(title_lines):
        draw_right_aligned_text(
            draw,
            (right, first_y + i * line_height),
            line,
            title_font,
            fill=(255, 255, 255),
            stroke_fill=(0, 0, 0),
            stroke_width=max(2, int(title_size * 0.12)),
        )

    footer_lines = ["AI生成"]
    if meta["original_author"]:
        footer_lines.append(f"原文作者: {meta['original_author']}")
    footer_lines.append(f"漫画作者: {MANGA_AUTHOR}")

    footer_size = int(height * 0.06)
    footer_line_height = int(footer_size * 1.25)
    footer_bottom = first_y - int(height * 0.04)
    footer_start = footer_bottom - len(footer_lines) * footer_line_height
    separator_y = int((footer_bottom + first_y) / 2)
    draw.line(
        (int(width * 0.46), separator_y, int(width * 0.96), separator_y),
        fill=(0, 0, 0),
        width=max(2, int(height * 0.006)),
    )
    for i, line in enumerate(footer_lines):
        draw_right_aligned_text(
            draw,
            (int(width * 0.96), footer_start + i * footer_line_height),
            line,
            footer_font,
            fill=(0, 0, 0),
            stroke_fill=(255, 255, 255),
            stroke_width=max(1, int(footer_size * 0.08)),
        )

    img.save(output_path)


def make_landscape_cover(img_path, json_path, items, output_path):
    try:
        from make_cover import make_manga_cover_landscape

        make_manga_cover_landscape(img_path, json_path, output_path, manga_author=MANGA_AUTHOR)
    except ModuleNotFoundError as exc:
        if exc.name != "cairo":
            raise
        make_landscape_cover_fallback(img_path, items, output_path)


def pick_bgm_candidates(tags, limit=5):
    include = set(tags or [])
    scored = []
    for key, bgm_tags in data.bgm_map.items():
        path = data.BGM_DIR / key
        if not path.exists():
            continue
        score = len(include & set(bgm_tags))
        if include and score == 0:
            continue
        scored.append((score, key, path, list(bgm_tags)))

    if not scored:
        for key, bgm_tags in data.bgm_map.items():
            path = data.BGM_DIR / key
            if path.exists():
                scored.append((0, key, path, list(bgm_tags)))

    rng = random.SystemRandom()
    if len(scored) > limit:
        return rng.sample(scored, limit)
    rng.shuffle(scored)
    return scored[:limit]


def write_bgm_tags_to_data_py(key, tags):
    data_path = Path(data.__file__).with_name("data.py")
    text = data_path.read_text(encoding="utf-8")
    key_literal = json.dumps(key, ensure_ascii=False)
    tags_literal = json.dumps(tags, ensure_ascii=False)
    pattern = re.compile(rf'^(\s*){re.escape(key_literal)}\s*:\s*\[[^\n]*\],\s*$', re.MULTILINE)
    replacement = rf'\1{key_literal}: {tags_literal},'
    new_text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"未能在 data.py 中定位 BGM 条目: {key}")
    data_path.write_text(new_text, encoding="utf-8")
    data.bgm_map[key] = tags


def delete_bgm_from_data_py(key):
    data_path = Path(data.__file__).with_name("data.py")
    text = data_path.read_text(encoding="utf-8")
    key_literal = json.dumps(key, ensure_ascii=False)
    pattern = re.compile(rf'^\s*{re.escape(key_literal)}\s*:\s*\[[^\n]*\],\s*\n?', re.MULTILINE)
    new_text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"未能在 data.py 中定位 BGM 条目: {key}")
    data_path.write_text(new_text, encoding="utf-8")
    data.bgm_map.pop(key, None)


def make_bgm_preview_wav(path):
    src = Path(path).resolve()
    if not src.exists():
        raise FileNotFoundError(src)

    PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stat = src.stat()
    cache_key = hashlib.sha1(f"{src}|{stat.st_mtime_ns}|{stat.st_size}".encode("utf-8")).hexdigest()[:16]
    safe_stem = re.sub(r"[^0-9A-Za-z._-]+", "_", src.stem).strip("._") or "bgm"
    wav_path = (PREVIEW_CACHE_DIR / f"{safe_stem}_{cache_key}.wav").resolve()
    if wav_path.exists() and wav_path.stat().st_size > 0:
        return wav_path

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(f"无法找到 ffmpeg，不能生成播放预览: {exc}") from exc

    tmp_path = wav_path.with_suffix(".tmp.wav")
    if tmp_path.exists():
        tmp_path.unlink()

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(tmp_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"BGM 预览转码失败: {stderr or result.returncode}")
    tmp_path.replace(wav_path)
    return wav_path


class BgmEditDialog(QDialog):
    def __init__(self, key, tags, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑 BGM 标签")
        self.resize(560, 180)
        self.tags = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(key))
        self.edit = QLineEdit(", ".join(tags))
        layout.addWidget(self.edit)

        hint = QLabel("用逗号分隔，例如：原神, 抒情")
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        ok = QPushButton("保存")
        cancel = QPushButton("取消")
        ok.clicked.connect(self.accept_tags)
        cancel.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def accept_tags(self):
        tags = [part.strip() for part in re.split(r"[,，]", self.edit.text()) if part.strip()]
        if not tags:
            QMessageBox.warning(self, "标签为空", "请至少保留一个标签。")
            return
        self.tags = tags
        self.accept()


class ConvertWorker(QObject):
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, image_dir, required_source_dir_name, tags, bgm_path, output_path):
        super().__init__()
        self.image_dir = Path(image_dir).resolve()
        self.required_source_dir_name = required_source_dir_name
        self.tags = tags
        self.bgm_path = Path(bgm_path)
        self.output_path = Path(output_path)

    def run(self):
        try:
            if self.image_dir.name != self.required_source_dir_name:
                raise RuntimeError(f"视频图片源不是“{self.required_source_dir_name}”: {self.image_dir}")
            temp_output_name = f"__bilibili_convert_tmp__{self.output_path.name}"
            temp_output = self.image_dir / temp_output_name
            self.message.emit(f"视频图片源: {self.image_dir}")
            self.message.emit(f"最终视频输出: {self.output_path}")
            out = make_video(
                folder=self.image_dir,
                tags=self.tags,
                bgm_path=str(self.bgm_path),
                duration=2.0,
                transition="fade",
                transition_duration=0.6,
                fps=30,
                fit_mode="blur",
                bgm_volume=0.6,
                show_progress_ring=True,
                output_name=temp_output_name,
                exclude_names={"Z01.png"},
            )
            out = Path(out)
            if out.resolve() != self.output_path.resolve():
                self.output_path.parent.mkdir(parents=True, exist_ok=True)
                if self.output_path.exists():
                    self.output_path.unlink()
                shutil.move(str(out), str(self.output_path))
            if temp_output.exists() and temp_output.resolve() != self.output_path.resolve():
                temp_output.unlink()
            self.finished.emit(str(self.output_path))
        except Exception as exc:
            self.failed.emit(str(exc))


class ImageTile(QFrame):
    clicked = pyqtSignal(Path)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.pixmap = QPixmap(str(path))
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid #ddd; }")
        self.setMinimumWidth(IMAGE_TILE_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.image_label = QLabel()
        self.image_label.setMinimumWidth(IMAGE_TILE_MIN_WIDTH)
        self.image_label.setFixedHeight(IMAGE_PREVIEW_HEIGHT)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.update_preview()
        name = QLabel(path.name)
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        layout.addWidget(self.image_label)
        layout.addWidget(name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_preview()

    def update_preview(self):
        if self.pixmap.isNull():
            return
        self.image_label.setPixmap(
            self.pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.path)

    def set_selected(self, selected):
        self.setStyleSheet(
            "QFrame { border: 3px solid #1677ff; background: #eef5ff; }"
            if selected else
            "QFrame { border: 1px solid #ddd; background: white; }"
        )


class BilibiliConvertWindow(QWidget):
    def __init__(self, root):
        super().__init__()
        self.project_root = get_project_root(root)
        self.no_subtitle_dir = find_no_subtitle_dir(self.project_root)
        self.manga_dir = get_manga_version_dir(self.project_root)
        self.json_path = find_json_path(self.project_root)
        self.items = load_json(self.json_path)
        self.tags = list(DEFAULT_TAGS)
        self.selected_cover = None
        self.selected_bgm = None
        self.tiles = []
        self.thread = None
        self.worker = None

        self.player = QMediaPlayer(self)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.error.connect(self.on_player_error)

        self.setWindowTitle(f"Bilibili Convert - {self.project_root.name}")
        self.resize(1680, 880)
        self.build_ui()
        self.load_images()
        self.refresh_bgm_candidates()

    def build_ui(self):
        root_layout = QVBoxLayout(self)

        summary = QLabel(
            f"输入: {self.project_root}\n"
            f"图片: {self.no_subtitle_dir}\n"
            f"漫画版: {self.manga_dir}\n"
            f"脚本: {self.json_path}\n"
            f"BGM 标签: {', '.join(self.tags)}"
        )
        summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root_layout.addWidget(summary)

        main = QHBoxLayout()
        root_layout.addLayout(main, 1)

        left = QVBoxLayout()
        left.addWidget(QLabel("横屏封面候选图"))
        self.image_grid = QGridLayout()
        self.image_grid.setSpacing(8)
        for column in range(IMAGE_GRID_COLUMNS):
            self.image_grid.setColumnStretch(column, 1)
        image_container = QWidget()
        image_container.setLayout(self.image_grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(image_container)
        left.addWidget(scroll, 1)
        main.addLayout(left, 4)

        right = QVBoxLayout()
        bgm_header = QHBoxLayout()
        bgm_header.addWidget(QLabel("BGM 候选"))
        refresh_bgm_btn = QPushButton("换一批")
        refresh_bgm_btn.clicked.connect(self.refresh_bgm_candidates)
        bgm_header.addWidget(refresh_bgm_btn)
        right.addLayout(bgm_header)
        self.bgm_container = QVBoxLayout()
        right.addLayout(self.bgm_container)
        right.addWidget(QLabel("播放进度"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(self.player.setPosition)
        right.addWidget(self.slider)
        self.status = QTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(190)
        right.addWidget(self.status)
        main.addLayout(right, 2)

        buttons = QHBoxLayout()
        self.cover_label = QLabel("未选择封面")
        self.bgm_label = QLabel("未选择 BGM")
        run_btn = QPushButton("生成横屏封面并转换视频")
        run_btn.clicked.connect(self.convert)
        buttons.addWidget(self.cover_label, 1)
        buttons.addWidget(self.bgm_label, 1)
        buttons.addWidget(run_btn)
        root_layout.addLayout(buttons)

    def log(self, text):
        self.status.append(text)

    def load_images(self):
        names = get_sorted_images(str(self.no_subtitle_dir))
        paths = [self.no_subtitle_dir / name for name in names if (self.no_subtitle_dir / name).suffix.lower() in IMAGE_EXTS]
        for index, path in enumerate(paths):
            tile = ImageTile(path)
            tile.clicked.connect(self.choose_cover)
            self.tiles.append(tile)
            self.image_grid.addWidget(tile, index // IMAGE_GRID_COLUMNS, index % IMAGE_GRID_COLUMNS)
        self.log(f"已加载 {len(paths)} 张无字幕原图。")

    def choose_cover(self, path):
        self.selected_cover = path
        for tile in self.tiles:
            tile.set_selected(tile.path == path)
        self.cover_label.setText(f"封面: {path.name}")

    def clear_bgm_rows(self):
        while self.bgm_container.count():
            item = self.bgm_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def refresh_bgm_candidates(self):
        self.clear_bgm_rows()
        candidates = pick_bgm_candidates(self.tags, limit=5)
        if not candidates:
            self.bgm_container.addWidget(QLabel("没有找到可用 BGM"))
            return
        for _score, key, path, tags in candidates:
            row = self.make_bgm_row(key, path, tags)
            self.bgm_container.addWidget(row)
        self.bgm_container.addStretch()

    def make_bgm_row(self, key, path, tags):
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)
        title = QLabel(f"{path.name}\n标签: {', '.join(tags)}")
        title.setWordWrap(True)
        layout.addWidget(title)

        buttons = QHBoxLayout()
        play = QPushButton("播放")
        choose = QPushButton("选择")
        edit = QPushButton("编辑")
        delete = QPushButton("删除")
        play.clicked.connect(lambda _=False, p=path: self.play_bgm(p))
        choose.clicked.connect(lambda _=False, k=key, p=path: self.choose_bgm(k, p))
        edit.clicked.connect(lambda _=False, k=key, t=tags: self.edit_bgm(k, t))
        delete.clicked.connect(lambda _=False, k=key, p=path: self.delete_bgm(k, p))
        buttons.addWidget(play)
        buttons.addWidget(choose)
        buttons.addWidget(edit)
        buttons.addWidget(delete)
        layout.addLayout(buttons)
        return frame

    def play_bgm(self, path):
        try:
            preview_path = make_bgm_preview_wav(path)
        except Exception as exc:
            QMessageBox.critical(self, "BGM 播放失败", str(exc))
            self.log(f"播放失败: {path.name} -> {exc}")
            return

        self.player.stop()
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(preview_path))))
        self.player.play()
        self.log(f"播放: {path.name}")

    def choose_bgm(self, key, path):
        self.selected_bgm = Path(path)
        self.bgm_label.setText(f"BGM: {path.name}")
        self.log(f"已选择 BGM: {path}")

    def edit_bgm(self, key, tags):
        dialog = BgmEditDialog(key, tags, self)
        if dialog.exec_() == QDialog.Accepted:
            try:
                write_bgm_tags_to_data_py(key, dialog.tags)
                self.log(f"已更新标签: {Path(key).name} -> {', '.join(dialog.tags)}")
                self.refresh_bgm_candidates()
            except Exception as exc:
                QMessageBox.critical(self, "更新失败", str(exc))

    def delete_bgm(self, key, path):
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"将删除音乐文件和 data.py 中的条目:\n{path}",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.player.stop()
            delete_bgm_from_data_py(key)
            if path.exists():
                path.unlink()
            self.log(f"已删除: {path}")
            if self.selected_bgm == path:
                self.selected_bgm = None
                self.bgm_label.setText("未选择 BGM")
            self.refresh_bgm_candidates()
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))

    def on_position_changed(self, position):
        if not self.slider.isSliderDown():
            self.slider.setValue(position)

    def on_duration_changed(self, duration):
        self.slider.setRange(0, duration)

    def on_player_error(self, *_):
        error = self.player.errorString()
        if error:
            self.log(f"播放器错误: {error}")

    def convert(self):
        if not self.selected_cover:
            QMessageBox.warning(self, "缺少封面", "请先点击一张图片作为横屏封面。")
            return
        if not self.selected_bgm:
            QMessageBox.warning(self, "缺少 BGM", "请先选择一个 BGM。")
            return

        cover_path = self.manga_dir / "Z01.png"
        try:
            make_landscape_cover(self.selected_cover, self.json_path, self.items, cover_path)
            self.log(f"已生成横屏封面: {cover_path}")
        except Exception as exc:
            QMessageBox.critical(self, "封面生成失败", str(exc))
            return

        output_path = self.manga_dir / f"{self.project_root.name}.mp4"
        self.log(f"使用 BGM: {self.selected_bgm}")
        self.thread = QThread(self)
        self.worker = ConvertWorker(self.manga_dir, "漫画版", self.tags, self.selected_bgm, output_path)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.message.connect(self.log)
        self.worker.finished.connect(self.on_convert_finished)
        self.worker.failed.connect(self.on_convert_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()
        self.log("开始转换视频...")

    def on_convert_finished(self, output_path):
        self.log(f"视频转换完成: {output_path}")
        try:
            copied_to = copy_project_to_upload(self.project_root)
            self.log(f"已复制到待上传: {copied_to}")
            QMessageBox.information(self, "完成", f"已生成:\n{output_path}\n\n已复制到:\n{copied_to}")
        except Exception as exc:
            self.log(f"复制到待上传失败: {exc}")
            QMessageBox.warning(self, "视频已生成，复制失败", f"已生成:\n{output_path}\n\n复制到 final/待上传 失败:\n{exc}")

    def on_convert_failed(self, error):
        self.log(f"视频转换失败: {error}")
        QMessageBox.critical(self, "转换失败", error)


def choose_root_with_dialog():
    folder = QFileDialog.getExistingDirectory(None, "选择旧作输入文件夹")
    return Path(folder) if folder else None


def resolve_start_folder():
    if len(sys.argv) > 1 and str(sys.argv[1]).strip():
        return Path(sys.argv[1])
    if INPUT_FOLDER.strip():
        return Path(INPUT_FOLDER)
    return choose_root_with_dialog()


def main():
    app = QApplication(sys.argv)
    root = resolve_start_folder()
    if not root:
        return 0
    try:
        window = BilibiliConvertWindow(root)
    except Exception as exc:
        QMessageBox.critical(None, "初始化失败", str(exc))
        return 1
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
