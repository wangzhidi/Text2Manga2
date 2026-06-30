import cairo
import math
import random
def draw_tail(ctx, bbox_x, bbox_y, bbox_width, bbox_height, target_x, target_y, tail_width=20, color=(0,0,0), tail_length=1.0):
    """
    绘制对话框尾巴
    
    参数:
    ctx: cairo 上下文
    bbox_x, bbox_y: 方框左上角坐标
    bbox_width, bbox_height: 方框宽度和高度
    target_x, target_y: 尾巴指向的目标点坐标
    tail_width: 尾巴底边宽度
    color: 尾巴颜色
    length_ratio: 尾巴长度比例 (0-1)，bbox中心到实际绘制的target与bbox中心到输入target之间的距离比值
    """
    # 计算方框中心点
    bbox_center_x = bbox_x + bbox_width / 2
    bbox_center_y = bbox_y + bbox_height / 2
    
    # 计算方向向量（从方框中心指向目标点）
    dx = target_x - bbox_center_x
    dy = target_y - bbox_center_y
    length = math.sqrt(dx*dx + dy*dy)
    
    if length == 0:
        return
    
    # 单位方向向量
    ux = dx / length
    uy = dy / length
    
    # 根据长度比例计算实际的目标点
    actual_target_x = bbox_center_x + dx * tail_length
    actual_target_y = bbox_center_y + dy * tail_length
    
    # 计算垂直向量（用于确定底边的两个端点）
    perp_ux = -uy
    perp_uy = ux
    
    # 计算底边的两个端点
    base_half_width = tail_width / 2
    base_point1_x = bbox_center_x + perp_ux * base_half_width
    base_point1_y = bbox_center_y + perp_uy * base_half_width
    base_point2_x = bbox_center_x - perp_ux * base_half_width
    base_point2_y = bbox_center_y - perp_uy * base_half_width
    
    # 绘制三角形
    ctx.new_path()
    ctx.move_to(base_point1_x, base_point1_y)
    ctx.line_to(base_point2_x, base_point2_y)
    ctx.line_to(actual_target_x, actual_target_y)
    ctx.close_path()
    
    ctx.set_source_rgb(color[0], color[1], color[2])
    ctx.fill()



def draw_dot_tail(ctx,
                  bbox_x, bbox_y, bbox_width, bbox_height,
                  target_x, target_y,
                  scale=2.5,
                  tail_length=0.8):
    cx = bbox_x + bbox_width * 0.5
    cy = bbox_y + bbox_height * 0.5

    # 指向目标的方向向量
    dx = target_x - cx
    dy = target_y - cy

    target_x = cx + dx * tail_length
    target_y = cy + dy * tail_length

    if dx == 0 and dy == 0:
        return
    t_vals = []

    if dx != 0:
        t_vals.append((bbox_x - cx) / dx)
        t_vals.append((bbox_x + bbox_width - cx) / dx)
    if dy != 0:
        t_vals.append((bbox_y - cy) / dy)
        t_vals.append((bbox_y + bbox_height - cy) / dy)

    t_hit = None
    for t in t_vals:
        if t <= 0:
            continue
        x = cx + dx * t
        y = cy + dy * t
        if (bbox_x - 1e-3 <= x <= bbox_x + bbox_width + 1e-3 and
            bbox_y - 1e-3 <= y <= bbox_y + bbox_height + 1e-3):
            if t_hit is None or t < t_hit:
                t_hit = t

    if t_hit is None:
        return

    start_x = cx + dx * t_hit
    start_y = cy + dy * t_hit
    vx = target_x - start_x
    vy = target_y - start_y
    dist = math.hypot(vx, vy)

    if dist < 1e-3:
        return

    ux = vx / dist
    uy = vy / dist
    dot_ratios = [0.25, 0.5, 0.75]   # 位置比例
    base_radius = max(2.5, dist * 0.06) * scale
    radii = [
        base_radius * 0.8,
        base_radius * 0.6,
        base_radius * 0.4,
    ]
    for ratio, r in zip(dot_ratios, radii):
        px = start_x + ux * dist * ratio
        py = start_y + uy * dist * ratio

        # 黑色外圆
        ctx.set_source_rgb(0, 0, 0)
        ctx.arc(px, py, r, 0, 2 * math.pi)
        ctx.fill()

        # 白色内圆
        ctx.set_source_rgb(1, 1, 1)
        ctx.arc(px, py, r * 0.8, 0, 2 * math.pi)
        ctx.fill()


def draw_speech_bubble(ctx, box_type, bbox, target, tail_width=40, *box_args):
    """
    绘制带尾巴的完整对话框
    
    参数:
    ctx: cairo 上下文
    box_type: 方框类型字符串
    bbox: 方框边界框 (x, y, width, height)
    target: 尾巴指向目标 (target_x, target_y)
    tail_width: 尾巴宽度
    *box_args: 传递给方框绘制函数的额外参数
    """
    # 解构参数
    bbox_x, bbox_y, bbox_width, bbox_height = bbox
    bbox_width=bbox[2]-bbox[0]
    bbox_height=bbox[3]-bbox[1]
    target_x, target_y = target
    tail_length=random.random()*0.1+0.55

    if box_type == "ellipse":
        draw_ellipse_box(ctx, bbox_x, bbox_y, bbox_width, bbox_height, *box_args)
        pass    
    elif box_type == "rect":
        draw_tail(ctx, bbox_x, bbox_y, bbox_width, bbox_height, target_x, target_y, tail_width,tail_length=tail_length)
        draw_rounded_rectangle_box(ctx, bbox_x, bbox_y, bbox_width, bbox_height, *box_args)
        draw_tail(ctx, bbox_x, bbox_y, bbox_width, bbox_height, target_x, target_y, tail_width*0.8, color=(1,1,1),tail_length=tail_length-0.05)
    elif box_type == "wavy_rect":
        draw_wavy_rectangle_box(ctx, bbox_x, bbox_y, bbox_width, bbox_height, *box_args)
        draw_dot_tail(ctx, bbox_x, bbox_y, bbox_width, bbox_height, target_x, target_y, tail_length=tail_length)

    else:
        draw_shadowed_rectangle_box(ctx, bbox_x, bbox_y, bbox_width, bbox_height, *box_args)
    
# 方框绘制函数（保持原样）
def draw_ellipse_box(ctx, x, y, width, height):
    """绘制椭圆形状的方框"""
    ctx.new_path()
    ctx.save()
    ctx.translate(x + width/2, y + height/2)
    ctx.scale(width/2, height/2)
    ctx.arc(0, 0, 1, 0, 2 * math.pi)
    ctx.restore()
    
    # 填充和描边
    ctx.set_source_rgb(1, 1, 1)
    ctx.fill_preserve()
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(2)
    ctx.stroke()

def draw_rounded_rectangle_box(ctx, x, y, width, height, corner_radius=5, fill=True):
    """绘制圆角矩形方框"""
    corner_radius = min(corner_radius, width/2, height/2)
    
    ctx.new_path()
    ctx.arc(x + width - corner_radius, y + corner_radius, 
            corner_radius, -math.pi/2, 0)
    ctx.arc(x + width - corner_radius, y + height - corner_radius, 
            corner_radius, 0, math.pi/2)
    ctx.arc(x + corner_radius, y + height - corner_radius, 
            corner_radius, math.pi/2, math.pi)
    ctx.arc(x + corner_radius, y + corner_radius, 
            corner_radius, math.pi, 3*math.pi/2)
    ctx.close_path()
    if fill:
        ctx.set_source_rgb(1, 1, 1)
        ctx.fill_preserve()
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(2)
    ctx.stroke()

def draw_rect(ctx, bbox, fill=False):
    draw_rounded_rectangle_box(ctx, bbox[0], bbox[1], bbox[2]-bbox[0], bbox[3]-bbox[1], fill=fill)

def draw_wavy_rectangle_box(ctx, x, y, width, height, wave_size=20):
    """绘制波浪边缘的矩形方框"""
    ctx.new_path()
    ctx.move_to(x + wave_size, y)
    ctx.curve_to(x + wave_size/2, y - wave_size/2, 
                x + width - wave_size*1.5, y - wave_size, 
                x + width - wave_size, y)
    ctx.curve_to(x + width - wave_size/2, y + wave_size/3, 
                x + width - wave_size, y + wave_size/2, 
                x + width, y + wave_size)
    ctx.curve_to(x + width + wave_size/2, y + wave_size*2, 
                x + width, y + height - wave_size*2, 
                x + width - wave_size, y + height - wave_size)
    ctx.curve_to(x + width - wave_size*2, y + height, 
                x + wave_size*2, y + height + wave_size/2, 
                x + wave_size, y + height - wave_size/2)
    ctx.curve_to(x, y + height - wave_size*1.5, 
                x - wave_size/2, y + wave_size*2, 
                x + wave_size/2, y + wave_size)
    ctx.close_path()
    
    ctx.set_source_rgb(1, 1, 1)
    ctx.fill_preserve()
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(2)
    ctx.stroke()



def draw_shadowed_rectangle_box(ctx, x, y, width, height, shadow_size=5, corner_radius=15):
    """绘制带阴影效果的圆角矩形方框"""
    # 阴影
    ctx.save()
    ctx.translate(shadow_size, shadow_size)
    corner_radius = min(corner_radius, width/2, height/2)
    ctx.new_path()
    ctx.arc(x + width - corner_radius, y + corner_radius, 
            corner_radius, -math.pi/2, 0)
    ctx.arc(x + width - corner_radius, y + height - corner_radius, 
            corner_radius, 0, math.pi/2)
    ctx.arc(x + corner_radius, y + height - corner_radius, 
            corner_radius, math.pi/2, math.pi)
    ctx.arc(x + corner_radius, y + corner_radius, 
            corner_radius, math.pi, 3*math.pi/2)
    ctx.close_path()
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.fill()
    ctx.restore()
    
    # 主体
    ctx.new_path()
    ctx.arc(x + width - corner_radius, y + corner_radius, 
            corner_radius, -math.pi/2, 0)
    ctx.arc(x + width - corner_radius, y + height - corner_radius, 
            corner_radius, 0, math.pi/2)
    ctx.arc(x + corner_radius, y + height - corner_radius, 
            corner_radius, math.pi/2, math.pi)
    ctx.arc(x + corner_radius, y + corner_radius, 
            corner_radius, math.pi, 3*math.pi/2)
    ctx.close_path()
    
    gradient = cairo.LinearGradient(x, y, x + width, y + height)
    gradient.add_color_stop_rgb(0, 1, 1, 1)
    gradient.add_color_stop_rgb(1, 0.95, 0.95, 1)
    ctx.set_source(gradient)
    ctx.fill_preserve()
    ctx.set_source_rgb(0, 0, 0)
    ctx.set_line_width(2)
    ctx.stroke()
    
def draw_text(ctx, bbox, font_pixel, text):
    """
    在指定边界框内绘制自动换行和居中的文本
    （增加对标点、省略号的特殊处理）
    """
    ctx.set_source_rgb(0, 0, 0)
    x1, y1, x2, y2 = bbox
    x1 -= 8  # 向左平移20像素
    x2 -= 8  # 向左平移20像素
    width = x2 - x1
    height = y2 - y1
    ctx.set_source_rgb(0, 0, 0)


    # 字体度量
    ascent, descent, line_height, _, _ = ctx.font_extents()

    # ---------- 字符宽度规则 ----------
    english_pixel = font_pixel * 0.5

    half_punct = set(",.!?")
    full_punct = set("，。！？：；（）【】")

    def char_width(char):
        if '\u4e00' <= char <= '\u9fff':          # 中文
            return font_pixel
        if char in {'…', '～', '—'}:                           # 省略号
            return font_pixel * 1.2
        if char in full_punct:                    # 全角标点
            return font_pixel * 0.9
        if char in half_punct:                    # 半角标点
            return font_pixel * 0.3
        return english_pixel                      # 英文/其他

    # ---------- 分行 ----------
    lines = []
    current_line = ""
    current_width = 0

    for char in text:
        w = char_width(char)

        if current_width + w <= width or not current_line:
            current_line += char
            current_width += w
        else:
            lines.append(current_line)
            current_line = char
            current_width = w

    if current_line:
        lines.append(current_line)

    # ---------- 垂直居中 ----------
    total_text_height = len(lines) * line_height
    start_y = y1 + (height - total_text_height) / 2 + ascent

    # ---------- 绘制 ----------
    for i, line in enumerate(lines):
        line_width = sum(char_width(c) for c in line)

        # 水平居中（你原本的轻微右移我保留）
        start_x = x1 + (width - line_width) / 2 + width * 0.05

        ctx.move_to(start_x, start_y + i * line_height)
        ctx.show_text(line)

    

# 运行示例
if __name__ == "__main__":
    """绘制带尾巴的各种对话框示例"""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 896, 1152)
    ctx = cairo.Context(surface)
    
    ctx.set_source_rgb(0, 0.9, 0.9)
    ctx.paint()
    
    # 椭圆对话框
    draw_speech_bubble(ctx, "ellipse", (100, 100, 300, 400), (0, 0), 25)
    
    # 圆角矩形对话框
    draw_speech_bubble(ctx, "rect", (200, 500, 500, 800), (800, 250), 25)
    
    # 添加标签
    ctx.set_source_rgb(0.3, 0.3, 0.3)
    ctx.select_font_face("YouYuan", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(36)
    text="椭圆对话框椭圆对话框椭圆对话框椭圆对话框"
    draw_text(ctx, (100, 100, 300, 400), 42, text)
    text="椭圆对话框椭圆对话框roundroundround椭圆对话框椭圆对话框"
    draw_text(ctx, (200, 500, 500, 800), 42, text)
    
    surface.write_to_png('speech_bubbles_with_tails.png')

    