# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""外接提示词：把**外部写好的故事板稿**接管进编辑器 state。

编辑器里逐格描述只有窄输入框，实际写法是在大文本框（Easy-Use 正面提示词之类）里写完再喂进来。
两条入口共用这一个解析器：`MossLayoutEditor` 的可选输入 `prompt`，以及编辑器提示词区的「应用」。

解析规则（确定性，不猜）：

1. 认 `[PANEL n]` / `[画面 n]` / `[分镜 n]`（与 `prompt.PANEL_RE` 同一套正则）。
2. 有标记 ⇒ 标记前是全局描述，第 i 段 → 第 i 格（按镜号排序后）。
3. 段首是合法景别词 ⇒ 剥出来当该格景别；没写 ⇒ **该格景别清空（不注入）**。
   外接文本是唯一真相源，不再拿编辑器里的景别偷偷拼回去。
4. 没有标记 ⇒ 整段当全局描述，逐格描述沿用编辑器。
5. 段数与格数不符 ⇒ 少了沿用、多了丢尾部，**都写进 notes**（不静默）。
"""

from . import editor as E
from . import prompt as T

#: 景别词后面允许出现的分隔符。剥完一起吃掉，否则 caption 会以「，」开头。
_SEP = "，,、:： \t　"

#: 英文景别前缀（长句优先，再退到术语），用于把本包自己编出来的稿再读回来。
_EN_PREFIX = sorted(
    [(v, k) for k, v in E.SIZE_EN.items()] + [(v.split(",")[0], k) for k, v in E.SIZE_EN.items()],
    key=lambda kv: -len(kv[0]),
)


def _split_size(cap):
    """`中景，她低头` / `Medium shot, framed from the waist up. 她低头` → `("中景", "她低头")`。

    没写景别 → `(None, cap)`。英文按本包 `SIZE_EN` 反查回中文档位，保证「应用」往返闭合。
    """
    cap = (cap or "").strip()
    for size in E.SIZES:
        if cap.startswith(size):
            rest = cap[len(size):]
            # 「中景」不是「中近景」的前缀，按表顺序试即可
            if not rest or rest[0] in _SEP:
                return size, rest.lstrip(_SEP)
    low = cap.lower()
    for prefix, size in _EN_PREFIX:
        if low.startswith(prefix.lower()):
            rest = cap[len(prefix):]
            if not rest or rest[0] in ".。，,":
                return size, rest.lstrip(".。 " + _SEP)
    return None, cap


def parse_storyboard(text):
    """故事板稿 → `{"style", "panels": [{"no", "size", "caption"}], "has_marks"}`。

    `size` 为 `None` 表示该段没写景别。
    """
    head, parts = T.split_panels("" if text is None else str(text))
    panels = []
    for no, cap in parts:
        size, rest = _split_size(cap)
        panels.append({"no": int(no), "size": size, "caption": rest.strip()})
    return {"style": head, "panels": panels, "has_marks": bool(parts)}


def apply_to_state(state, text):
    """把外接文本叠到 state 上，返回 `(新 state, notes)`。**不改入参。**

    `notes` 进 `report`，每条都说明"哪部分没接管"。
    """
    import copy

    st = copy.deepcopy(state)
    parsed = parse_storyboard(text)
    if not (parsed["style"] or parsed["panels"]):
        return st, ["外接提示词为空：沿用编辑器内容。"]

    notes = []
    if parsed["style"]:
        st["style"] = parsed["style"]
        notes.append("全局描述取自外接提示词。")

    if not E.is_grid(st):
        notes.append("单张模式没有分格：外接提示词只接管全局描述。")
        return st, notes

    if not parsed["panels"]:
        notes.append("外接提示词没有 [PANEL n] 标记：整段当全局描述，逐格描述沿用编辑器。")
        return st, notes

    items = sorted(st.get("items") or [], key=lambda s: s.get("no", 0))
    caps = parsed["panels"]
    n, m = len(items), len(caps)
    used = 0
    for i in range(min(n, m)):
        cap = caps[i]["caption"]
        if not cap:
            continue          # 空段不覆盖：宁可留旧描述，也不把一格清空
        items[i]["caption"] = cap
        # 外接文本是唯一真相源：它没写景别 ⇒ 这一格不注入（否则会和描述里的机位打架）
        items[i]["size"] = caps[i]["size"] or ""
        used += 1
    notes.append("外接提示词接管 %d 格描述（共 %d 段）。" % (used, m))
    if m < n:
        notes.append("外接提示词只有 %d 段，后 %d 格沿用编辑器描述。" % (m, n - m))
    elif m > n:
        notes.append("外接提示词有 %d 段，超过 %d 格：尾部 %d 段被丢弃。" % (m, n, m - n))
    kept = [i + 1 for i in range(min(n, m)) if caps[i]["caption"] and not caps[i]["size"]]
    if kept:
        notes.append("%d 段没写景别 ⇒ 这些格不注入景别（构图交给描述）。" % len(kept))
    return st, notes
