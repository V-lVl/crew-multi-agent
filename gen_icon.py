"""Crew 桌面 icon —— Raft 牛皮纸手作风 v2

设计定型（v2 采纳 vision 反馈）：
  1. 单层不规则手绘描边（去掉双线）
  2. 无底部文字（app 名交给系统）
  3. 主图：手写感 C（有起收笔墨迹）+ 两张错位便利贴（暗示多 agent）
  4. 便利贴：翘角 + 投影 + 略斜
  5. 黄色拉高到 #FFD60A
  6. 纸纹降 60%
"""
from __future__ import annotations

import io
import math
import random
import struct
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

# 色板
BG = (246, 239, 225)     # 牛皮纸 #f6efe1
INK = (26, 26, 26)       # 黑 #1a1a1a
ACCENT = (245, 197, 24)  # 3M 便签芥末黄 #F5C518（比 v2 更贴牛皮纸调性）
ACCENT2 = (222, 170, 12) # 深芥末 #DEAA0C（副便签）
INK_SOFT = (60, 50, 40, 220)  # 便签上手写线条（比纯黑淡一点）

OUT_ICO = Path(__file__).parent / "static" / "crew.ico"
OUT_PNG = Path(__file__).parent / "static" / "crew.png"


# ─────────────────────────── 绘图工具 ───────────────────────────
def _jitter(v: float, amp: float = 1.0, seed: int = 0) -> float:
    """给坐标加一点手抖"""
    random.seed(seed)
    return v + random.uniform(-amp, amp)


def _hand_rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, width: int, fill=None, seed: int = 1):
    """手绘感圆角方 —— 用多条微微抖动的线段拼出来。

    box: (x0, y0, x1, y1)
    """
    x0, y0, x1, y1 = box
    if fill is not None:
        # 先填色（不做手绘感，只描边手绘）
        draw.rounded_rectangle(box, radius=radius, fill=fill)

    # 手绘描边：多次画同一路径，每次略微抖动
    random.seed(seed)
    for i in range(3):  # 3 pass，模拟笔粗细不均
        jitter_amp = width * 0.15
        w = max(1, width - i)
        # 画四条边（每条都手抖）
        pts = _rounded_rect_points(x0, y0, x1, y1, radius, seg=64)
        # 抖动
        jittered = [
            (p[0] + random.uniform(-jitter_amp, jitter_amp),
             p[1] + random.uniform(-jitter_amp, jitter_amp))
            for p in pts
        ]
        # 画连接线
        for a, b in zip(jittered, jittered[1:] + jittered[:1]):
            draw.line([a, b], fill=INK, width=w)


def _rounded_rect_points(x0, y0, x1, y1, r, seg=64):
    """圆角矩形描边采样点（顺时针）"""
    pts = []
    corners = [
        (x0 + r, y0 + r, 180, 270),  # 左上
        (x1 - r, y0 + r, 270, 360),  # 右上
        (x1 - r, y1 - r, 0, 90),     # 右下
        (x0 + r, y1 - r, 90, 180),   # 左下
    ]
    for cx, cy, a_start, a_end in corners:
        for i in range(seg // 4):
            t = a_start + (a_end - a_start) * i / (seg // 4)
            rad = math.radians(t)
            pts.append((cx + r * math.cos(rad), cy + r * math.sin(rad)))
    return pts


def _draw_c(draw: ImageDraw.ImageDraw, cx: float, cy: float, radius: float, stroke: int, seed: int = 42):
    """手写感 C：主弧 + 起收笔墨点"""
    bbox = [(cx - radius, cy - radius), (cx + radius, cy + radius)]

    # 主弧：多层叠加模拟毛笔（细→粗→细）
    for w_ratio in (0.75, 1.0, 0.9):
        w = max(1, int(stroke * w_ratio))
        draw.arc(bbox, start=42, end=318, fill=INK, width=w)

    # 起笔（上）：一个略大的椭圆墨点
    top_ang = math.radians(42)
    tx = cx + radius * math.cos(-top_ang)
    ty = cy + radius * math.sin(-top_ang)
    dot_r = stroke * 0.6
    draw.ellipse([(tx - dot_r, ty - dot_r), (tx + dot_r * 1.1, ty + dot_r)], fill=INK)

    # 收笔（下）：稍小的墨点 + 一点点甩笔（右下方向短线）
    bot_ang = math.radians(-42)
    bx = cx + radius * math.cos(-bot_ang)
    by = cy - radius * math.sin(-bot_ang)
    draw.ellipse([(bx - dot_r * 0.9, by - dot_r * 0.9), (bx + dot_r, by + dot_r)], fill=INK)
    # 甩笔：一段短的锥形笔画
    tail_len = stroke * 0.8
    draw.line([(bx + dot_r * 0.5, by + dot_r * 0.2),
               (bx + dot_r * 0.5 + tail_len * 0.7, by + dot_r * 0.2 + tail_len * 0.3)],
              fill=INK, width=max(1, int(stroke * 0.55)))


def _draw_sticky_note(img: Image.Image, cx: float, cy: float, size_r: float, angle_deg: float,
                       color: tuple, stroke: int, fold_corner: str = "tr", with_lines: bool = False):
    """画一张便利贴 —— 略斜 + 翘角 + 投影

    cx, cy: 中心
    size_r: 便签边长的一半（即半径）
    angle_deg: 旋转角度
    color: 填色
    fold_corner: 翘角在哪个角 'tr' / 'tl' / 'br' / 'bl'
    with_lines: True 时便签上画 2-3 条手写横线（模拟便签上的字）
    """
    draw = ImageDraw.Draw(img)
    ang = math.radians(angle_deg)
    cos_a, sin_a = math.cos(ang), math.sin(ang)

    # 四个角（未旋转）
    corners_local = [(-size_r, -size_r), (size_r, -size_r),
                     (size_r, size_r), (-size_r, size_r)]
    corners = [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)
               for x, y in corners_local]

    # 投影：往下右偏移
    shadow_off = stroke * 0.8
    shadow_pts = [(p[0] + shadow_off, p[1] + shadow_off) for p in corners]
    shadow_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow_img)
    sdraw.polygon(shadow_pts, fill=(0, 0, 0, 70))
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=stroke * 0.6))
    img.alpha_composite(shadow_img)

    # 主体（黄色）
    draw.polygon(corners, fill=color)

    # 翘角：把 fold_corner 那个角裁掉一个三角，露出下面（透明）
    fold_map = {"tr": 1, "tl": 0, "br": 2, "bl": 3}
    fi = fold_map.get(fold_corner, 1)
    fold_size = size_r * 0.35
    # 翘角的两个邻边中点
    p_corner = corners[fi]
    p_prev = corners[(fi - 1) % 4]
    p_next = corners[(fi + 1) % 4]
    # 从角向两个邻边走 fold_size
    def _pt_towards(a, b, dist):
        vx, vy = b[0] - a[0], b[1] - a[1]
        vl = math.hypot(vx, vy)
        if vl == 0: return a
        return (a[0] + vx / vl * dist, a[1] + vy / vl * dist)
    p1 = _pt_towards(p_corner, p_prev, fold_size)
    p2 = _pt_towards(p_corner, p_next, fold_size)
    # 用 BG 色覆盖出翘角"缺口"（模拟翘起看到下面的纸）
    draw.polygon([p_corner, p1, p2], fill=BG)
    # 翘角深色三角（阴影，让它有立体感）
    # 一个更小的三角在翘角内侧
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    inner_corner = ((p_corner[0] + mid[0]) / 2, (p_corner[1] + mid[1]) / 2)
    draw.polygon([p1, p2, inner_corner], fill=(200, 175, 30, 180))

    # 描边（手绘感）：不封死翘角那两条边
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    for i, (a, b) in enumerate(edges):
        # 如果这条边是被翘角吃掉的，只画到 p1/p2
        if i == fi - 1 % 4 or i == (fi - 1 + 4) % 4:  # 到 fold_corner 的入边
            draw.line([a, p1], fill=INK, width=stroke)
        elif i == fi:  # 从 fold_corner 出边
            draw.line([p2, b], fill=INK, width=stroke)
        else:
            draw.line([a, b], fill=INK, width=stroke)
    # 翘角的三角描边
    draw.line([p1, p2], fill=INK, width=stroke)
    draw.line([p_corner, p1], fill=INK, width=max(1, stroke // 2))
    draw.line([p_corner, p2], fill=INK, width=max(1, stroke // 2))

    # 便签上的手写"任务清单"（2 条横线 + 一个复选框 ☑）
    if with_lines:
        line_w = max(1, int(stroke * 0.75))
        # 复选框（第 1 行）：小方框 + 对勾
        # 复选框位置：局部坐标 x=-0.65, y=-0.32；边长 0.28*size_r
        cbox_x, cbox_y = -0.70, -0.32
        cbox_size = 0.30
        def _to_global(lx, ly):
            return (cx + lx * size_r * cos_a - ly * size_r * sin_a,
                    cy + lx * size_r * sin_a + ly * size_r * cos_a)
        # 复选框四角
        cb_tl = _to_global(cbox_x, cbox_y - cbox_size * 0.4)
        cb_tr = _to_global(cbox_x + cbox_size, cbox_y - cbox_size * 0.4)
        cb_br = _to_global(cbox_x + cbox_size, cbox_y + cbox_size * 0.4)
        cb_bl = _to_global(cbox_x, cbox_y + cbox_size * 0.4)
        # 手绘的小方框（4 条独立线）
        draw.line([cb_tl, cb_tr], fill=INK_SOFT, width=line_w)
        draw.line([cb_tr, cb_br], fill=INK_SOFT, width=line_w)
        draw.line([cb_br, cb_bl], fill=INK_SOFT, width=line_w)
        draw.line([cb_bl, cb_tl], fill=INK_SOFT, width=line_w)
        # ☑ 对勾（两段折线）
        check_pts_local = [
            (cbox_x + cbox_size * 0.15, cbox_y),          # 起点
            (cbox_x + cbox_size * 0.42, cbox_y + cbox_size * 0.3),   # 折点
            (cbox_x + cbox_size * 0.90, cbox_y - cbox_size * 0.4),   # 收笔（往上冒出框）
        ]
        check_pts = [_to_global(x, y) for x, y in check_pts_local]
        draw.line(check_pts[:2], fill=INK, width=max(1, int(line_w * 1.4)))
        draw.line(check_pts[1:], fill=INK, width=max(1, int(line_w * 1.4)))

        # 复选框右边一段横线（"任务名"）
        task_start = _to_global(cbox_x + cbox_size + 0.08, cbox_y)
        task_end   = _to_global(0.58, cbox_y - 0.02)  # 略微下扬
        draw.line([task_start, task_end], fill=INK_SOFT, width=line_w)

        # 第 2 行：一条较短的横线，长度不一（未完成的任务）
        line2_a = _to_global(-0.70,  0.10)
        line2_b = _to_global( 0.20,  0.08)
        draw.line([line2_a, line2_b], fill=INK_SOFT, width=line_w)

        # 第 3 行：一条更短的（模拟"还没写完"）
        line3_a = _to_global(-0.70,  0.42)
        line3_b = _to_global(-0.05,  0.40)
        draw.line([line3_a, line3_b], fill=INK_SOFT, width=line_w)


def _draw_paper_grain(img: Image.Image, strength: float = 0.4):
    """轻微纸纹（比 v1 弱 60%）"""
    size = img.width
    noise = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ndraw = ImageDraw.Draw(noise)
    random.seed(42)
    density = int(size * size * 0.015)  # 密度大幅降低
    for _ in range(density):
        x = random.randint(0, size - 1)
        y = random.randint(0, size - 1)
        alpha = int(random.randint(3, 8) * strength)
        ndraw.point((x, y), fill=(90, 65, 30, alpha))
    noise = noise.filter(ImageFilter.GaussianBlur(radius=0.3))
    img.alpha_composite(noise)


# ─────────────────────────── 主绘制 ───────────────────────────
def draw_icon(size: int) -> Image.Image:
    """size×size 图标"""
    scale = 4  # 抗锯齿 oversampling
    ss = size * scale
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角方（不规则手绘外框）
    corner_r = int(ss * 0.19)
    outer_stroke = max(3, int(ss * 0.028))

    # 底填色 —— 用非手绘的圆角方（干净的形状，只有描边手绘）
    draw.rounded_rectangle([(0, 0), (ss - 1, ss - 1)], radius=corner_r, fill=BG)

    # 手绘感描边（小尺寸下退化为单线，避免糊）
    if size >= 48:
        _hand_rounded_rect(draw,
                           (outer_stroke, outer_stroke, ss - outer_stroke - 1, ss - outer_stroke - 1),
                           radius=corner_r - outer_stroke // 2,
                           width=outer_stroke,
                           seed=1)
    else:
        # 小尺寸：干净描边
        draw.rounded_rectangle(
            [(outer_stroke // 2, outer_stroke // 2), (ss - outer_stroke // 2 - 1, ss - outer_stroke // 2 - 1)],
            radius=corner_r - outer_stroke // 2,
            outline=INK, width=outer_stroke,
        )

    # 中央 C（略偏左，给右侧便签留位）
    if size >= 32:
        c_cx = ss * 0.42
        c_cy = ss * 0.52
        c_r = ss * 0.27
    else:
        c_cx = ss * 0.50
        c_cy = ss * 0.50
        c_r = ss * 0.30
    c_stroke = int(ss * 0.14)
    _draw_c(draw, c_cx, c_cy, c_r, c_stroke)

    # 两张错位便利贴（暗示多 agent） —— 只在 32+ 显示
    if size >= 32:
        sticky_r = ss * 0.13
        sticky_stroke = max(2, int(ss * 0.014))
        # 后面那张：略深黄 + 更斜（露出一部分表示"下面还有更多"）
        _draw_sticky_note(img,
                          cx=ss * 0.72, cy=ss * 0.28,
                          size_r=sticky_r * 0.9,
                          angle_deg=-13,
                          color=ACCENT2,
                          stroke=sticky_stroke,
                          fold_corner="tr",
                          with_lines=False)
        # 前面那张：芥末黄 + 微斜，压在后面之上；大尺寸时上面有手写横线
        _draw_sticky_note(img,
                          cx=ss * 0.755, cy=ss * 0.335,
                          size_r=sticky_r,
                          angle_deg=7,
                          color=ACCENT,
                          stroke=sticky_stroke,
                          fold_corner="tr",
                          with_lines=(size >= 128))
    elif size >= 24:
        # 24/16：只画一个小黄块（保留色彩记忆点）
        draw.rectangle([(ss * 0.63, ss * 0.20), (ss * 0.85, ss * 0.42)],
                       fill=ACCENT, outline=INK, width=max(1, int(ss * 0.03)))

    # 纸纹（64+）
    if size >= 64:
        _draw_paper_grain(img, strength=0.4)

    return img.resize((size, size), Image.LANCZOS)


# ─────────────────────────── 输出 ───────────────────────────
def write_multi_ico(frames, out_path):
    png_bytes_list = []
    for img in frames:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes_list.append(buf.getvalue())

    n = len(frames)
    header = struct.pack("<HHH", 0, 1, n)
    entries = b""
    offset = 6 + 16 * n
    for img, data in zip(frames, png_bytes_list):
        w = img.width if img.width < 256 else 0
        h = img.height if img.height < 256 else 0
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    with open(out_path, "wb") as f:
        f.write(header)
        f.write(entries)
        for d in png_bytes_list:
            f.write(d)


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for s in sizes:
        print(f"drawing {s}x{s}...")
        frames.append(draw_icon(s))
    write_multi_ico(frames, OUT_ICO)
    frames[-1].save(OUT_PNG)
    print(f"\n✓ {OUT_ICO} ({OUT_ICO.stat().st_size} bytes)")
    print(f"✓ {OUT_PNG} ({OUT_PNG.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
