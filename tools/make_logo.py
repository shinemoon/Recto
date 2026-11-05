#!/usr/bin/env python3
"""把仓库根目录的 `logo.png` 加工成界面用的几个派生图。

原图是「黑字白底」(2048×2048, 纯灰度), 直接放进顶栏会有两个问题:
  * 白色底块在深色皮肤上是个突兀的白方块 → 去掉白底, 转成透明 + 纯黑前景
  * 原图四周留白极大 (内容只占 1085×693) → 先按内容裁边, 否则缩到 22px 高时图形只剩几个像素

产出 (全部写进 `src/recto/app/ui/assets/`):
  logo-mark.png        图徽 (不含文字) —— 顶栏, 高 66px (顶栏显示 22px, 正好 3x: 手机上不发虚)
  logo-full.png        完整标志 (图徽 + 文字) —— 登录卡片, 高 192px (显示 64px, 3x)
  favicon.png          64×64 方图 (完整标志居中)
  apple-touch-icon.png 180×180 方图

用法:
    python3 tools/make_logo.py                # 默认读 <仓库根>/logo.png
    python3 tools/make_logo.py --src other.png
    python3 tools/make_logo.py --check        # 只检查派生图是否比源图旧 (CI 用)

换了 logo.png 之后重跑一次即可, 派生图跟着入库 (克隆仓库的人不需要 logo.png 也能跑)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "recto" / "app" / "ui" / "assets"

# 派生图: 文件名 -> (裁切方式, 目标高度; 方图走边长)
# favicon 只用图徽 —— 16~32px 里文字会糊成一团, 留图形反而更认得出。
TARGETS = {
    "logo-mark.png": ("mark", 66),
    "logo-full.png": ("full", 192),
    "favicon.png": ("square-mark", 64),
    "apple-touch-icon.png": ("square", 180),
}


def _alpha_layer(src: Path):
    """白底转透明: alpha = 255 - 亮度, 前景统一纯黑 (抗锯齿信息全在 alpha 里)。"""
    from PIL import Image, ImageOps

    im = Image.open(src).convert("L")
    alpha = ImageOps.invert(im)
    # 极淡的纸纹/压缩噪点不该变成可见的灰雾
    alpha = alpha.point(lambda v: 0 if v < 6 else (255 if v > 250 else v))
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.putalpha(alpha)
    black = Image.new("RGBA", im.size, (0, 0, 0, 255))
    black.putalpha(alpha)
    return black


def _ink_bands(im) -> list[tuple[int, int]]:
    """按行扫描出不透明的横向带 —— 原图里「图徽」和「文字」之间是纯空行。"""
    a = im.getchannel("A")
    w, h = a.size
    px = a.load()
    bands: list[tuple[int, int]] = []
    start = None
    for y in range(h):
        row_has = any(px[x, y] > 8 for x in range(0, w, 2))
        if row_has and start is None:
            start = y
        elif not row_has and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, h))
    return bands


def _crop_mark(im):
    bands = _ink_bands(im)
    if not bands:
        raise SystemExit("logo.png 里找不到任何内容")
    y0, y1 = bands[0]                      # 最上一条 = 图徽
    top = im.crop((0, y0, im.width, y1))
    return top.crop(top.getbbox())


def _crop_full(im):
    bbox = im.getbbox()
    if not bbox:
        raise SystemExit("logo.png 里找不到任何内容")
    return im.crop(bbox)


def _square(im, side: int, pad_ratio: float = 0.08):
    """等比装进正方形画布 (留 pad_ratio 的边), 居中。用于 favicon。"""
    from PIL import Image

    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    inner = int(round(side * (1 - 2 * pad_ratio)))
    scale = min(inner / im.width, inner / im.height)
    w = max(1, int(round(im.width * scale)))
    h = max(1, int(round(im.height * scale)))
    small = im.resize((w, h), Image.LANCZOS)
    canvas.paste(small, ((side - w) // 2, (side - h) // 2), small)
    return canvas


def _resize_h(im, height: int):
    scale = height / im.height
    w = max(1, int(round(im.width * scale)))
    from PIL import Image

    return im.resize((w, height), Image.LANCZOS)


def build(src: Path, *, dry: bool = False) -> list[tuple[str, tuple[int, int], int]]:
    full = _alpha_layer(src)
    mark = _crop_mark(full)
    full = _crop_full(full)
    made = []
    for name, (kind, size) in TARGETS.items():
        if kind == "mark":
            img = _resize_h(mark, size)
        elif kind == "full":
            img = _resize_h(full, size)
        elif kind == "square-mark":
            img = _square(mark, size, pad_ratio=0.05)
        else:
            img = _square(full, size)
        out = ASSETS / name
        if dry:
            made.append((name, img.size, 0))
            continue
        ASSETS.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG", optimize=True)
        made.append((name, img.size, out.stat().st_size))
    return made


def _stale(src: Path) -> list[str]:
    bad = []
    for name in TARGETS:
        p = ASSETS / name
        if not p.exists():
            bad.append(name + " (缺失)")
        elif p.stat().st_mtime < src.stat().st_mtime:
            bad.append(name + " (比源图旧)")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成界面用的 logo 派生图")
    ap.add_argument("--src", default=str(ROOT / "logo.png"))
    ap.add_argument("--check", action="store_true", help="只检查派生图是否需要重建")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.src)
    if not src.exists():
        print(f"找不到源图: {src}", file=sys.stderr)
        return 2
    if args.check:
        bad = _stale(src)
        if bad:
            print("需要重建: " + ", ".join(bad), file=sys.stderr)
            return 1
        print("派生图已是最新")
        return 0
    for name, size, nbytes in build(src, dry=args.dry):
        print(f"{'[dry] ' if args.dry else ''}{name}: {size[0]}x{size[1]}"
              + (f" · {nbytes / 1024:.1f} KB" if nbytes else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
