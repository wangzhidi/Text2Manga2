"""将一个漫画图片文件夹合成为竖屏 MP4 视频。

输入：按文件名排好序的图片文件夹（第一张为封面，沿用 concate.get_sorted_images 的排序规则），
输出：`<图片文件夹>/<文件夹名>.mp4`，分辨率固定为竖屏 832x1216，按标签从 data.bgm_map 中随机选取背景音乐。

用法：直接修改下面“配置”部分的参数，然后运行 `python manga_to_video.py` 即可。
"""

import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import imageio_ffmpeg

from concate import get_sorted_images
from data import get_bgm_paths

# ============================================================================
# 配置：直接修改这里的参数，然后运行本文件
# ============================================================================
FOLDER = r"final\原图一期\寡妇荧妹和女儿派\漫画版"  # 图片文件夹路径
TAGS = ["原神"]  # 用于挑选 BGM 的标签（任意命中即可），留空 [] 或 None 则从全部 BGM 中随机选取
DURATION = 2.0  # 每张图片展示时长（秒）
TRANSITION = "fade"  # 切换方式，见下方 ALL_TRANSITION_CHOICES；"random" 表示每次切换随机挑选
TRANSITION_DURATION = 0.4  # 切换动画时长（秒），设为 0 等价于硬切
FPS = 30  # 视频帧率
FIT_MODE = "blur"  # 非 832x1216 比例图片的留白填充方式："blur"(模糊背景) / "white" / "black"
BGM_VOLUME = 0.6  # 背景音乐音量比例
SHOW_PROGRESS_RING = True  # 是否在画面左侧显示翻页倒计时圆环
OUTPUT_NAME = None  # 输出文件名，None 则使用 "<文件夹名>.mp4"
SEED = None  # 随机数种子，固定后可复现切换方式与 BGM 选择

WIDTH, HEIGHT = 832, 1216

# 可用的图片切换方式
TRANSITIONS = [
    "fade",
    "slide_left", "slide_right", "slide_up", "slide_down",
    "wipe_left", "wipe_right", "wipe_up", "wipe_down",
    "zoom_in",
]
ALL_TRANSITION_CHOICES = ["cut", *TRANSITIONS, "random"]


# ---------------------------------------------------------------------------
# 图片适配画布
# ---------------------------------------------------------------------------

def _to_rgb(img: Image.Image) -> Image.Image:
    """将任意模式的图片转换为 RGB，带透明通道的图片合成到白底上。"""
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode == "P":
        return _to_rgb(img.convert("RGBA"))
    return img.convert("RGB")


def fit_to_canvas(img: Image.Image, size=(WIDTH, HEIGHT), mode: str = "blur") -> Image.Image:
    """将图片缩放并居中放到目标分辨率画布上。

    多数输入图片本身已是 832x1216，直接返回；少数方形/异形图片按比例缩放后
    居中放置，空白区域按 mode 填充：
      - "blur":  用图片自身放大模糊后的版本作背景（默认，观感最自然）
      - "white": 白色背景
      - "black": 黑色背景
    """
    img = _to_rgb(img)
    target_w, target_h = size
    if img.size == size:
        return img

    src_w, src_h = img.size
    contain_scale = min(target_w / src_w, target_h / src_h)
    contain_size = (max(1, round(src_w * contain_scale)), max(1, round(src_h * contain_scale)))
    contained = img.resize(contain_size, Image.LANCZOS)

    if mode == "blur":
        cover_scale = max(target_w / src_w, target_h / src_h)
        cover_size = (max(1, round(src_w * cover_scale)), max(1, round(src_h * cover_scale)))
        background = img.resize(cover_size, Image.LANCZOS)
        left = (background.width - target_w) // 2
        top = (background.height - target_h) // 2
        background = background.crop((left, top, left + target_w, top + target_h))
        background = background.filter(ImageFilter.GaussianBlur(30))
    elif mode == "white":
        background = Image.new("RGB", size, (255, 255, 255))
    else:
        background = Image.new("RGB", size, (0, 0, 0))

    background.paste(contained, ((target_w - contained.width) // 2, (target_h - contained.height) // 2))
    return background


# ---------------------------------------------------------------------------
# 切换效果：每个函数接收 (上一帧数组, 下一帧数组, 进度 0~1) 返回合成帧数组
# ---------------------------------------------------------------------------

def _fade(prev: np.ndarray, nxt: np.ndarray, p: float) -> np.ndarray:
    return (prev.astype(np.float32) * (1 - p) + nxt.astype(np.float32) * p).astype(np.uint8)


def _slide(prev: np.ndarray, nxt: np.ndarray, p: float, direction: str) -> np.ndarray:
    h, w = prev.shape[:2]
    canvas = np.empty_like(prev)
    if direction == "left":
        offset = int(round(p * w))
        canvas[:, :w - offset] = prev[:, offset:] if offset else prev
        if offset:
            canvas[:, w - offset:] = nxt[:, :offset]
    elif direction == "right":
        offset = int(round(p * w))
        canvas[:, offset:] = prev[:, :w - offset] if offset else prev
        if offset:
            canvas[:, :offset] = nxt[:, w - offset:]
    elif direction == "up":
        offset = int(round(p * h))
        canvas[:h - offset, :] = prev[offset:, :] if offset else prev
        if offset:
            canvas[h - offset:, :] = nxt[:offset, :]
    else:  # down
        offset = int(round(p * h))
        canvas[offset:, :] = prev[:h - offset, :] if offset else prev
        if offset:
            canvas[:offset, :] = nxt[h - offset:, :]
    return canvas


def _wipe(prev: np.ndarray, nxt: np.ndarray, p: float, direction: str, feather: int = 40) -> np.ndarray:
    h, w = prev.shape[:2]
    horizontal = direction in ("left", "right")
    length = w if horizontal else h
    edge = p * length
    coords = np.arange(length, dtype=np.float32)
    if direction in ("right", "down"):
        d = edge - coords
    else:  # left, up：擦除边界从远端向近端推进
        d = coords - (length - edge)
    alpha = np.clip(d / feather + 0.5, 0.0, 1.0)
    mask = alpha[np.newaxis, :, np.newaxis] if horizontal else alpha[:, np.newaxis, np.newaxis]
    return (prev.astype(np.float32) * (1 - mask) + nxt.astype(np.float32) * mask).astype(np.uint8)


def _zoom_in(prev: np.ndarray, nxt_img: Image.Image, p: float, zoom_start: float = 1.25) -> np.ndarray:
    w, h = nxt_img.size
    scale = zoom_start + (1.0 - zoom_start) * p
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = nxt_img.resize((new_w, new_h), Image.LANCZOS)
    left, top = (new_w - w) // 2, (new_h - h) // 2
    cropped = resized.crop((left, top, left + w, top + h))
    nxt_arr = np.asarray(cropped, dtype=np.float32)
    return (prev.astype(np.float32) * (1 - p) + nxt_arr * p).astype(np.uint8)


def render_transition(prev: np.ndarray, nxt: np.ndarray, nxt_img: Image.Image, p: float, name: str) -> np.ndarray:
    if name == "fade":
        return _fade(prev, nxt, p)
    if name.startswith("slide_"):
        return _slide(prev, nxt, p, name[len("slide_"):])
    if name.startswith("wipe_"):
        return _wipe(prev, nxt, p, name[len("wipe_"):])
    if name == "zoom_in":
        return _zoom_in(prev, nxt_img, p)
    return nxt


def resolve_transitions(transition: str, count: int, rng: random.Random) -> list[str]:
    if count <= 0:
        return []
    if transition == "random":
        return [rng.choice(TRANSITIONS) for _ in range(count)]
    return [transition] * count


# ---------------------------------------------------------------------------
# 翻页倒计时圆环：画面左侧居中的扇形进度条，从空圈逐渐填满为绿色实心圆，
# 代表当前页停留时间已用掉的比例（填满之时即为翻页时刻）
# ---------------------------------------------------------------------------

RING_DIAMETER = 56
RING_MARGIN_LEFT = 16
RING_COLOR = (255, 255, 255)
RING_ALPHA = 255
RING_STROKE_WIDTH = 8


def render_progress_ring_sprites(steps: int, supersample: int = 4) -> list[np.ndarray]:
    """预渲染 steps 张不同填充比例的圆环精灵图（RGBA numpy 数组）。

    下标 0 对应该页刚显示（扇形接近全空），下标 steps-1 对应即将翻页（扇形填满整圆）。
    一次性按超采样绘制再缩小，让圆弧边缘平滑；同一时长的圆环在每一页都复用，避免逐帧绘制。
    """
    pad = 4
    size = RING_DIAMETER + pad * 2
    big = size * supersample
    bbox = [pad * supersample, pad * supersample,
            (pad + RING_DIAMETER) * supersample, (pad + RING_DIAMETER) * supersample]

    sprites = []
    for i in range(steps):
        progress = (i + 1) / steps
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        stroke_width = max(1, RING_STROKE_WIDTH * supersample)
        if progress >= 0.999:
            draw.ellipse(bbox, outline=(*RING_COLOR, RING_ALPHA), width=stroke_width)
        else:
            draw.arc(
                bbox,
                start=-90,
                end=-90 + 360 * progress,
                fill=(*RING_COLOR, RING_ALPHA),
                width=stroke_width,
            )
        sprites.append(np.asarray(img.resize((size, size), Image.LANCZOS), dtype=np.uint8))
    return sprites


def progress_ring_position(sprite_size: int) -> tuple[int, int]:
    """精灵图左上角坐标：贴近左边缘、垂直居中。"""
    return RING_MARGIN_LEFT, (HEIGHT - sprite_size) // 2


def composite_sprite(frame: np.ndarray, sprite: np.ndarray, top_left: tuple[int, int]) -> np.ndarray:
    """将带 alpha 通道的精灵图叠加到帧的指定位置（返回新数组，不修改原帧）。"""
    x, y = top_left
    h, w = sprite.shape[:2]
    out = frame.copy()
    region = out[y:y + h, x:x + w].astype(np.float32)
    rgb = sprite[..., :3].astype(np.float32)
    alpha = sprite[..., 3:4].astype(np.float32) / 255.0
    out[y:y + h, x:x + w] = (region * (1 - alpha) + rgb * alpha).astype(np.uint8)
    return out


def generate_frames(fitted_images, transitions, hold_frames, trans_frames,
                     progress_sprites=None, progress_pos=None):
    arrays = [np.asarray(img, dtype=np.uint8) for img in fitted_images]
    last_index = len(arrays) - 1
    for i, arr in enumerate(arrays):
        show_ring = progress_sprites is not None and i < last_index
        for f in range(hold_frames):
            yield composite_sprite(arr, progress_sprites[f], progress_pos) if show_ring else arr
        if i == last_index:
            continue
        name = transitions[i]
        if name == "cut" or trans_frames <= 0:
            continue
        for f in range(trans_frames):
            p = (f + 1) / trans_frames
            yield render_transition(arr, arrays[i + 1], fitted_images[i + 1], p, name)


def count_total_frames(image_count, transitions, hold_frames, trans_frames):
    return image_count * hold_frames + sum(trans_frames for t in transitions if t != "cut")


# ---------------------------------------------------------------------------
# BGM 选择
# ---------------------------------------------------------------------------

def pick_bgm(tags, rng: random.Random) -> Path | None:
    candidates = [p for p in get_bgm_paths(include_tags=tags or None) if p.exists()]
    if not candidates:
        candidates = [p for p in get_bgm_paths() if p.exists()]
    return rng.choice(candidates) if candidates else None


# ---------------------------------------------------------------------------
# ffmpeg 编码
# ---------------------------------------------------------------------------

# 画质优先：低 CRF + slow 预设 + 动画调优，不考虑文件体积
VIDEO_CODEC_ARGS = [
    "-c:v", "h264_nvenc", "-preset", "slow", "-tune", "hq", "-cq", "14", "-b:v", "0", "-pix_fmt", "yuv420p",
]


def build_ffmpeg_cmd(ffmpeg_exe, fps, size, bgm_path, video_seconds, bgm_volume, output_path):
    width, height = size
    cmd = [
        ffmpeg_exe, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
    ]
    if bgm_path is not None:
        fade_duration = max(0.1, min(2.5, video_seconds / 4))
        fade_start = max(0.0, video_seconds - fade_duration)
        audio_filter = f"volume={bgm_volume},afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}"
        cmd += [
            "-stream_loop", "-1", "-i", str(bgm_path),
            "-map", "0:v:0", "-map", "1:a:0",
            *VIDEO_CODEC_ARGS,
            "-c:a", "aac", "-b:a", "256k", "-af", audio_filter,
            "-shortest",
        ]
    else:
        cmd += ["-an", *VIDEO_CODEC_ARGS]
    cmd += ["-movflags", "+faststart", str(output_path)]
    return cmd


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def make_video(
    folder,
    tags=None,
    bgm_path: str | Path | None = None,
    exclude_names: set[str] | list[str] | tuple[str, ...] | None = None,
    duration: float = 3.0,
    transition: str = "random",
    transition_duration: float = 0.6,
    fps: int = 30,
    fit_mode: str = "blur",
    bgm_volume: float = 0.6,
    show_progress_ring: bool = True,
    output_name: str | None = None,
    seed: int | None = None,
) -> Path:
    folder = Path(folder)
    files = get_sorted_images(str(folder))
    if exclude_names:
        exclude_set = {str(name).lower() for name in exclude_names}
        files = [name for name in files if name.lower() not in exclude_set]
    if not files:
        raise ValueError(f"未在 {folder} 中找到图片")
    if transition not in ALL_TRANSITION_CHOICES:
        raise ValueError(f"不支持的切换方式: {transition}，可选: {', '.join(ALL_TRANSITION_CHOICES)}")

    rng = random.Random(seed)

    print(f"[加载] 共 {len(files)} 张图片，正在适配画布 {WIDTH}x{HEIGHT} ...")
    fitted = [fit_to_canvas(Image.open(folder / name), mode=fit_mode) for name in files]

    transitions = resolve_transitions(transition, len(fitted) - 1, rng)
    if transitions:
        print(f"[切换] {' -> '.join(transitions)}")

    hold_frames = max(1, round(duration * fps))
    trans_frames = max(0, round(transition_duration * fps))
    total_frames = count_total_frames(len(fitted), transitions, hold_frames, trans_frames)
    video_seconds = total_frames / fps
    print(f"[时长] 约 {video_seconds:.1f} 秒，共 {total_frames} 帧 @ {fps}fps")

    progress_sprites = progress_pos = None
    if show_progress_ring:
        print("[翻页倒计时] 正在预渲染左侧圆环进度条 ...")
        progress_sprites = render_progress_ring_sprites(hold_frames)
        progress_pos = progress_ring_position(progress_sprites[0].shape[0])

    bgm_path = Path(bgm_path) if bgm_path else pick_bgm(tags, rng)
    print(f"[BGM] 选用: {bgm_path.name}" if bgm_path else "[BGM] 未找到可用音乐，将生成无声视频")

    output_path = folder / (output_name or f"{folder.name}.mp4")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = build_ffmpeg_cmd(ffmpeg_exe, fps, (WIDTH, HEIGHT), bgm_path, video_seconds, bgm_volume, output_path)

    print(f"[编码] 输出到 {output_path}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    report_every = max(1, fps * 5)
    try:
        frames = generate_frames(fitted, transitions, hold_frames, trans_frames,
                                 progress_sprites=progress_sprites, progress_pos=progress_pos)
        for idx, frame in enumerate(frames, start=1):
            proc.stdin.write(frame.tobytes())
            if idx % report_every == 0 or idx == total_frames:
                print(f"  渲染进度: {idx}/{total_frames}")
        proc.stdin.close()
    except BrokenPipeError:
        pass
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"ffmpeg 编码失败，退出码 {ret}（详情见上方 ffmpeg 输出）")

    print(f"[完成] {output_path}")
    return output_path


if __name__ == "__main__":
    make_video(
        folder=FOLDER,
        tags=TAGS,
        duration=DURATION,
        transition=TRANSITION,
        transition_duration=TRANSITION_DURATION,
        fps=FPS,
        fit_mode=FIT_MODE,
        bgm_volume=BGM_VOLUME,
        show_progress_ring=SHOW_PROGRESS_RING,
        output_name=OUTPUT_NAME,
        seed=SEED,
    )
