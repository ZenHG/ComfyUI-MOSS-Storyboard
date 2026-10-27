# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""编辑器状态层 v3：把布局状态编译成 MOSS 吃得下的提示词。

**不依赖任何模型、任何 LLM。** 全部是确定性转换：同样一份 state，永远编译出同样的字符串。
可单测、可 diff、零延迟。

两种模式共用一套 state：

- `preset == "grid"` ⇒ **宫格模式**。items 是格位，编译成 `[STYLE] + [PANEL n]`，
  下游逐格独立采样再拼版。格位 bbox **只给前端显示用，后端不消费**（每格都是全幅）。
- 其他 preset ⇒ **单张模式**。items 是元素，编译成一个多元素 layout JSON，一次采样整张。

三条实测前提（维护者本地实测记录）：

1. **元素级 bbox 是强约束**，不是软提示 —— 同 seed 换 bbox，构图完全改变。
2. **bbox 对文字：宽度有效，高度不严** —— 字会溢出框。不能用框高精确控字号。
3. **文字必须留安全边距** —— 贴顶（y0=5%）时被画布裁掉过。建议 y0 ≥ 0.10、y1 ≤ 0.90。

state 结构见 `docs/10-contract.md` §一（冻结契约）。
"""

import json
import math
import re

from . import layout as L
from . import prompt as T

#: state 版本闸。读到别的值直接报错，不做兼容猜测。
STATE_VERSION = 3

#: 契约上限（`docs/10-contract.md` §一）：state 字符串 ≤ 1,000,000 字符、items ≤ 256 个。
#: 前者在 `json.loads` 之前判，避免恶意工作流用超长字符串冻结宿主。
MAX_STATE_CHARS = 1_000_000
MAX_ITEMS = 256

#: 画布与单格像素的上下限（`docs/10-contract.md` §一）：64..4096、16 的倍数。
#: 宫格行列上限与 `layout.MAX_AXIS` 共用一份值。
PX_MIN = 64
PX_MAX = 4096

#: 宫格缝宽 / 外边距上限（与前端校验器和编辑器输入控件一致）。
GAP_MAX = 256

#: palette 唯一合法形态：`#rrggbb`（7 字符、6 位十六进制）。8 位 alpha 与 `null` 都不收。
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

#: 零宽 / 方向控制字符：只有这些字符的 caption / text 渲染不出来，按空处理。
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff]")


def is_blank(text):
    """空白 + 零宽 + 方向控制字符组成的字符串视为空（前端 `isBlank` 同口径）。"""
    return not _INVISIBLE_RE.sub("", text).strip()


#: 预设占位文案，取自 `PRESET_SPECS`——改预设必须两边一起改（`test_cross_language` 盯着）。
#: text 占会被逐字渲染 ⇒ 排队前确认一次；caption 占位只警告。
_GRID_CAPTION_RE = re.compile(r"^(?:第 \d+ 格画面|Panel \d+)$")
_PLACEHOLDER_CAPTIONS = frozenset((
    "主体", "背景元素", "前景主体", "主体，居中，四周留白", "主角半身，侧对镜头",
    "粗体无衬线标题", "细体说明文字", "粗体无衬线标题，浅金色", "细体小字，白色",
    "主体（左）", "主体（中）", "主体（右）",
    "Main subject", "Background element", "Foreground element",
    "Main subject, centered, generous margins", "Main character, half body, side profile",
    "Bold sans-serif title", "Light body text", "Bold sans-serif title, pale gold",
    "Small light text, white", "Subject (left)", "Subject (middle)", "Subject (right)",
))
_PLACEHOLDER_TEXTS = frozenset((
    "标题文字", "正文文字", "副标题文字",
    "Title", "Body text", "Subtitle",
))

#: 预设清单。`grid` 是唯一的宫格模式，其余都是单张模式。
PRESETS = ("grid", "split-v", "split-h", "title-body",
           "three-col", "center", "stack", "free")

#: 宫格模式的景别**预设**。顺序 = 由远到近。UI 用这八档，但 `size` 字段本身接受自定义文本。
SIZES = ("大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写")

#: 八档 → 英文构图子句。依据 MOSS USERGUIDE：默认推荐英文，构图用自然语言
#: （官方示例 "A wide view of ..."）。UI 仍显示中文档位，**只有输出用英文**。
SIZE_EN = {
    "大远景": "Extreme wide shot, the environment fills the frame and the figure is tiny",
    "远景": "Wide shot, the full figure in frame with the setting around it",
    "全景": "Full shot, the whole figure just fits the frame",
    "中景": "Medium shot, framed from the waist up",
    "中近景": "Medium close shot, framed from the chest up",
    "近景": "Close shot, framed from the shoulders up",
    "特写": "Close-up, the face fills the frame",
    "大特写": "Extreme close-up, only the eyes or a single detail",
}

#: 自定义景别的长度上限（字符）。超了直接报错，不截断。
SIZE_MAX = 64

#: 描述里自带的可见范围词 → 它实际在说哪一档。只用于报警，不改存档、不改提示词。
_SIZE_WORDS = (
    (re.compile(r"(?:人物|人)(?:很|极)?小|渺小|环境占(?:满|据)画面"), "大远景"),
    (re.compile(r"全身(?:入画|像|构图)?|远景"), "远景"),
    (re.compile(r"半身|腰部以上|腰以上|齐腰"), "中景"),
    (re.compile(r"胸(?:部)?以上|胸口以上|中近景"), "中近景"),
    (re.compile(r"肩(?:部)?以上|近景"), "近景"),
    (re.compile(r"面部占满|脸部特写|面部特写|特写"), "特写"),
    (re.compile(r"只(?:取|拍)(?:眼睛|眼部|局部)|大特写"), "大特写"),
)

#: 描述里自带的可见范围词 → 实际在说哪一档。只用于报警，不改存档。
_SIZE_WORDS = (
    (re.compile(r"(?:人物|人)(?:很|极)?小|渺小|环境占(?:满|据)画面"), "大远景"),
    (re.compile(r"全身(?:入画|像|构图)?|远景"), "远景"),
    (re.compile(r"半身|腰部以上|腰以上|齐腰"), "中景"),
    (re.compile(r"胸(?:部)?以上|胸口以上|中近景"), "中近景"),
    (re.compile(r"肩(?:部)?以上|近景"), "近景"),
    (re.compile(r"面部占满|脸部特写|面部特写|特写"), "特写"),
    (re.compile(r"只(?:取|拍)(?:眼睛|眼部|局部)|大特写"), "大特写"),
)


def empty_state(preset="free"):
    """空状态。单张模式给 1536×864 空白画布（= 前端 `DEFAULT_CANVAS`）。

    宫格模式给 1×1 的**空白占位**：`items` 为空，`parse_state` 把它认作合法空白态，
    `geometry()` 本来就按 1 格占位算。它不是可渲染的故事板 —— 要格位请用
    `presets.build("grid")`（默认 4×2）。
    """
    st = {
        "version": STATE_VERSION,
        "preset": preset,
        "style": "",
        "items": [],
    }
    if preset == "grid":
        st["grid"] = {"cols": 1, "rows": 1, "panel_aspect": "16:9",
                      "panel_edge": 512, "gutter": 8, "margin": 8}
    else:
        st["canvas"] = {"width": 1536, "height": 864}
    return st


def is_grid(state):
    return state.get("preset") == "grid"


def _fail(msg):
    raise ValueError(msg)


def _is_int(v):
    """JSON 没有 int/float 之分：`3` 与 `3.0` 在浏览器里是同一个数。

    所以整数字段接受**整值浮点**（`1536.0`），但拒绝 `1536.5`、bool 与 NaN/Infinity。
    """
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v) and v.is_integer()


def _reject_constant(name):
    raise ValueError("JSON 里不允许 %s（不是合法 JSON）" % name)


def _check_bbox(bbox, where):
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        _fail("%s 的 bbox 必须是 4 个数 [x0,y0,x1,y1]（收到 %r）。" % (where, bbox))
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in bbox):
        _fail("%s 的 bbox 必须全是数字，不收字符串/布尔（收到 %r）。" % (where, bbox))
    if any(not math.isfinite(float(v)) for v in bbox):
        _fail("%s 的 bbox 含 NaN / Infinity；只收有限数字（收到 %r）。" % (where, bbox))
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        _fail("%s 的 bbox 越界或反向：需要 0 ≤ x0 < x1 ≤ 1、0 ≤ y0 < y1 ≤ 1"
              "（收到 %r）。bbox 是**归一化**坐标，不是像素。" % (where, list(bbox)))


def parse_state(text):
    """解析并逐字段校验。不合法就抛 ValueError，文案要能指导修复。"""
    raw = "" if text is None else str(text)
    if len(raw) > MAX_STATE_CHARS:
        _fail("编辑器状态有 %d 个字符，超过上限 %d。请拆分工作流或清空重画。"
              % (len(raw), MAX_STATE_CHARS))
    if not raw.strip():
        return empty_state()
    try:
        # Python 的 json 默认接受 NaN / Infinity；它们不是合法 JSON，
        # 编辑器（JSON.parse）也读不了，必须在这里就拒绝。
        state = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError) as exc:
        _fail("编辑器状态不是合法 JSON（%s）。请恢复工作流，或在编辑器里重置。" % exc)
    if not isinstance(state, dict):
        _fail("编辑器状态必须是 JSON 对象，收到 %s。" % type(state).__name__)

    ver = state.get("version")
    if not _is_int(ver) or ver != STATE_VERSION:
        _fail("编辑器状态版本是 %r，本版只认 %d。旧工作流的 state 需要重存。"
              % (ver, STATE_VERSION))
    state["version"] = STATE_VERSION

    preset = state.get("preset")
    if preset not in PRESETS:
        _fail("preset=%r 不认识，可选：%s。" % (preset, "、".join(PRESETS)))

    style = state.get("style")
    if not isinstance(style, str):
        _fail("style 必须是字符串（收到 %s）。" % type(style).__name__)

    items = state.get("items")
    if not isinstance(items, list):
        _fail("items 必须是数组（收到 %s）。" % type(items).__name__)
    if len(items) > MAX_ITEMS:
        _fail("items 有 %d 个，超过上限 %d。请拆成多张画布。" % (len(items), MAX_ITEMS))

    if preset == "grid":
        grid = state.get("grid")
        if not isinstance(grid, dict):
            _fail("preset=grid 时必须有 grid 对象（收到 %s）。" % type(grid).__name__)
        for key in ("cols", "rows"):
            v = grid.get(key)
            if not _is_int(v) or not 1 <= v <= L.MAX_AXIS:
                _fail("grid.%s 必须是 1..%d 的整数（收到 %r）。" % (key, L.MAX_AXIS, v))
            grid[key] = int(v)
        # 空白宫格态（1×1、items 为空）是 empty_state("grid") 的占位，允许；
        # 其余情况行列数必须与格数一致。
        blank = not items and grid["cols"] == 1 and grid["rows"] == 1
        if not blank and grid["cols"] * grid["rows"] != len(items):
            _fail("grid.cols×rows = %d×%d = %d，但 items 有 %d 个。行列数必须与格数一致。"
                  % (grid["cols"], grid["rows"],
                     grid["cols"] * grid["rows"], len(items)))
        if grid.get("panel_aspect") not in L.ASPECTS:
            _fail("grid.panel_aspect=%r 不认识，可选：%s。"
                  % (grid.get("panel_aspect"), "、".join(L.ASPECTS)))
        edge = grid.get("panel_edge")
        if not _is_int(edge) or not PX_MIN <= edge <= PX_MAX or edge % L.SNAP:
            _fail("grid.panel_edge 必须是 %d..%d 且为 %d 的倍数（收到 %r）。"
                  % (PX_MIN, PX_MAX, L.SNAP, edge))
        grid["panel_edge"] = int(edge)
        for key in ("gutter", "margin"):
            v = grid.get(key)
            if not _is_int(v) or not 0 <= v <= GAP_MAX:
                _fail("grid.%s 必须是 0..%d 的整数（收到 %r）。" % (key, GAP_MAX, v))
            grid[key] = int(v)
    else:
        canvas = state.get("canvas")
        if not isinstance(canvas, dict):
            _fail("preset=%s（单张模式）时必须有 canvas 对象。" % preset)
        for key in ("width", "height"):
            v = canvas.get(key)
            if not _is_int(v) or not PX_MIN <= v <= PX_MAX or v % L.SNAP:
                _fail("canvas.%s 必须是 %d..%d 且为 %d 的倍数（收到 %r）。"
                      % (key, PX_MIN, PX_MAX, L.SNAP, v))
            canvas[key] = int(v)

    seen = set()
    for i, it in enumerate(items):
        where = "第 %d 个元素" % (i + 1)
        if not isinstance(it, dict):
            _fail("%s 必须是对象（收到 %s）。" % (where, type(it).__name__))
        ident = it.get("id")
        if not isinstance(ident, str) or not ident:
            _fail("%s 缺少 id（字符串，前端生成，必须唯一）。" % where)
        if ident in seen:
            _fail("元素 id=%r 重复了。id 必须唯一。" % ident)
        seen.add(ident)
        label = it.get("label")
        if label not in T.LABELS:
            _fail("%s 的 label=%r 不认识（必填），可选：%s。"
                  % (where, label, "/".join(T.LABELS)))
        if not isinstance(it.get("caption"), str) or is_blank(it["caption"]):
            _fail("%s 没有描述（caption 不能为空）。" % where)
        if label == "text":
            txt = it.get("text")
            if not isinstance(txt, str) or is_blank(txt):
                _fail("%s 是文字元素，必须填 text（**实际渲染的字**）。"
                      "caption 只描述样式，不能当 text 用。" % where)
        if "palette" in it:
            pal = it["palette"]
            if not isinstance(pal, list) or len(pal) > 5:
                _fail("%s 的 palette 必须是数组，最多 5 个颜色（收到 %r）。" % (where, pal))
            for c in pal:
                if not (isinstance(c, str) and _HEX_RE.match(c)):
                    _fail("%s 的 palette 颜色必须是 #rrggbb（7 字符、6 位十六进制，"
                          "收到 %r）。" % (where, c))
        _check_bbox(it.get("bbox"), where)
        if preset == "grid":
            no = it.get("no")
            if not _is_int(no):
                _fail("%s 的镜号 no 必须是整数（收到 %r）。" % (where, no))
            it["no"] = int(no)
            # size 是自由文本：八档只是预设，用户可写自己的（空串 = 不注入）。
            size = it.get("size")
            if not isinstance(size, str):
                _fail("%s 的景别 size 必须是字符串（收到 %s）。"
                      % (where, type(size).__name__))
            size = size.strip()
            if len(size) > SIZE_MAX:
                _fail("%s 的景别有 %d 字，超过上限 %d。"
                      % (where, len(size), SIZE_MAX))
            it["size"] = size
    if preset == "grid":
        nos = [it["no"] for it in items]
        if len(set(nos)) != len(nos):
            _fail("镜号 no 有重复：%r。镜号必须唯一。" % sorted(nos))
    return state


def geometry(state):
    """宫格模式的几何。唯一入口 `layout.build_layout`，本模块不碰坐标（铁律 4）。

    单张模式返回 None —— 画布尺寸由 `state.canvas` 直接给，不需要算。
    """
    if not is_grid(state):
        return None
    g = state["grid"]
    n = len(state["items"]) or 1
    return L.build_layout(
        panels=n, cols=g["cols"], rows=g["rows"],
        gutter=g["gutter"], margin=g["margin"],
        panel_edge=g["panel_edge"], panel_aspect=g["panel_aspect"],
        note="来自编辑器 state" if state["items"] else "来自编辑器 state（占位 1 格）",
    )


def canvas_size(state):
    """采样画布尺寸 `(width, height)`。宫格模式 = 拼版后的整张；单张模式 = canvas 字段。"""
    if is_grid(state):
        lay = geometry(state)
        return lay.width, lay.height
    c = state["canvas"]
    return int(c["width"]), int(c["height"])


def _size_prefix(size, caption):
    """把景别拼进该格描述：八档走英文子句，自定义原样，空串不注入。

    分隔符：英文子句用 `. `（两条独立约束），中文自定义用 `，`。
    """
    cap = (caption or "").strip()
    size = (size or "").strip()
    if not size:
        return cap
    out = SIZE_EN.get(size, size)
    if not cap or cap.startswith((size, out)):
        return cap or out
    sep = ". " if re.fullmatch(r"[\x20-\x7e]+", out) else "，"
    return out + sep + cap


def compile_prompt(state):
    """宫格模式 → `[STYLE]` + 逐格 `[PANEL n]`；单张模式 → 只有全局段。

    ⚠ `[PANEL n]` 编号必须**连续 1..N**：`prompt.PANEL_RE` 解析段序号，
    `pair_captions` 按位置配对。镜号 `no` 只用于排序与显示，不进入标记。
    """
    style = (state.get("style") or "").strip()
    if not is_grid(state):
        return "[STYLE] %s" % style if style else "[STYLE]"
    items = sorted(state["items"], key=lambda s: s["no"])
    if not items:
        return ""
    head = "[STYLE] %s" % style if style else "[STYLE]"
    lines = [head, ""]
    for i, it in enumerate(items):
        lines.append("[PANEL %d] %s" % (i + 1, _size_prefix(it["size"], it["caption"])))
    return "\n".join(lines).strip()


def _item_to_json(it, width, height):
    """单个元素 → layout JSON 的 item（坐标从画布归一化换到中心原点像素）。"""
    item = {"label": it["label"], "caption": it["caption"].strip(),
            "bbox": T.cell_bbox_to_centered(it["bbox"], width, height)}
    if it["label"] == "text":
        item["text"] = it["text"].strip()
    if it.get("palette"):
        item["palette"] = list(it["palette"])
    return item


def layout_json(state, width=None, height=None):
    """**单张模式**：所有元素合成一个 layout JSON，一次采样整张。

    `width`/`height` 默认取 `canvas`；显式传值用于「画布与采样尺寸不同」的场景。
    """
    if is_grid(state):
        _fail("宫格模式没有整张 layout JSON —— 每格独立采样，请用 shot_layout_json()。")
    cw, ch = canvas_size(state)
    w = int(width or cw)
    h = int(height or ch)
    items = [_item_to_json(it, w, h) for it in state["items"]]
    if not items:
        return (state.get("style") or "").strip()
    payload = {"summary": {"caption": (state.get("style") or "").strip()},
               "items": items,
               "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def shot_layout_json(state, shot, width, height, label="object"):
    """**宫格模式**：单格 layout JSON。`width`/`height` 是**该格的采样画布**（pw × ph）。

    格位 bbox 是前端显示用的，这里不消费 —— 每格采样都是全幅。
    """
    return T.single_panel_json(state.get("style") or "", shot.get("caption") or "",
                               width, height, label=label, elements=None)


def compile_final_prompt(state):
    """最终发给模型的文本：宫格 → `[STYLE]+[PANEL n]`，单张 → 原生布局 JSON。

    前端预览走 `moss_sb_editor.js::compileFinalPrompt`，两边逐字节比对，别单边做简化版。
    """
    if is_grid(state):
        return compile_prompt(state)
    return layout_json(state)


def find_placeholders(state):
    """仍是预设占位的字段 → `[{id, field, kind}]`（保持 items 顺序）。

    `render` = 会被逐字渲染的 text；`generic` = caption。宫格的 text 不进提示词，故不拦。
    """
    grid = is_grid(state)
    found = []
    for it in state.get("items") or []:
        if not isinstance(it, dict):
            continue
        cap = it.get("caption")
        cap = cap.strip() if isinstance(cap, str) else ""
        if cap and (cap in _PLACEHOLDER_CAPTIONS or _GRID_CAPTION_RE.match(cap)):
            found.append({"id": it.get("id"), "field": "caption", "kind": "generic"})
        if not grid and it.get("label") == "text":
            txt = it.get("text")
            txt = txt.strip() if isinstance(txt, str) else ""
            if txt and txt in _PLACEHOLDER_TEXTS:
                found.append({"id": it.get("id"), "field": "text", "kind": "render"})
    return found


def find_ignored_grid_fields(state):
    """宫格模式不消费的字段（只出声，不改用户存档）。"""
    if not is_grid(state):
        return []
    out = []
    for it in state.get("items") or []:
        if not isinstance(it, dict):
            continue
        fields = []
        if it.get("label") != "object":
            fields.append("label")
        if isinstance(it.get("text"), str) and not is_blank(it["text"]):
            fields.append("text")
        if it.get("palette"):
            fields.append("palette")
        if fields:
            out.append({"id": it.get("id"), "fields": fields})
    return out


def find_size_conflicts(state):
    """景别与描述**自己写的**可见范围打架 → `[{id, size, says, word}]`。

    为什么只报警不改写（`docs/07` §三 2026-10-26 踩坑）：全局写"浅景深"、格内要
    "大远景"时，模型最省事的解法是**退回中景** —— 也就是两处说法打架时它不选其一，
    而是选中间。改写用户描述等于替他做导演，所以这里只出声。

    判据：描述里出现明确的可见范围词，且与所选景别**相差 ≥ 2 档**才算打架
    （相邻档本来就可能分不开，报了只是噪音）。
    """
    if not is_grid(state):
        return []
    out = []
    for it in state.get("items") or []:
        if not isinstance(it, dict):
            continue
        size = it.get("size")
        cap = it.get("caption")
        if size not in SIZES or not isinstance(cap, str) or not cap:
            continue
        idx = SIZES.index(size)
        for pat, implied in _SIZE_WORDS:
            m = pat.search(cap)
            if not m:
                continue
            if abs(SIZES.index(implied) - idx) >= 2:
                out.append({"id": it.get("id"), "size": size,
                            "says": implied, "word": m.group(0)})
                break
    return out


def _item_at(state):
    """id → 人话位置。原始 id（`e-8f2a1c3d`）用户看不出指的是谁，报"第几格/第几个"才能动手改。"""
    grid = is_grid(state)
    out = {}
    for n, it in enumerate(state.get("items") or [], 1):
        if isinstance(it, dict):
            out[it.get("id")] = "第 %s 格" % (it.get("no") or n) if grid else "第 %d 个" % n
    return out


def placeholder_summary(state):
    """给 `report` 用的一句话；没有占位返回 None。"""
    found = find_placeholders(state)
    if not found:
        return None
    at = _item_at(state)
    parts = []
    for kind, text in (("render", "%d 个文字框仍是预设文案（会被逐字渲染：%s）"),
                       ("generic", "%d 个描述仍是预设占位（画面会雷同：%s）")):
        hit = [f for f in found if f["kind"] == kind]
        if hit:
            parts.append(text % (len(hit), "、".join(at.get(f["id"], "?") for f in hit[:3])))
    return "⚠ " + "；".join(parts) + "。"


def report(state):
    """给人看的总结：模式、尺寸、元素统计、告警。"""
    out = []
    if is_grid(state):
        lay = geometry(state)
        g = state["grid"]
        items = sorted(state["items"], key=lambda s: s["no"])
        out.append("宫格 %d 格 = %d 列 × %d 行" % (len(items) or lay.panels, g["cols"], g["rows"]))
        out.append("画布 %d × %d（%.2fMP）｜单格 %d × %d（%.2fMP）"
                   % (lay.width, lay.height, lay.megapixels,
                      lay.panel_w, lay.panel_h, lay.panel_megapixels))
        if not items:
            out.append("还没有分镜。在编辑器里新增一格，或换一个预设。")
            return "\n".join(out)
        out.append("景别：%s" % " → ".join(s["size"] or "—" for s in items))
        sizes = [s["size"] for s in items if s["size"]]
        if len(items) >= 3 and len(sizes) == len(items) and len(set(sizes)) == 1:
            out.append("⚠ %d 格景别全是「%s」：画面会雷同。把景别拉开到 3 档以上。"
                       % (len(items), sizes[0]))
        elif len(items) >= 3 and len(set(sizes)) == 2:
            out.append("· 景别只有 2 档，梯度偏平；建议 3 档以上。")
        clash = find_size_conflicts(state)
        if clash:
            at = _item_at(state)
            out.append("⚠ %d 格景别与描述打架：%s。两处说法冲突时模型常退回中景，改一处。"
                       % (len(clash),
                          "、".join("%s 选「%s」但写「%s」" % (at.get(c["id"], "?"), c["size"], c["word"])
                                   for c in clash[:3])))
        ignored = find_ignored_grid_fields(state)
        if ignored:
            out.append("⚠ %d 个格位带有 label/text/palette：宫格模式只消费镜号/景别/描述，这些字段不参与提示词。"
                       % len(ignored))
        ph = placeholder_summary(state)
        if ph:
            out.append(ph)
        return "\n".join(out)

    w, h = canvas_size(state)
    items = state["items"]
    out.append("单张模式 ｜ preset=%s ｜ 画布 %d × %d（%.2fMP）"
               % (state["preset"], w, h, w * h / 1e6))
    if not items:
        out.append("还没有元素。换一个预设，或在画布上画框。")
        return "\n".join(out)
    by_label = {}
    for it in items:
        by_label[it["label"]] = by_label.get(it["label"], 0) + 1
    out.append("元素 %d 个：%s" % (len(items),
                               "、".join("%s×%d" % (k, v) for k, v in by_label.items())))
    ph = placeholder_summary(state)
    if ph:
        out.append(ph)
    # 文字安全边距（实测：贴顶会被裁）
    risky = [it for it in items if it["label"] == "text"
             and (it["bbox"][1] < 0.10 or it["bbox"][3] > 0.90)]
    if risky:
        out.append("⚠ %d 个文字框贴边（y0<0.10 或 y1>0.90）：实测会被画布裁掉。"
                   "把文字框往里收。" % len(risky))
    # 重叠检测（stack 预设刻意重叠，跳过）
    if state["preset"] != "stack":
        ov = _overlaps(items)
        if ov:
            out.append("· %d 组元素框重叠：%s（要层叠请用 stack 预设）"
                       % (len(ov), "、".join("%s/%s" % p for p in ov[:3])))
    return "\n".join(out)


def _overlaps(items):
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i]["bbox"], items[j]["bbox"]
            if a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]:
                out.append((items[i]["id"], items[j]["id"]))
    return out
