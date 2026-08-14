import math
import cairo
import json
from PIL import Image


def _load_cover_meta(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)

    if not json_data:
        raise ValueError("JSON 文件为空")

    meta_item = next((item for item in json_data if item.get("id") == -1), None)
    if meta_item is None:
        meta_item = json_data[0]

    title = meta_item.get('title', '')
    original_author = meta_item.get('original_author', '')
    platform = meta_item.get('platform', '')
    url = meta_item.get('url', '')

    if not title:
        raise ValueError("未能从 JSON 中读取到 title，请确认 id=-1 的元数据块已生成")

    return title, original_author, platform, url


def _split_title_one_or_two_lines(text):
    """Match the vertical cover title split: short titles stay whole; long titles split once."""
    text = str(text or "")
    if len(text) <= 9:
        return [text]

    separators = [',', '，', ' ', '_', '、', '—', '-', ':', '：']
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            return [parts[0], parts[1] if len(parts) > 1 else ""]

    mid = len(text) // 2
    return [text[:mid], text[mid:]]


def make_manga_cover(
    img_path,
    json_path,
    output_path,
    manga_author="很大只狸花",
):
    # ==== 读取 JSON 元信息 ====
    title, original_author, platform, url = _load_cover_meta(json_path)
    
    # ==== 读取原图 ====
    img = Image.open(img_path).convert("RGBA")
    width, height = img.size

    # 转换为 cairo 可用的 ARGB32 数据
    img_data = bytearray(img.tobytes("raw", "BGRA"))
    surface = cairo.ImageSurface.create_for_data(
        img_data,
        cairo.FORMAT_ARGB32,
        width,
        height,
        width * 4,
    )
    ctx = cairo.Context(surface)

    # =========================
    # 公共描边文字函数
    # =========================
    def draw_stroked_text(text, x, y, font_size=40,
                          fill_color=(1, 1, 1),
                          stroke_color=(0, 0, 0),
                          stroke_width=3,
                          rotate_angle=0):
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(rotate_angle)

        ctx.select_font_face(
            "HYWenHei 55W",
            cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD
        )
        ctx.set_font_size(font_size)

        ctx.text_path(text)

        # 描边
        ctx.set_source_rgb(*stroke_color)
        ctx.set_line_width(stroke_width)
        ctx.stroke_preserve()

        # 填充
        ctx.set_source_rgb(*fill_color)
        ctx.fill()

        ctx.restore()

    # =========================
    # 右侧竖排标题
    # =========================
    # 参数可自行微调
    # 自适应字体大小（按每一半分别计算）
    def adjust_font_size(text_part):
        title_length = len(text_part)
        if title_length > 15:
            return int(height * 0.04)  # 进一步减小字体
        elif title_length > 9:
            return int(height * 0.055)  # 减小字体
        return int(height * 0.07)  # 默认字体大小

    # 拆分 title 的逻辑
    def split_title(text):
        """根据分隔符拆分 title，如果无分隔符则二等分"""
        if len(text) <= 9:
            return text, ""
        
        # 定义分隔符
        separators = [',', '，', ' ', '_', '、', '—', '-', ':', '：']
        
        # 尝试按照分隔符拆分
        for sep in separators:
            if sep in text:
                parts = text.split(sep, 1)  # 仅在第一个分隔符处拆分
                return parts[0], parts[1] if len(parts) > 1 else ""
        
        # 如果没有分隔符，二等分
        mid = len(text) // 2
        return text[:mid], text[mid:]
    
    title_top, title_bottom = split_title(title)
    top_font_size = adjust_font_size(title_top)
    bottom_font_size = adjust_font_size(title_bottom) if title_bottom else top_font_size
    top_line_spacing = top_font_size * 1.2
    bottom_line_spacing = bottom_font_size * 1.2
    
    # 从左侧往内侧偏移
    if len(title) <= 9:
        left_margin = width * 0.20
    else:
        left_margin = width * 0.10
    
    # 上半部分：贴近图片上边缘，显示在左侧
    start_y_top = height * 0.12
    column_spacing = width * 0.15  # 两列之间的间距
    
    # 逐字竖排 - 上半部分
    for i, char in enumerate(title_top):
        draw_stroked_text(
            char,
            left_margin,
            start_y_top + i * top_line_spacing,
            font_size=top_font_size,
            fill_color=(1, 1, 1),     # 白字
            stroke_color=(0, 0, 0),   # 黑描边
            stroke_width=top_font_size * 0.2,
        )
    
    # 下半部分：贴近图片下边缘，显示在左侧（第二列）
    if title_bottom:
        # 计算下半部分的起始 y 位置，使其贴近下边缘
        start_y_bottom = height * 0.95 - len(title_bottom) * bottom_line_spacing
        
        # 逐字竖排 - 下半部分
        for i, char in enumerate(title_bottom):
            draw_stroked_text(
                char,
                left_margin + column_spacing,
                start_y_bottom + i * bottom_line_spacing,
                font_size=bottom_font_size,
                fill_color=(1, 1, 1),     # 黑字
                stroke_color=(0, 0, 0),   # 白描边
                stroke_width=bottom_font_size * 0.2,
            )

    # =========================
    # 右下角横排说明文字
    # =========================
    # footer_text = f"此漫画由AI生成\n感谢{platform}上{original_author}的原文：\n{url}"
    footer_lines = ["AI生成"]
    if str(original_author or "").strip():
        footer_lines.append(f"原文作者:{str(original_author).strip()}")
    footer_lines.append(f"漫画作者:{manga_author}")
    footer_text = "\n".join(footer_lines)
    # footer_text = f"AI生成\n原文作者:{original_author}"
    # footer_text = f"AI生成"
    footer_font_size = int(height * 0.035)
    footer_url_font_size = int(footer_font_size * 0.8)
    footer_lines = footer_text.split("\n")

    bottom_margin = height * 0.90
    right_margin_limit = width * 0.95
    line_height = footer_font_size * 1.3

    for i, line in enumerate(reversed(footer_lines)):
        current_font_size = footer_url_font_size if line == url else footer_font_size
        
        # 计算文字宽度以进行右对齐
        ctx.save()
        ctx.select_font_face(
            "HYWenHei 55W",
            cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD
        )
        ctx.set_font_size(current_font_size)
        x_bearing, y_bearing, w, h, x_advance, y_advance = ctx.text_extents(line)
        ctx.restore()

        draw_stroked_text(
            line,
            right_margin_limit - w - x_bearing,
            bottom_margin - i * line_height,
            font_size=current_font_size,
            fill_color=(0, 0, 0),
            stroke_color=(1, 1, 1),
            stroke_width=footer_font_size * 0.1,
        )

    # =========================
    # 输出（支持 jpg/jpeg/png）
    # =========================
    ext = str(output_path).lower().rsplit('.', 1)[-1] if '.' in str(output_path) else "png"
    if ext in ("jpg", "jpeg"):
        raw = bytes(surface.get_data())
        out_img = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA")
        out_img.convert("RGB").save(output_path, format="JPEG", quality=95, optimize=True)
    else:
        surface.write_to_png(output_path)


def make_manga_cover_landscape(
    img_path,
    json_path,
    output_path,
    manga_author="很大只狸花",
):
    title, original_author, platform, url = _load_cover_meta(json_path)

    # 1) 中央裁切为 4:3 横屏底图
    src = Image.open(img_path).convert("RGBA")
    src_w, src_h = src.size
    target_ratio = 4 / 3
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        crop_h = src_h
        crop_w = int(round(crop_h * target_ratio))
        left = (src_w - crop_w) // 2
        top = 0
    else:
        crop_w = src_w
        crop_h = int(round(crop_w / target_ratio))
        left = 0
        top = (src_h - crop_h) // 2

    img = src.crop((left, top, left + crop_w, top + crop_h))
    width, height = img.size

    # 转换为 cairo 可用的 ARGB32 数据
    img_data = bytearray(img.tobytes("raw", "BGRA"))
    surface = cairo.ImageSurface.create_for_data(
        img_data,
        cairo.FORMAT_ARGB32,
        width,
        height,
        width * 4,
    )
    ctx = cairo.Context(surface)

    def draw_stroked_text(text, x, y, font_size=40,
                          fill_color=(1, 1, 1),
                          stroke_color=(0, 0, 0),
                          stroke_width=3):
        ctx.save()
        ctx.select_font_face(
            "HYWenHei 55W",
            cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD
        )
        ctx.set_font_size(font_size)
        ctx.move_to(x, y)
        ctx.text_path(text)
        ctx.set_source_rgb(*stroke_color)
        ctx.set_line_width(stroke_width)
        ctx.stroke_preserve()
        ctx.set_source_rgb(*fill_color)
        ctx.fill()
        ctx.restore()

    # 2) Title: lower-left middle area, left aligned, 1-2 lines.
    title_lines = [line for line in _split_title_one_or_two_lines(title) if line]
    if not title_lines:
        title_lines = [title]

    title_font_size = int(height * (0.11 if len(title_lines) == 1 else 0.09))
    title_line_height = title_font_size * 1.2
    title_left = width * 0.02
    # Title in the lower-left middle area.
    title_center_y = height * 0.64

    ctx.save()
    ctx.select_font_face(
        "HYWenHei 55W",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD
    )
    ctx.set_font_size(title_font_size)
    title_ascent, title_descent, _, _, _ = ctx.font_extents()
    ctx.restore()

    title_block_height = title_ascent + title_descent + (len(title_lines) - 1) * title_line_height
    first_title_baseline = title_center_y - title_block_height / 2 + title_ascent

    for i, line in enumerate(title_lines):
        baseline_y = first_title_baseline + i * title_line_height
        ctx.save()
        ctx.select_font_face(
            "HYWenHei 55W",
            cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD
        )
        ctx.set_font_size(title_font_size)
        x_bearing, y_bearing, w, h, x_advance, y_advance = ctx.text_extents(line)
        ctx.restore()

        draw_stroked_text(
            line,
            title_left - x_bearing,
            baseline_y,
            font_size=title_font_size,
            fill_color=(1, 1, 1),
            stroke_color=(0, 0, 0),
            stroke_width=max(2, title_font_size * 0.12),
        )

    # 3) Annotation block: upper-left middle area, left aligned.
    footer_lines = [
        "AI生成",
        f"原文作者:{str(original_author or '').strip()}",
        f"漫画改编:{manga_author}",
    ]

    footer_font_size = int(height * 0.06)
    footer_line_height = footer_font_size * 1.25
    footer_left = title_left
    footer_top_baseline = height * 0.34

    ctx.save()
    ctx.select_font_face(
        "HYWenHei 55W",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD
    )
    ctx.set_font_size(footer_font_size)
    footer_ascent, footer_descent, _, _, _ = ctx.font_extents()
    ctx.restore()

    start_y = footer_top_baseline
    footer_bottom_boundary = start_y + (len(footer_lines) - 1) * footer_line_height + footer_descent
    title_top_boundary = first_title_baseline - title_ascent
    separator_y = (footer_bottom_boundary + title_top_boundary) / 2
    separator_x1 = title_left
    separator_x2 = title_left + width * 0.48

    ctx.save()
    ctx.set_line_cap(cairo.LINE_CAP_BUTT)
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(max(4, height * 0.014))
    ctx.move_to(separator_x1, separator_y)
    ctx.line_to(separator_x2, separator_y)
    ctx.stroke()
    ctx.set_source_rgb(1, 1, 1)
    ctx.set_line_width(max(2, height * 0.008))
    ctx.move_to(separator_x1, separator_y)
    ctx.line_to(separator_x2, separator_y)
    ctx.stroke()
    ctx.restore()

    for i, line in enumerate(footer_lines):
        ctx.save()
        ctx.select_font_face(
            "HYWenHei 55W",
            cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD
        )
        ctx.set_font_size(footer_font_size)
        x_bearing, y_bearing, w, h, x_advance, y_advance = ctx.text_extents(line)
        ctx.restore()

        draw_stroked_text(
            line,
            footer_left - x_bearing,
            start_y + i * footer_line_height,
            font_size=footer_font_size,
            fill_color=(0, 0, 0),
            stroke_color=(1, 1, 1),
            stroke_width=max(1, footer_font_size * 0.08),
        )

    ext = str(output_path).lower().rsplit('.', 1)[-1] if '.' in str(output_path) else "png"
    if ext in ("jpg", "jpeg"):
        raw = bytes(surface.get_data())
        out_img = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA")
        out_img.convert("RGB").save(output_path, format="JPEG", quality=95, optimize=True)
    else:
        surface.write_to_png(output_path)

if __name__ == "__main__":
    make_manga_cover(
        img_path=r"image\001\原神\1_1.jpg",
        json_path=r"script\001\原神.json",
        output_path=r"with_text\001\原神_final\000.jpg"
    )
