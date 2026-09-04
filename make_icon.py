#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「书到了」印章风 .ico 图标（纯标准库，无需 Pillow）。

绘制一枚朱砂红方印，印面白文「书到」二字，底部一条细横线。
多尺寸（16/24/32/48/64/128/256），用 ICO 容器内嵌 BMP（BGRA）格式，
PyInstaller 的 icon= 可直接使用。

注：图标内「书到」二字由像素模板近似勾勒，小尺寸下靠印章轮廓+红白对比
保证辨识度；大尺寸下字形完整可读。
"""
import struct
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")

# 调色板（印章风）
SEAL = (176, 42, 32)       # 朱砂红印面
SEAL_EDGE = (140, 30, 24)  # 印面边缘/暗部
INK = (245, 232, 208)      # 白文（米白，近似纸色）


def _in_rounded_rect(x, y, size, radius):
    """判断 (x,y) 是否在圆角矩形内（归一化 0..1，坐标像素）"""
    s = size
    r = radius * s
    if r <= x <= s - r or r <= y <= s - r:
        if (r <= x <= s - r) and (0 <= y <= s):
            return True
        if (r <= y <= s - r) and (0 <= x <= s):
            return True
    for cx, cy in ((r, r), (s - r, r), (r, s - r), (s - r, s - r)):
        if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
            return True
    return False


def _stroke_of_char(ch, X, Y):
    """返回「书」或「到」在归一化坐标 (X,Y) 处是否属于笔画。

    每个字画在 0..1 的局部方格内，笔画用粗竖/横/斜条带近似。
    返回 True 表示该点属于白文笔画（即应显示为米白色）。
    """
    if ch == "书":
        # 「书」：三横一竖（竖钩）
        # 三横：y ≈ 0.28, 0.50, 0.72；竖：x ≈ 0.5 贯通
        y_bands = (0.26, 0.34), (0.48, 0.56), (0.70, 0.78)
        for lo, hi in y_bands:
            if lo <= Y <= hi and 0.12 <= X <= 0.88:
                return True
        if 0.40 <= X <= 0.60 and 0.20 <= Y <= 0.80:
            return True
        return False
    else:  # "到"
        # 「到」：左「至」+ 右「刂」
        # 左部「至」：上横、中横、下横 + 一竖
        y_bands = (0.22, 0.30), (0.45, 0.53), (0.70, 0.78)
        for lo, hi in y_bands:
            if lo <= Y <= hi and 0.08 <= X <= 0.42:
                return True
        if 0.20 <= X <= 0.30 and 0.18 <= Y <= 0.82:
            return True
        # 右部「刂」：竖 + 竖钩
        if 0.72 <= X <= 0.84 and 0.18 <= Y <= 0.82:
            return True
        if 0.56 <= X <= 0.66 and 0.18 <= Y <= 0.82:
            return True
        return False


def _px(x, y, size):
    """返回某像素的颜色（BGRA）。坐标系 y 向下。"""
    s = float(size)
    X, Y = x / s, y / s

    # 印面：圆角矩形（略内缩，留透明边）
    if not _in_rounded_rect(x, y, s, 0.16):
        return (0, 0, 0, 0)

    # 印章底色：朱砂红，边缘略深
    edge = _in_rounded_rect(x, y, s, 0.16) and not _in_rounded_rect(x, y, s, 0.20)
    if edge:
        return (*SEAL_EDGE, 255)
    bg = SEAL

    # 内边框（白文细框）
    if _in_rounded_rect(x, y, s, 0.28) and not _in_rounded_rect(x, y, s, 0.33):
        return (*INK, 255)

    # 两字排布：左「书」右「到」，各占约 0.34 宽，居中于 0.34~0.72 高
    if 0.34 <= Y <= 0.72:
        # 左字「书」：X 0.13~0.47
        if 0.13 <= X <= 0.47:
            lx = (X - 0.13) / 0.34
            ly = (Y - 0.34) / 0.38
            if _stroke_of_char("书", lx, ly):
                return (*INK, 255)
        # 右字「到」：X 0.53~0.87
        if 0.53 <= X <= 0.87:
            lx = (X - 0.53) / 0.34
            ly = (Y - 0.34) / 0.38
            if _stroke_of_char("到", lx, ly):
                return (*INK, 255)

    return (*bg, 255)


def _make_bmp(size):
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            b, g, r, a = _px(x, y, size)
            row += bytes((b, g, r, a))
        while len(row) % 4:
            row += b"\x00"
        rows.append(bytes(row))
    pixel_data = b"".join(rows)

    header_size = 40
    data_size = len(pixel_data)
    file_size = 14 + header_size + data_size
    bmp = b""
    bmp += b"BM"
    bmp += struct.pack("<I", file_size)
    bmp += struct.pack("<H", 0)
    bmp += struct.pack("<H", 0)
    bmp += struct.pack("<I", 14 + header_size)
    bmp += struct.pack("<I", header_size)
    bmp += struct.pack("<i", size)
    bmp += struct.pack("<i", size * 2)  # ICO 用 height*2 表示 AND mask
    bmp += struct.pack("<H", 1)
    bmp += struct.pack("<H", 32)
    bmp += struct.pack("<I", 0)
    bmp += struct.pack("<I", data_size)
    bmp += struct.pack("<i", 0)
    bmp += struct.pack("<i", 0)
    bmp += struct.pack("<I", 0)
    bmp += struct.pack("<I", 0)
    bmp += pixel_data
    return bmp


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [(s, _make_bmp(s)) for s in sizes]

    ico = b""
    ico += struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for s, bmp in images:
        ico += struct.pack("<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0,
                           0, 0, 1, 32, len(bmp), offset)
        offset += len(bmp)
    for s, bmp in images:
        ico += bmp

    with open(OUT, "wb") as f:
        f.write(ico)
    print(f"图标已生成: {OUT} ({os.path.getsize(OUT)} bytes, {len(sizes)} 尺寸)")


if __name__ == "__main__":
    main()
