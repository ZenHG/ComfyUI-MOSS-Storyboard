#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""跨格一致性度量：把 `docs/07-consistency.md` 的四组指标变成一条命令能跑出来的东西。

为什么要有它：那张表的判据原先由一个**不在本仓**的脚本产出，用户照着文档"怎么复现"
那节根本跑不出来 ⇒ 本包立论（逐格 > 整张）不可验证。口径以本文件为准。

跑法：
    python tools/check_grid_consistency.py 单格1.png 单格2.png ... [--hex #d92b2b] [--report j.json]
人脸两项需要 `mediapipe`（可选依赖）；检不到 2 张脸时该两项报 n/a，不猜。

口径（与文档 §五 同命名）：
  tone            每格算 (luma, R−B, saturation)，各取**格间极差**，再对三项取均值
  id_consistency  人脸 478 点网格坐标拼成向量，两两余弦的均值 ×100（越大越像同一张脸的**形状**）
  shot_gradient   人脸框面积占画幅比例的格间极差（景别有没有真的拉开）
                  ↑ 这两项**只在每格恰好一张脸时出数**，否则 n/a（原因见 faces()）
  content         所有格对的平均 MAE（0–1，太小 = 画面重复而不是一致）
  prop            可选：`--hex` 色相邻域像素占该格面积的比例，报均值与**格间方差**
                  ⚠ 景别不同时同一道具的占比天然不同 ⇒ 这项混了"构图差异"，别单独当身份判据
  arcface         可选：`--arcface` 用 insightface `buffalo_l`（w600k_r50）出**身份**相似度：
                  每个格对取「两格各挑一张脸的最大余弦」，再报均值与最低值。
                  与 `id_consistency`（478 点**形状**代理）不同，这一项量的是"是不是同一个人"；
                  双人格不必指定挑谁，代价是"任一格有脸即可"，所以**必须先看每格脸数**。
                  没装 insightface / 没下权重 → 报 None，不猜。
"""

import argparse
import colorsys
import json
import os
import sys

import numpy as np
from PIL import Image

MAX_EDGE = 512          # 统一缩到这边再比，避免不同尺寸算不出差值


def load(path):
    im = Image.open(path).convert("RGB")
    if max(im.size) > MAX_EDGE:
        im = im.resize((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    return np.asarray(im).astype(np.float32) / 255.0


def panel_stats(a):
    """单格三项色调量：luma、R−B（有符号）、饱和度。"""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = a.max(axis=2) - a.min(axis=2)
    return float(luma.mean()), float((r - b).mean()), float(sat.mean())


def tone_spread(stats):
    """每维取格间极差再平均：只报一个数，避免三件事各说一套。"""
    arr = np.asarray(stats)                      # (n格, 3)
    return float(np.mean(arr.max(axis=0) - arr.min(axis=0)))


def content_mae(panels):
    n = len(panels)
    if n < 2:
        return None
    vals = [float(np.abs(a - b).mean()) for i, a in enumerate(panels) for b in panels[i + 1:]]
    return float(np.mean(vals))


def faces(panels):
    """返回 (余弦均值×100 或 None, 景别极差 或 None, 每格检出脸数)。

    🔴 只有**每格恰好一张脸**才出数：故事板常是双人/多人镜头，而"抓哪张脸"不确定
    ⇒ 余弦量到的会变成"检到了谁"而不是"是不是同一个人"（实测：逐格因景别塌陷
    普遍每格 2 张脸，同一批图的余弦从 95 掉到 91 就是这么来的）。不满足就报 n/a。
    """
    try:
        import mediapipe as mp
    except ImportError:
        return None, None, []
    lms, areas, counts = [], [], []
    with mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=8,
                                         refine_landmarks=False) as fm:
        for a in panels:
            res = fm.process(np.ascontiguousarray((a * 255).astype(np.uint8)))
            got = res.multi_face_landmarks or []
            counts.append(len(got))
            if len(got) != 1:
                lms.append(None)
                areas.append(None)
                continue
            pts = np.array([(lm.x, lm.y) for lm in got[0].landmark])
            lms.append(pts.ravel())
            areas.append(float(np.ptp(pts[:, 0]) * np.ptp(pts[:, 1])))
    if any(c != 1 for c in counts):
        return None, None, counts
    vecs = lms
    cos = [float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
           for i, a in enumerate(vecs) for b in vecs[i + 1:]]
    return round(100 * float(np.mean(cos)), 1), round(float(max(areas) - min(areas)), 4), counts


_AF_APP = None


def load_bgr(path, long_edge=1024):
    """按**原比例**读图（ArcFace 用）：`load()` 会把图强制拉成 512×512 方形，
    16:9 的格被压扁后检测会全灭，所以这条另走一路。"""
    im = Image.open(path).convert("RGB")
    if max(im.size) > long_edge:
        k = long_edge / float(max(im.size))
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    return np.asarray(im)[:, :, ::-1].copy()          # RGB → BGR


def arcface_app():
    """懒加载 insightface（CPU，不占 GPU）。没装包/没权重 → None，调用方报 n/a。

    权重 = `~/.insightface/models/buffalo_l/`（det_10g.onnx + w600k_r50.onnx）。
    """
    global _AF_APP
    if _AF_APP is None:
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            return None
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640))
        _AF_APP = app
    return _AF_APP


def face_embeds(bgr):
    """一格里**所有**脸的单位化 embedding（按脸面积从大到小）。"""
    app = arcface_app()
    if app is None:
        return []
    out = []
    for f in app.get(bgr) or []:
        x0, y0, x1, y1 = f["bbox"]
        v = np.asarray(f["embedding"], dtype="float32")
        out.append(((x1 - x0) * (y1 - y0), v / (np.linalg.norm(v) + 1e-9)))
    return [v for _, v in sorted(out, key=lambda t: t[0], reverse=True)]


def best_match(va, vb):
    """两组脸之间的**最大**余弦 —— 问的是「这两格里有没有同一个人」，不必指定挑谁。"""
    if not va or not vb:
        return None
    return max(float(np.dot(x, y)) for x in va for y in vb)


def grid_identity(bgrs):
    """一张宫格的格间身份相似度：返回 `(均值, 最小值, 每格脸数)`；任一格检不到脸 → 前两项 None。"""
    embs = [face_embeds(a) for a in bgrs]
    counts = [len(e) for e in embs]
    vals = [m for m in (best_match(embs[i], embs[j])
                        for i in range(len(embs)) for j in range(i + 1, len(embs))) if m is not None]
    if not vals or min(counts) == 0:
        return None, None, counts
    return round(float(np.mean(vals)), 4), round(float(min(vals)), 4), counts


def prop_ratio(panels, hexs, tol=18.0):
    """道具色相邻域占比：只认**色相**距离，灰白黑不算"有这个颜色"。

    方差按格算——故事板要的是"每格都在"，一格有一团红、另一格没有，
    均值再高也是串色；所以文档用方差而不是均值下结论。
    """
    tr, tg, tb = (int(hexs[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    target = colorsys.rgb_to_hls(tr, tg, tb)[0] * 360.0
    out = []
    for a in panels:
        m = a.max(axis=2)
        d = m - a.min(axis=2)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        hue = np.zeros_like(m)
        for cond, val in (((m == r) & (d > 0), (g - b) / np.where(d > 0, d, 1)),
                          ((m == g) & (d > 0) & (m != r), 2 + (b - r) / np.where(d > 0, d, 1)),
                          ((m == b) & (d > 0) & (m != r) & (m != g), 4 + (r - g) / np.where(d > 0, d, 1))):
            hue[cond] = val[cond]
        hue = (hue * 60.0) % 360.0
        dist = np.abs((hue - target + 180.0) % 360.0 - 180.0)
        mask = (dist <= tol) & (d > 0.12) & (m > 0.12)
        out.append(round(float(mask.mean()), 5))
    return {"per_panel": out, "mean": round(float(np.mean(out)), 4),
            "variance": round(float(np.var(out)), 6), "hex": hexs, "tol": tol}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--hex", default="", help="道具色，如 #d92b2b；给了才报 prop 一项")
    ap.add_argument("--arcface", action="store_true",
                    help="加报 ArcFace 格间身份相似度（insightface buffalo_l，CPU）")
    ap.add_argument("--report", default="")
    args = ap.parse_args(argv)

    panels = [load(f) for f in args.files]
    stats = [panel_stats(a) for a in panels]
    idc, grad, nf = faces(panels)
    cm = content_mae(panels)
    res = {"n_panels": len(panels),
           "tone": round(tone_spread(stats), 4),
           "per_panel_tone": [[round(v, 4) for v in s] for s in stats],
           "id_consistency": idc, "shot_gradient": grad, "faces_per_panel": nf,
           "content": None if cm is None else round(cm, 4)}
    if args.arcface:
        avg, low, af_counts = grid_identity([load_bgr(f) for f in args.files])
        res["arcface"] = {"mean": avg, "min": low, "faces_per_panel": af_counts}
    print("格数      %d" % res["n_panels"])
    print("tone ↓    %.4f   （luma/R−B/sat 三项格间极差的均值）" % res["tone"])
    for f, s in zip(args.files, stats, strict=True):
        print("   %-44s luma %.3f  R−B %+.3f  sat %.3f" % (os.path.basename(f), *s))
    why = "" if not nf or all(c == 1 for c in nf) else "（每格脸数 %s ≠ 全 1：双人镜头/无脸/兽脸 ⇒ 不可用）" % nf
    print("id_cons ↑ %s   景别极差 %s%s" % (res["id_consistency"], res["shot_gradient"], why))
    if args.arcface:
        a = res["arcface"]
        print("arcface   均值 %s  最低 %s  每格脸数 %s   （格对取「两格各挑一张脸的最大余弦」）"
              % (a["mean"], a["min"], a["faces_per_panel"]))
    if args.hex:
        p = prop_ratio(panels, args.hex)
        res["prop"] = p
        print("prop      占比均值 %.4f  格间方差 %.6f  各格 %s"
              % (p["mean"], p["variance"], p["per_panel"]))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print("报告 → %s" % args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
