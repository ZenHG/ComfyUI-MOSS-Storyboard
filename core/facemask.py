# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
"""自动脸框蒙版：insightface 检测 → 按位置/面积挑一张脸 → 生成羽化蒙版。

**为什么必须自动。** 手画的椭圆只能对一张图有效：换图就错位，宫格里还会偏到另一格的人脸上。
而蒙版错位**不会报错**，只会表现为「换了另一个人」——这是最难自查的一类失败。

**为什么用 insightface 而不是 SAM / BiSeNet。**
insightface 的 `buffalo_l` 在本机已有权重（离线可用），且 E26 就是用它的关键点算蒙版、
与旧实现逐像素等价。SAM 更准但要权重 + 提示点，BiSeNet 语义最好但权重可能要联网。
本包「零 pip 依赖」的承诺不变：**它是可选依赖**，`import` 失败就明确报错，不静默降级。

蒙版几何（E26 实测有效的那套）：
- 上沿 = 两眼中点 → 鼻尖之间 30%（`eye_y + 0.30 × (nose_y - eye_y)`）
- 下沿 = `y2 + 0.06 × h`
- 左右 = 脸框各外扩 `grow × w`（默认 5%）
- 羽化 = `max(3, 0.02 × w)`
"""

from __future__ import annotations

import os

#: 挑脸的方式。`largest` 最稳；`left` / `right` 用来点名「换右边那个男的」。
PICK_MODES = ["largest", "left", "right"]

#: 检测模块只装 detection，不装 recognition —— 我们要的是框和关键点，不是身份向量。
#: ⇒ 权重只需要 `det_10g.onnx`；`buffalo_l` 里的 `w600k_r50.onnx`（识别）本节点用不到。
_MODULES = ["detection"]

#: 权重文件名（insightface 的 `buffalo_l` 包）。
_WEIGHT = "det_10g.onnx"

#: 权重下载地址，只在报错信息里给出来，**不会**自动联网下载。
WEIGHT_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

#: 已初始化的检测器。`FaceAnalysis(...)` + `prepare()` 要几百毫秒，每格都重建会拖慢整张宫格。
_APPS = {}


def model_root():
    """权重根目录。默认 `~/.insightface`，可用环境变量 `MOSS_INSIGHTFACE_ROOT` 改。

    改这个是为了**离线部署**：把 `buffalo_l` 放到任意盘，设一个环境变量就行，
    不用动用户主目录。
    """
    return os.environ.get("MOSS_INSIGHTFACE_ROOT") or os.path.expanduser("~/.insightface")


def weight_path(root=None):
    """`det_10g.onnx` 的完整路径。"""
    return os.path.join(root or model_root(), "models", "buffalo_l", _WEIGHT)


def available():
    """insightface 能不能用（不加载模型，只查包）。"""
    try:
        import insightface  # noqa: F401
    except Exception:
        return False, "没有安装 insightface"
    return True, ""


def _get_app(det_size=640):
    """取（并缓存）检测器。权重缺失时**不联网下载**，直接报错说清放哪里。"""
    ok, why = available()
    if not ok:
        raise RuntimeError(
            "%s：自动蒙版需要 insightface（`pip install insightface onnxruntime`），"
            "或者手接一个 mask。" % why)

    root = model_root()
    path = weight_path(root)
    if not os.path.exists(path):
        raise RuntimeError(
            "自动蒙版缺少权重：%s\n"
            "把 `%s` 放到 `%s` 即可（同目录放不放 `w600k_r50.onnx` 都行，本节点只用检测）。\n"
            "下载：%s\n"
            "想放到别的盘：设环境变量 `MOSS_INSIGHTFACE_ROOT=<你的根目录>`，"
            "权重放在 `<根目录>/models/buffalo_l/`。\n"
            "或者关掉 `auto_mask` 并手接一个 `mask`。"
            % (path, _WEIGHT, os.path.dirname(path), WEIGHT_URL))

    key = (root, int(det_size))
    app = _APPS.get(key)
    if app is None:
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(name="buffalo_l", root=root,
                           providers=["CPUExecutionProvider"], allowed_modules=_MODULES)
        app.prepare(ctx_id=-1, det_size=(int(det_size), int(det_size)))
        _APPS[key] = app
    return app


def detect(rgb, det_size=640):
    """在 `rgb`（uint8，H×W×3）上检脸。返回按 **从左到右** 排好的列表。

    每项：`{"bbox": (x1,y1,x2,y2), "kps": [(x,y)×5], "area": w*h}`。
    """
    import numpy as np

    app = _get_app(det_size)
    faces = app.get(np.ascontiguousarray(rgb[:, :, ::-1]))   # insightface 吃 BGR

    out = []
    for f in faces:
        x1, y1, x2, y2 = [float(v) for v in f["bbox"]]
        # ⚠ `f.get("kps") or []` 会炸：kps 是 numpy 数组，`or` 要对它取真值。
        kps = f.get("kps")
        if kps is None:
            kps = []
        out.append({
            "bbox": (x1, y1, x2, y2),
            "kps": [(float(p[0]), float(p[1])) for p in kps],
            "area": (x2 - x1) * (y2 - y1),
            "cx": (x1 + x2) / 2.0,
        })
    # 按中心 x 排序：这样 `left` / `right` 与肉眼一致，不受检测顺序影响。
    out.sort(key=lambda f: f["cx"])
    return out


def pick(faces, mode="largest", index=0):
    """挑一张脸。返回 `(face, 说明)`；没有脸就报错。

    `left` / `right` 取**最左 / 最右**那张 —— 要「换右边那个男的」时，
    这是唯一不靠猜的表达方式。
    """
    if not faces:
        raise ValueError("这张图里检测不到人脸：换一张脸更清楚的图，或手接 mask。")
    mode = mode if mode in PICK_MODES else "largest"
    if mode == "left":
        return faces[0], "最左那张（共 %d 张）" % len(faces)
    if mode == "right":
        return faces[-1], "最右那张（共 %d 张）" % len(faces)
    best = max(faces, key=lambda f: f["area"])
    return best, "最大那张（共 %d 张，占 %.1f%%）" % (
        len(faces), 100.0 * best["area"] / max(1e-6, sum(f["area"] for f in faces)))


def build_mask(size_hw, face, grow=0.05, blur=None):
    """按一张脸生成蒙版。返回 PIL `L` 图（255 = 重画）。

    `size_hw` 是**这张图**的 `(h, w)`，不是原图 —— 裁格之后要按格的尺寸算。
    """
    from PIL import Image, ImageDraw, ImageFilter

    h, w = int(size_hw[0]), int(size_hw[1])
    x1, y1, x2, y2 = face["bbox"]
    fw, fh = x2 - x1, y2 - y1
    kps = face.get("kps") or []
    if len(kps) >= 5:
        eye_y = (kps[0][1] + kps[1][1]) / 2.0
        nose_y = kps[2][1]
        top = eye_y + 0.30 * (nose_y - eye_y)
    else:
        top = y1 + 0.50 * fh
    bottom = min(h, y2 + 0.06 * fh)

    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle(
        [int(max(0, x1 - grow * fw)), int(max(0, top)),
         int(min(w, x2 + grow * fw)), int(bottom)], fill=255)
    return mask.filter(
        ImageFilter.GaussianBlur(radius=blur if blur else max(3, int(fw * 0.02))))


def auto_mask(rgb, size_hw, mode="largest", index=0, grow=0.05):
    """一步到位：检测 → 挑脸 → 出蒙版。返回 `(PIL L 图, 说明)`。"""
    faces = detect(rgb)
    face, why = pick(faces, mode, index)
    return build_mask(size_hw, face, grow=grow), "%d 张脸，用了%s" % (len(faces), why)
