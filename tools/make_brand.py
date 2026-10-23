# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""生成 repo 图标与横幅（纯几何，无字体依赖、可复跑）。

为什么不用 AI 出图：这两个是**仓库资产**，要求"任何人重跑得到同一张"，
且不能带任何许可不明的素材。纯几何绘制最稳。

产物：
    icon.png    400×400（Comfy Registry 要求 ≤400×400 方图）
    banner.png  1050×450（21:9，registry 节点页横幅）

跑法：python tools/make_brand.py
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BG = (20, 22, 26, 255)
LINE = (245, 247, 250, 255)
ACCENT = (78, 161, 255, 255)
DIM = (58, 63, 72, 255)


def grid_mask(draw, x, y, w, h, cols, rows, gutter, color, accent_cell=None):
    """画 N×M 宫格：外框 + 内缝，风格与节点产出的故事板一致。"""
    pw = (w - 2 * gutter - (cols - 1) * gutter) / cols
    ph = (h - 2 * gutter - (rows - 1) * gutter) / rows
    for r in range(rows):
        for c in range(cols):
            x0 = x + gutter + c * (pw + gutter)
            y0 = y + gutter + r * (ph + gutter)
            fill = ACCENT if (accent_cell == (r, c)) else None
            if fill:
                draw.rectangle([x0, y0, x0 + pw, y0 + ph], fill=fill)
            else:
                draw.rectangle([x0, y0, x0 + pw, y0 + ph], outline=color, width=max(3, int(pw * 0.04)))


def make_icon():
    s = 400
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=72, fill=BG)
    # 2×2 宫格 + 右下角高亮：读作"一张图切成 4 格"
    grid_mask(d, 56, 56, s - 112, s - 112, 2, 2, 16, LINE, accent_cell=(1, 1))
    im.save(os.path.join(ROOT, "icon.png"))
    print("icon.png 400×400")


def make_banner():
    w, h = 1050, 450
    im = Image.new("RGB", (w, h), BG[:3])
    d = ImageDraw.Draw(im)
    # 左侧 4×2 宫格（故事板的典型排法），第三格高亮
    grid_mask(d, 70, 90, 620, 270, 4, 2, 14, LINE, accent_cell=(0, 2))
    # 右侧三条装饰线，暗示"采样 / 序列"
    for i, col in enumerate((LINE, ACCENT, DIM)):
        d.rounded_rectangle([760, 150 + i * 60, 760 + (200 - i * 50), 168 + i * 60],
                            radius=9, fill=col)
    im.save(os.path.join(ROOT, "banner.png"))
    print("banner.png 1050×450（21:9）")


if __name__ == "__main__":
    make_icon()
    make_banner()
