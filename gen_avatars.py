"""生成 12 个像素风头像 SVG（12x12 网格，viewBox 32x32）。

每格 2px；共 24px 图案 + 上下左右 4px padding = 32px 画布。
每人用同一个"标准脸型"：
  第 4-5 行：额头/发际线
  第 6 行：眼睛 (DSSSSD 或 DS..SD)
  第 7 行：鼻梁 (SSKKSS)
  第 8 行：脸中段
  第 9 行：嘴 (SKKKKS 一字 或 KSKKSK 嘴角)
  第 10 行：下巴
  第 11-12 行：肩/领
角色区别 = 头顶装饰 + 装饰色。
"""
from pathlib import Path

OUT = Path(__file__).parent / "static" / "avatars"
OUT.mkdir(parents=True, exist_ok=True)

BG = "#f6efe1"   # 牛皮纸底色
SK = "#e8bd9a"   # 皮肤
SK2 = "#c58f6a"  # 皮肤阴影（很少用，避免误读为胡子）
LI = "#3d2b1f"   # 主线
DK = "#1c1310"   # 极黑（眼）
WH = "#ffffff"

PALETTE = {"K": LI, "D": DK, "S": SK, "s": SK2, "W": WH}
AVATARS = {}


def add(name: str, colors: dict, grid: list[str]):
    # 校验：每行必须 12 字符
    fixed = [row.ljust(12, ".")[:12] for row in grid]
    while len(fixed) < 12:
        fixed.append("." * 12)
    AVATARS[name] = {"colors": colors, "grid": fixed[:12]}


# ═══════ Pine · 产品 · 绿眼镜 + 围巾 ═══════
add("Pine",
    {"1": "#3f6b4a", "2": "#7ba687"},
    [
        "............",
        "...KKKKKK...",  # 发顶
        "..K111111K..",
        ".K11111111K.",
        "..KSSSSSSK..",  # 额头
        "..KDSKKSDK..",  # 眼 + 眼镜梁
        "..KSSKKSSK..",  # 鼻梁
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 微笑
        "..KKSSSSKK..",  # 下巴
        "...K2222K...",  # 绿围巾
        "....2222....",
    ])

# ═══════ Ash · 开发 · 蓝耳机 ═══════
add("Ash",
    {"1": "#3b82f6", "2": "#1e40af"},
    [
        "............",
        "....1111....",  # 耳机横梁 (蓝)
        "...111111...",
        "..11KSSK11..",  # 耳罩包住两侧
        "..11SSSS11..",
        "..KDSSSSDK..",  # 眼
        "..KSSKKSSK..",  # 鼻梁
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 微笑
        "..KKSSSSKK..",
        "....KKKK....",
        "............",
    ])

# ═══════ Wren · 设计 · 紫贝雷帽 ═══════
add("Wren",
    {"1": "#a855f7", "2": "#7c3aed"},
    [
        "............",
        "...111111...",  # 贝雷帽顶
        "..11111112.",   # 帽 + 帽尾装饰
        "..K1111112.",
        "..KSSSSSSK..",  # 额头
        "..KDSSSSDK..",  # 眼
        "..KSSKKSSK..",  # 鼻梁
        "..KSSSSSSK..",
        "..KKSSSSKK..",  # 嘴（一字）
        "..KSKKKKSK..",  # 下巴阴影 → 用微笑替代
        "...KSSSSK...",
        "....KKKK....",
    ])

# ═══════ Owl · 测试 · 单片放大镜 ═══════
add("Owl",
    {"1": "#10b981", "2": "#065f46"},
    [
        "............",
        "....KKKK....",
        "...K1111K...",  # 短发
        "..K111111K..",
        "..KSSSSSSK..",  # 额头
        "..KDSSSKKK..",  # 左眼 + 右侧单片放大镜
        "..KSSSSK1K..",  # 放大镜柄
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 一横嘴
        "..KKSSSSKK..",
        "....KKKK....",
        "............",
    ])

# ═══════ Chief · 老板 · 皇冠 ═══════
add("Chief",
    {"1": "#f59e0b", "2": "#7c2d12", "3": "#dc2626"},
    [
        "............",
        ".1..1..1..1.",  # 皇冠尖
        ".11111111111",
        ".13113113113",  # 皇冠上的红宝石
        "..KSSSSSSK..",
        "..KDSSSSDK..",  # 眼
        "..KSSKKSSK..",  # 鼻梁
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 微笑
        "..KKSSSSKK..",
        "...K2222K...",  # 深棕西装领
        "..22....22..",
    ])

# ═══════ Rune · 数据 · 蓝色 VR 头显 ═══════
add("Rune",
    {"1": "#06b6d4", "2": "#0e7490"},
    [
        "............",
        "....KKKK....",
        "...KKKKKK...",
        "..KKKKKKKK..",
        "..1111111 1.".replace(" ", "1"),  # 头显（护目镜） 一整条
        ".1DKKKKKKD1.",  # 头显里透出眼
        "..1111111 1.".replace(" ", "1"),
        "..KSSSSSSK..",
        "..KSKKKKSK..",
        "..KKSSSSKK..",
        "....KKKK....",
        "............",
    ])

# ═══════ Poppy · 客服 · 橙耳麦 ═══════
add("Poppy",
    {"1": "#f97316", "2": "#c2410c"},
    [
        "............",
        "...K1111K...",  # 头顶 + 橙色发梢
        "..K111111K..",
        ".K1KKKKKK11.",  # 耳麦横梁
        "..KSSSSSSK1.",  # 麦克风臂
        "..KDSSSSDK1.",  # 眼
        "..KSSKKSSK..",
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 大微笑
        "..KKSSSSKK..",
        "....KKKK....",
        "............",
    ])

# ═══════ Judge · 法务 · 无框眼镜 ═══════
add("Judge",
    {"1": "#64748b", "2": "#334155"},
    [
        "............",
        "....KKKK....",
        "...K1111K...",  # 灰发
        "..K111111K..",
        "..KSSSSSSK..",
        "..KDS22SDK..",  # 眼 + 眼镜镜片（浅灰）
        "..KSSKKSSK..",  # 鼻梁
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 一横严肃
        "..KKSSSSKK..",
        "...K2222K...",  # 深西装
        "..22....22..",
    ])

# ═══════ Rally · 运营 · 红鸭舌帽 ═══════
add("Rally",
    {"1": "#ef4444", "2": "#991b1b"},
    [
        "............",
        "...111111...",  # 帽顶
        "..11111111..",
        ".1111111111.",  # 帽檐伸出
        "..K1KKKK1K..",  # 帽下
        "..KSSSSSSK..",
        "..KDSSSSDK..",  # 眼
        "..KSSKKSSK..",  # 鼻梁
        "..KSSKKSSK..",
        "..KSKKKKSK..",  # 咧嘴笑
        "..KKSSSSKK..",
        "....KKKK....",
    ])

# ═══════ Ivy · HR · 绿发夹 + 微笑 ═══════
add("Ivy",
    {"1": "#14b8a6", "2": "#0f766e"},
    [
        "............",
        "....KKKK1...",  # 短发 + 右上发夹
        "...K11KKK...",
        "..K111111K..",
        "..KSSSSSSK..",
        "..KDSSSSDK..",  # 眼
        "..KSSKKSSK..",
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 微笑
        "..KKSSSSKK..",
        "....KKKK....",
        "............",
    ])

# ═══════ Ledger · 财务 · 绿眼罩 ═══════
add("Ledger",
    {"1": "#eab308", "2": "#854d0e", "3": "#166534"},
    [
        "............",
        "....KKKK....",
        "...K2222K...",  # 棕发
        "..K222222K..",
        "..K333333K..",  # 绿眼罩上沿
        ".K3DSSSSD3K.",  # 眼罩中：眼睛露出
        "..K333333K..",  # 眼罩下沿
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 一横嘴
        "..KKSSSSKK..",
        "...K1111K...",  # 金色领结
        "....KKKK....",
    ])

# ═══════ Foreman · 总管 · 礼帽 ═══════
add("Foreman",
    {"1": "#3f2e1a", "2": "#78350f", "3": "#facc15"},
    [
        "............",
        "..11111111..",  # 礼帽顶
        "..11111111..",
        ".1111311111.",  # 礼帽 + 金色帽带
        ".1111111111.",  # 礼帽帽檐
        "..KSSSSSSK..",
        "..KDSSSSDK..",  # 眼
        "..KSSKKSSK..",  # 鼻梁
        "..KSSSSSSK..",
        "..KSKKKKSK..",  # 微笑
        "..2KKKKKK2..",  # 领结 + 西装领
        "..22....22..",
    ])


def render(name: str, data: dict) -> str:
    grid = data["grid"]
    colors = data["colors"]
    px = 2  # 每像素 2px（12格 × 2 = 24px）
    pad = (32 - 24) // 2  # 4px 边距
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" '
        'shape-rendering="crispEdges" width="100%" height="100%">',
        f'<rect width="32" height="32" fill="{BG}"/>',
    ]
    for r, row in enumerate(grid):
        for c, ch in enumerate(row):
            if ch in (".", " "):
                continue
            color = PALETTE.get(ch) or colors.get(ch)
            if not color:
                continue
            x = pad + c * px
            y = pad + r * px
            parts.append(
                f'<rect x="{x}" y="{y}" width="{px}" height="{px}" fill="{color}"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def main():
    for name, data in AVATARS.items():
        svg = render(name, data)
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        print(f"  ✓ {name}.svg")
    print(f"\n共 {len(AVATARS)} 个头像 → {OUT}")


if __name__ == "__main__":
    main()
