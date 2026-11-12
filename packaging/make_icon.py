"""生成程序图标 packaging/icon.ico：深色方块上一个白色的"听"字。改了样式就重新跑一次，把生成的 ico 一起提交。

    python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("icon.ico")
FONT = r"C:\Windows\Fonts\msyhbd.ttc"  # 微软雅黑粗体


def draw(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = max(2, size // 6)
    d.rounded_rectangle((0, 0, size - 1, size - 1), r, fill=(31, 35, 41, 255))
    font = ImageFont.truetype(FONT, int(size * 0.66))
    box = d.textbbox((0, 0), "听", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), "听", font=font, fill=(255, 255, 255, 255))
    # 底部一条浅色横线，示意字幕
    y = int(size * 0.86)
    d.rectangle((int(size * 0.22), y, int(size * 0.78), y + max(1, size // 32)), fill=(120, 200, 255, 255))
    return img


sizes = [16, 24, 32, 48, 64, 128, 256]
draw(256).save(OUT, sizes=[(s, s) for s in sizes], append_images=[draw(s) for s in sizes[:-1]])
print(OUT)
