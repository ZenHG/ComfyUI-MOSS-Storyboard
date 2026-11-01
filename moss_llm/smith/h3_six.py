# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""H3 全参考六段式（h3-six-section）：IR 的第四种投影。

## 它是什么

H3 基础模式把提示词摊成三字段（`integrated_multimodal_description` +
`overall_soundscape` + `non_diegetic_music`，见 `h3.py`）。**全参考六段式是另一套
骨架**：同样一份 IR，按参考标签（`<Subject N>` / `<Video N>` / `<Audio N>` /
`<Picture N>` / `@图片N`）先声明「有哪些主体、各自是什么」，再写摘要、保留分析、
带 `[Shot N]` 与 `At MM:SS.mmm` 的详细描述、声景与画外配乐。

两套骨架**并存**，不是替换：基础模式继续给 `--target minimax-h3` 用，六段式作为
`minimax-h3` 的方言档（`--profile six-section`）挂上去。为什么不做成独立 target：
新增 target 要在 `adapters/__init__.py` 的 `ADAPTERS` 表里登记（那不在本模块的
写域内），而两套骨架共用同一套参考标签解析、时间线语义与机位约定——扩展现有原生
H3 adapter 比为它复制一份 adapter 更不容易分叉。通用后端（generic）走不了这条路：
它只会把 IR 摊成散文/tag，没有参考标签与六段顺序的概念。

## 三个方向

| 方向 | 函数 | 用途 |
|---|---|---|
| IR → 六段文本 | `render(ir)` | 编译；有骨架时逐字回放，没有骨架时 `synthesize()` |
| 六段文本 → IR | `parse(text)` | 读别人的稿 / 改词前先变成可核对的事实 |
| 六段文本 → 判定 | `check(text)` | 提交前的机检闸（不需要 IR） |

## 格式真相源

不是推测，是**产线真稿**（2026-10-08 标注包里的 A01 / A03 / A06 三段，逐字存进
`tests/fixtures/h3_six/`）加上官方格式规范里列出的参考标签口径：

    subject_definitions:
    <Video 1> ……续接基准……
    <Subject N> 名字：外观/服装/材质锚点……
    ……（座位图 / 机位基线这类项目硬事实可以留在这里）
    @图片N ……定稿参考图。

    summary:
    [video continuation] N 秒续接段落，……（或 [reference generation]）

    retention_analysis:
    <Video 1> (continuation source / previous last frame): fully_preserved - ……
    <Subject N> (appears in [Shot 1], [Shot 2]): fully_preserved - ……

    detailed_description:
    ……段首说明……
    [Shot 1] ……画面与动作……
    [Shot 2] At 00:03.000, ……
    【项目影像规格】
    - 质感：……
    ……

    overall_soundscape:
    ……

    non_diegetic_music:
    N/A

`【项目影像规格】` 是产线项目模板块，不是官方六段字段之一。**parse 原样保留、
synthesize 不生成**——通用 IR 里没有「质感/动态/光/色」这套项目规范的位置，
硬编一段等于替用户编事实。

## 往返口径（必须先声明，否则「通过」没有意义）

- **字节级**：3 段真稿 `render(IR.from_dict(parse(t))) == t`，逐字节相等（含空行与
  全角标点）。为什么做得到：`parse` 保留两种东西——参考标签行 / 摘要 / 保留分析 /
  镜头行按结构化字段存（`<Subject 1> body` 这种格式由 render 重建），自由散文
  （段首说明、项目模板、声景、配乐）逐行原文保存。这是「能结构化的结构化，不能
  结构化的逐字留」，与 `h3.parse` 把认不出的句子原样放进 `detail` 是同一条纪律。
- **事实级**：`parse` 同时写出 `timeline.shots`（镜头号、`At` 时间戳、台词 `<d>`
  的说话人与文本）与 `duration_s`（从 summary 的「七点三秒 / 八秒」解析）。
  六段式**不要求**每镜都带时间戳，所以 Shot 1 没时间戳、后续镜头也可以不带。

## 独立错误码（可分别判）

| 码 | 守什么 | 级别 |
|---|---|---|
| `H3S-MISSING-SECTION` | 缺段（六个字段必须齐全） | error |
| `H3S-SECTION-ORDER` | 段序错（顺序固定为六段的声明顺序） | error |
| `H3S-TS-RANGE` | 时间戳越界（`At` 必须早于段落时长） | error |
| `H3S-SECTION-EMPTY` | 某段为空 | error |
| `H3S-SECTION-DUP` | 段重复 | error |
| `H3S-SHOT-SEQ` | `[Shot N]` 不是 1..N 连号 | error |
| `H3S-NO-SHOTS` | 详细描述里没有任何 `[Shot N]` | error |
| `H3S-TS-ORDER` | `At` 时间戳没有严格递增 | error |
| `H3S-REF-UNDEFINED` | 详细描述引用了未定义的 `<Subject N>` | error |
| `H3S-RETENTION-REF` | 保留分析引用了未定义的主体/视频 | error |
| `H3S-D-LANG` | `<d>` 缺语言标签 | error |
| `H3S-MUSIC` | `non_diegetic_music` 为空或不可读 | error |
| `H3S-FENCE` | 带 Markdown 代码围栏 | error |
| `H3S-SUMMARY-TAG` | summary 首行没有 `[video continuation]` 这类标记 | warn |
| `H3S-DURATION-UNKNOWN` | 解析不出段落时长 ⇒ 时间戳越界无法判 | warn |
| `H3S-SOUNDSCAPE-NA` | 声景写 N/A（只有全片静音才允许） | warn |
| `H3S-SHOT1-TS` | Shot 1 带了 `At`（会把首帧锁成置景图，见铁律 11） | warn |

只覆盖**形式与自洽**。表演、情绪、构图好坏一条都不判——那不是机检能管的事。
`check()` 全绿只说明「六段齐、序对、时间戳在段落内、引用都有定义」。
"""

from __future__ import annotations

import re

from . import h3
from .ir import IR
from .lint import Issue

# ─────────────────────────────────────────────────────────────
# 常量：格式即方言，改这里等于改方言
# ─────────────────────────────────────────────────────────────

#: 六个字段，顺序固定。与 registry 的 `minimax-h3.dialects.six-section.fields`
#: 是同一份口径；代码是解析侧的真值源，registry 是给人看/给 tooling 读的镜像。
FIELDS = ("subject_definitions", "summary", "retention_analysis",
          "detailed_description", "overall_soundscape", "non_diegetic_music")

#: 段头行：`subject_definitions:` 或 `overall_soundscape: 一行内容`（产线两种都写）。
#: 只吃行内空白（`[ \t]`）：用 `\s` 会把后面的换行也吞掉，空段后面的段头就再也
#: 匹配不到了（实测：`summary:\n\n\nretention_analysis:` 会把 retention 吞进 summary）。
_SECTION_RE = re.compile(r"^(%s)[ \t]*:[ \t]*(.*)$" % "|".join(FIELDS), re.M)

#: 参考标签行：`<Subject 1> 陈默：……` / `<Video 1> ……` / `<Audio 1> ……`
_REF_RE = re.compile(r"^<((?:Subject|Video|Audio|Picture))\s+(\d+)\s*>\s*(.*)$")

#: 参考图行：`@图片1 A1 陈默·基础三联 定稿参考图。`
_IMAGE_REF_RE = re.compile(r"^@图片\s*(\d+)\s*(.*)$")

#: 保留分析行：`<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - ……`
#: 括号允许为空（真稿里有 `<Subject 3> (appears in ): fully_preserved - ……`）。
_RETENTION_RE = re.compile(
    r"^(<[^<>]+>)\s*(\([^)]*\))?\s*:\s*([A-Za-z_]+)\s*-\s*(.*)$")

#: 详细描述里的镜头行：`[Shot 2] At 00:03.000, 中景……`
_SHOT_LINE_RE = re.compile(r"^\[Shot\s+(\d+)\]\s*(.*)$")
#: 镜头行行首时间戳。与 `h3.TS_RE` 同一写法，但只认行首、且把整段前缀一起吃掉。
_AT_HEAD_RE = re.compile(r"^At\s+(\d{2}):(\d{2})\.(\d{3})\s*,?\s*")
#: 正文任意位置的 `At MM:SS.mmm`（镜头行行首 + 镜内动作节拍都要查越界）。
_AT_ALL_RE = re.compile(r"At\s+(\d{2}):(\d{2})\.(\d{3})")

#: 项目影像规格块起始行。
_SPEC_RE = re.compile(r"^【项目影像规格】")

#: summary 首行的模式标记。
_SUMMARY_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

#: 段落时长：产线写中文（「七点三秒」「六点六秒」「八秒」），也接受阿拉伯数字。
_DUR_ZH_POINT_RE = re.compile(r"([一二两三四五六七八九十]+)点([一二三四五六七八九])秒")
_DUR_ZH_INT_RE = re.compile(r"([一二两三四五六七八九十]+)秒")
_DUR_AR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*秒")
_ZH_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

#: 合成单镜时的续接基准行（产线 A01/A03/A06 的开头逐字口径）。
_VIDEO_CONTINUATION_LINE = (
    "是上一段的输出视频，本段从其末帧无缝续写；提供续接起点、机位方向与高度、"
    "镜头晃动轨迹、光向与色温、角色站位与朝向的承接基准。")


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────

def _strip_fence(text: str) -> str:
    """剥 Markdown 代码围栏。用户会把整段从聊天窗口粘进来。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _zh_number(s: str) -> int | None:
    """中文数字（1–99）→ int。只覆盖段落时长会用到的范围。"""
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        tens, _, ones = s.partition("十")
        t = _ZH_DIGITS.get(tens, 1) if tens else 1
        o = _ZH_DIGITS.get(ones, 0) if ones else 0
        return t * 10 + o
    return _ZH_DIGITS.get(s)


def duration_from_summary(text: str) -> float | None:
    """从 summary 正文解析段落时长（秒）。解析不出返回 `None`，不猜默认 8s。

    为什么必须从 summary 读：六段式的时长声明在那里（「七点三秒续接段落」），
    而时间戳越界判据需要它。退而用 IR 的 `timeline.duration_s` 是下策——
    对 parse 出来的真稿，两者应当一致；不一致时以 summary 为准（用户看到的那份）。
    """
    s = text or ""
    m = _DUR_ZH_POINT_RE.search(s)
    if m:
        whole = _zh_number(m.group(1))
        frac = _ZH_DIGITS.get(m.group(2))
        if whole is not None and frac is not None:
            return round(whole + frac / 10.0, 3)
    m = _DUR_ZH_INT_RE.search(s)
    if m:
        whole = _zh_number(m.group(1))
        if whole:
            return float(whole)
    m = _DUR_AR_RE.search(s)
    if m:
        return float(m.group(1))
    return None


def _label_of(body: str) -> str:
    """`陈默：东亚男性……` → `陈默`；没有冒号时取开头一小段。

    label 只用于 IR 实体的短称（对话/摘要里引用），不参与六段文本渲染——
    六段文本里的主体名就是 body 原文，一个字都不动。
    """
    s = (body or "").strip()
    for sep in ("：", ":"):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if head:
                return head[:40]
    return s[:24]


def _at_seconds(text: str) -> list:
    """正文里所有 `At MM:SS.mmm` 的秒值，按出现顺序。"""
    return [int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000.0
            for m in _AT_ALL_RE.finditer(text or "")]


# ─────────────────────────────────────────────────────────────
# 拆段
# ─────────────────────────────────────────────────────────────

def split_sections(text: str) -> tuple:
    """→ (前言, {段名: 段内容}, [实际段序])。**缺的段不补**，让 check 去报。

    段内容是去掉首尾空行后的原文（内部空行保留）。`overall_soundscape: 一行`
    这种把值写在段头同行的写法也支持：段头冒号后面的剩余部分算内容第一行。
    """
    t = _strip_fence(text or "")
    hits = list(_SECTION_RE.finditer(t))
    preamble = t[:hits[0].start()].strip() if hits else t.strip()
    sections: dict = {}
    order: list = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(t)
        name = m.group(1)
        inline = m.group(2).rstrip()
        tail = t[m.end():end]
        content = ((inline + tail) if inline else tail).strip("\n")
        sections[name] = content
        order.append(name)
    return preamble, sections, order


# ─────────────────────────────────────────────────────────────
# 分段解析：结构化的进结构，散文逐字留
# ─────────────────────────────────────────────────────────────

def _parse_definitions(content: str) -> dict:
    """subject_definitions 段 → 逐行 plan + 分类视图。

    plan 保留行的**顺序与种类**（含空行）：渲染时按 plan 重建，所以顺序本身
    不丢——产线真稿里「主体定义 → 座位图/机位基线 → 空行 → @图片」的排版是
    有意义的（参考图紧跟主体之后），重新排序会改稿。
    """
    lines = content.split("\n") if content else []
    plan: list = []
    video: list = []
    subjects: list = []
    extras: list = []
    references: list = []
    for line in lines:
        s = line.strip()
        if not s:
            plan.append({"type": "blank"})
            continue
        m = _REF_RE.match(s)
        if m:
            kind, rid, body = m.group(1), int(m.group(2)), m.group(3).strip()
            if kind == "Subject":
                subjects.append({"ref": "<Subject %d>" % rid, "id": rid,
                                 "body": body, "label": _label_of(body)})
                plan.append({"type": "subject", "id": rid})
            else:
                video.append({"ref": "<%s %d>" % (kind, rid), "kind": kind.lower(),
                              "id": rid, "body": body})
                plan.append({"type": "video", "kind": kind, "id": rid})
            continue
        im = _IMAGE_REF_RE.match(s)
        if im:
            rid = int(im.group(1))
            references.append({"ref": "@图片%d" % rid, "id": rid,
                               "body": im.group(2).strip()})
            plan.append({"type": "reference", "id": rid})
            continue
        extras.append(line)
        plan.append({"type": "extra", "text": line})
    return {"plan": plan, "lines": lines, "video": video, "subjects": subjects,
            "extras": extras, "references": references}


def _parse_summary(content: str) -> dict:
    lines = content.split("\n") if content else []
    first = lines[0].strip() if lines else ""
    m = _SUMMARY_TAG_RE.match(first)
    mode_tag = m.group(1).strip() if m else ""
    text = (m.group(2) if m else first).strip()
    extra = [ln.strip() for ln in lines[1:] if ln.strip()]
    if extra:                       # 多行摘要（产线少见）：正文并入 text，逐字留 lines
        text = " ".join([text] + extra).strip()
    return {"lines": lines, "mode_tag": mode_tag, "text": text,
            "duration_s": duration_from_summary(content)}


def _parse_retention(content: str) -> dict:
    entries: list = []
    for line in (content.split("\n") if content else []):
        s = line.strip()
        if not s:
            continue
        m = _RETENTION_RE.match(s)
        if m:
            entries.append({
                "raw": line,
                "parsed": True,
                "ref": m.group(1).strip(),
                "paren": (m.group(2) or "").strip(),
                "status": m.group(3),
                "note": m.group(4),
                "appears_in": [int(x) for x in
                               re.findall(r"\[Shot\s+(\d+)\]", m.group(2) or "")],
            })
        else:
            # 解析不了的行逐字留：宁可原样回放，也不静默改写产线稿
            entries.append({"raw": line, "parsed": False, "ref": "",
                            "paren": "", "status": "", "note": s, "appears_in": []})
    return {"entries": entries}


def _parse_description(content: str) -> dict:
    """detailed_description → 段首行 / 镜头 / 【项目影像规格】行。

    `[Shot N]` 之后到下一个镜头（或项目规格块）之间的行，都算该镜头的续行。
    真稿里一镜一行，续行是防御性的：多行镜头也要能逐字回放。
    """
    lines = content.split("\n") if content else []
    intro: list = []
    shots: list = []
    spec: list = []
    state = "intro"
    for line in lines:
        s = line.strip()
        if _SPEC_RE.match(s):
            state = "spec"
            spec.append(line)
            continue
        if state == "spec":
            spec.append(line)
            continue
        m = _SHOT_LINE_RE.match(s)
        if m:
            rest = m.group(2)
            t_start = None
            ts_raw = ""
            tm = _AT_HEAD_RE.match(rest)
            if tm:
                t_start = (int(tm.group(1)) * 60 + int(tm.group(2))
                           + int(tm.group(3)) / 1000.0)
                ts_raw = rest[:tm.end()].strip()
                rest = rest[tm.end():]
            shots.append({"index": int(m.group(1)), "t_start": t_start,
                          "ts_raw": ts_raw, "body": rest.strip(),
                          "extra_lines": []})
            state = "shots"
            continue
        if state == "shots" and shots:
            shots[-1]["extra_lines"].append(line)
        else:
            intro.append(line)
    return {"lines": lines, "intro": intro, "shots": shots, "spec": spec}


def _dialogue_of(body: str) -> list:
    """一镜正文里的 `<d>` 台词 → 结构化台账（说话人 + 语言 + 文本 + 主体）。

    `(Sx)` 在 `<d>` 之前；主体名是最近一次出现的 `<Subject N>`。两者都可能缺，
    缺了就留空/留 S1 —— 台账的用途是让再编译与 M6 式比对有东西可对，
    不是替原文补它没写的东西。
    """
    out: list = []
    for m in h3.D_RE.finditer(body or ""):
        prefix = (body or "")[:m.start()]
        sp = h3.SPEAKER_RE.search(prefix[-120:]) if prefix else None
        subs = re.findall(r"<(?:Subject)\s+(\d+)>", prefix)
        out.append({"speaker": (sp.group(1).replace(" ", "") if sp else "S1"),
                    "lang": (m.group(1) or "zh"),
                    "text": m.group(2),
                    "subject_ref": ("<Subject %s>" % subs[-1]) if subs else ""})
    return out


def parse(text: str) -> dict:
    """六段文本 → IR dict。认不出的行**原样留**，不猜、不丢。

    返回的是 IR dict（与 `h3.parse` 同风格），合理路径是
    `IR.from_dict(h3_six.parse(text))`。
    """
    preamble, sections, order = split_sections(text)

    definitions = _parse_definitions(sections.get("subject_definitions", ""))
    summary = _parse_summary(sections.get("summary", ""))
    retention = _parse_retention(sections.get("retention_analysis", ""))
    description = _parse_description(sections.get("detailed_description", ""))
    soundscape = sections.get("overall_soundscape", "")
    music = sections.get("non_diegetic_music", "")
    duration = summary.get("duration_s")

    # 参考标签 → IR 实体。kind 按主体定义的开头几个字猜：人物 → character，
    # 其余（车厢/道具/场景）→ object。只用于 IR 视角的粗分类，不参与渲染。
    entities: list = []
    for s in definitions["subjects"]:
        eid = "s%d" % s["id"]
        label = s["label"]
        person = bool(re.search(
            r"男性|女性|叙述者|人物|男子|女子|男人|女人|少年|老人|孩子",
            s["body"][:80]))
        entities.append({
            "id": eid,
            "kind": "character" if person else "object",
            "label": label,
            "importance": round(max(0.5, 0.9 - 0.05 * (s["id"] - 1)), 2),
            "descriptors": {"six_section_ref": s["ref"],
                            "six_section_body": s["body"]},
        })
    by_ref = {e["descriptors"]["six_section_ref"]: e["id"] for e in entities}

    # 镜头台账：只收结构上能确定的字段。
    shots: list = []
    for s in description["shots"]:
        shot = {"index": s["index"], "action": s["body"], "action_kind": "clause"}
        if s["t_start"] is not None:
            shot["t_start"] = s["t_start"]
        dialogue = []
        for d in _dialogue_of(s["body"]):
            d2 = dict(d)
            d2["subject"] = by_ref.get(d.pop("subject_ref", ""), "")
            dialogue.append(d2)
        if dialogue:
            shot["dialogue"] = dialogue
        shots.append(shot)

    mode = "t2va"
    payload = {
        "format": "h3-six-section",
        "order": order,
        "preamble": preamble,
        "definitions": definitions,
        "summary": summary,
        "retention": retention,
        "detailed_description": description,
        "overall_soundscape": {"lines": soundscape.split("\n") if soundscape else []},
        "non_diegetic_music": {"lines": music.split("\n") if music else []},
        "duration_s": duration,
    }

    tl = {
        "duration_s": duration if duration is not None else 8.0,
        "mode": mode,
        "style": "",
        "one_take": len(shots) <= 1,
        "shots": shots,
        "soundscape": [soundscape.strip()] if soundscape.strip() else [],
        "music": ([] if music.strip().rstrip("。.！! ").upper() in ("N/A", "NA")
                  else [music.strip()] if music.strip() else []),
        "six_section": payload,
    }
    return {
        "ir_version": "0.1",
        "task": mode,
        "canvas": {"aspect_ratio": "16:9"},
        "entities": entities,
        "timeline": tl,
        "provenance": {"source": "h3-six-section-parse",
                       "preamble": preamble[:400]},
    }


# ─────────────────────────────────────────────────────────────
# 渲染：有骨架逐字回放，没有骨架从 IR 合成
# ─────────────────────────────────────────────────────────────

def _payload_of(ir: IR) -> dict | None:
    tl = getattr(ir, "timeline", None) or {}
    payload = tl.get("six_section")
    if isinstance(payload, dict) and payload.get("format") == "h3-six-section":
        return payload
    return None


def _render_definitions(sec: dict) -> str:
    body_by_subject = {s["id"]: s["body"] for s in sec.get("subjects") or []}
    body_by_video = {(v["kind"], v["id"]): v["body"] for v in sec.get("video") or []}
    body_by_ref = {r["id"]: r["body"] for r in sec.get("references") or []}
    lines: list = []
    for item in sec.get("plan") or []:
        kind = item.get("type")
        if kind == "blank":
            lines.append("")
        elif kind == "subject":
            rid = item["id"]
            body = body_by_subject.get(rid, "")
            lines.append(("<Subject %d> %s" % (rid, body)) if body
                         else "<Subject %d>" % rid)
        elif kind == "video":
            rid, vkind = item["id"], item["kind"]
            body = body_by_video.get((vkind.lower(), rid), "")
            lines.append(("<%s %d> %s" % (vkind, rid, body)) if body
                         else "<%s %d>" % (vkind, rid))
        elif kind == "reference":
            rid = item["id"]
            body = body_by_ref.get(rid, "")
            lines.append(("@图片%d %s" % (rid, body)) if body else "@图片%d" % rid)
        else:
            lines.append(str(item.get("text") or ""))
    return "\n".join(lines)


def _render_summary(sec: dict) -> str:
    lines = sec.get("lines") or []
    tag = str(sec.get("mode_tag") or "")
    text = str(sec.get("text") or "")
    if len(lines) <= 1:
        if tag:
            return "[%s] %s" % (tag, text) if text else "[%s]" % tag
        return text
    return "\n".join(lines)


def _render_retention(sec: dict) -> str:
    lines: list = []
    for e in sec.get("entries") or []:
        if not e.get("parsed"):
            lines.append(str(e.get("raw") or e.get("note") or ""))
            continue
        head = e["ref"]
        if e.get("paren"):
            head += " " + e["paren"]
        lines.append("%s: %s - %s" % (head, e["status"], e["note"]))
    return "\n".join(lines)


def _render_description(sec: dict) -> str:
    lines: list = list(sec.get("intro") or [])
    for s in sec.get("shots") or []:
        head = "[Shot %d]" % int(s["index"])
        if s.get("ts_raw"):
            head += " " + s["ts_raw"]
        if s.get("body"):
            head += " " + s["body"]
        lines.append(head)
        lines.extend(s.get("extra_lines") or [])
    lines.extend(sec.get("spec") or [])
    return "\n".join(lines)


def _render_lines(sec: dict) -> str:
    return "\n".join(sec.get("lines") or [])


def render_payload(payload: dict) -> str:
    """结构化骨架 → 六段文本。**按规范六段序输出**（段序错的稿在这里被拉正）。"""
    present = set(payload.get("order") or [])
    blocks: list = []
    for name in FIELDS:
        if name not in present:
            continue
        if name == "subject_definitions":
            body = _render_definitions(payload.get("definitions") or {})
        elif name == "summary":
            body = _render_summary(payload.get("summary") or {})
        elif name == "retention_analysis":
            body = _render_retention(payload.get("retention") or {})
        elif name == "detailed_description":
            body = _render_description(payload.get("detailed_description") or {})
        elif name == "overall_soundscape":
            body = _render_lines(payload.get("overall_soundscape") or {})
        else:
            body = _render_lines(payload.get("non_diegetic_music") or {})
        blocks.append(name + ":" + "\n" + body)
    return "\n\n".join(blocks) + ("\n" if blocks else "")


# ── 从 IR 合成六段（没有六段骨架时的路径） ─────────────────────

def _subjects_of(ir: IR) -> tuple:
    """IR 实体 → 六段式主体表（`<Subject N>` 从 1 连号，按 IR 的实体顺序）。

    IR 的实体顺序已经按 importance 排过（`IR.normalize`），不再另排：
    同一份 IR 换谁写都该得到同一份主体表。
    """
    subjects: list = []
    for e in ir.entities:
        if e.kind not in ("character", "object") or not e.label:
            continue
        subjects.append({"id": len(subjects) + 1, "entity": e})
    return subjects


def _subject_body(e) -> str:
    from .clauses import describe_entity

    return describe_entity(e, with_anchor=False, extras=True)


def build_payload(ir: IR) -> dict:
    """从 IR 的 entities + timeline 合成一份六段骨架。**不依赖任何解析过的原稿。**

    合成是**占位级**的，不是产线稿：主体定义用 `describe_entity` 的属性罗列，
    段首说明/项目影像规格块不生成（那是项目规范，通用 IR 里没有对应事实），
    声景/配乐缺省写 N/A 并让 `check` 报 warn。它的用途是「一份任意 IR 也要能
    进六段式通道」，不是替人写稿。
    """
    tl = getattr(ir, "timeline", None) or {}
    duration = float(tl.get("duration_s") or 8.0)
    mode = str(tl.get("mode") or "t2va").lower()
    continuation = mode in ("i2va", "fl2va", "l2va")
    takes = h3.takes(ir)
    subjects = _subjects_of(ir)
    ref_of = {s["entity"].id: "<Subject %d>" % s["id"] for s in subjects}
    appears: dict = {ref: [] for ref in ref_of.values()}
    for t in takes:
        for eid in t.entities:
            ref = ref_of.get(eid)
            if ref and t.index not in appears[ref]:
                appears[ref].append(t.index)

    # 主体定义：<Video N>（续接模式才有）+ <Subject N> 行
    plan: list = []
    video: list = []
    definitions_subjects: list = []
    if continuation:
        video.append({"ref": "<Video 1>", "kind": "video", "id": 1,
                      "body": _VIDEO_CONTINUATION_LINE})
        plan.append({"type": "video", "kind": "Video", "id": 1})
    for s in subjects:
        body = _subject_body(s["entity"])
        definitions_subjects.append({"ref": "<Subject %d>" % s["id"], "id": s["id"],
                                     "body": body, "label": s["entity"].label})
        plan.append({"type": "subject", "id": s["id"]})
    definitions = {"plan": plan, "video": video,
                   "subjects": definitions_subjects, "references": [],
                   "extras": [], "lines": []}

    # 摘要：模式标记从 timeline.mode 推；时长用 IR 的 duration_s
    n_shots = len(takes)
    dur_txt = ("%g" % duration)
    scope = "续接" if continuation else "参考生成"
    mode_tag = "video continuation" if continuation else "reference generation"
    summary = {"mode_tag": mode_tag,
               "text": "%s秒%s段落，%d镜。" % (dur_txt, scope, n_shots),
               "lines": [],
               "duration_s": duration}

    # 保留分析：每个主体列它在哪些镜头里出现
    entries: list = []
    if continuation:
        entries.append({
            "raw": "", "parsed": True, "ref": "<Video 1>",
            "paren": "(continuation source / previous last frame)",
            "status": "fully_preserved",
            "note": "上一段末帧的取景、机位方向与高度、光向与色温、角色站位从上一段"
                    "末帧完全保留，本段首帧与其像素级承接。",
            "appears_in": [],
        })
    for s in subjects:
        ref = "<Subject %d>" % s["id"]
        shots_in = appears.get(ref) or []
        paren = ("(appears in %s)" % ", ".join("[Shot %d]" % i for i in shots_in)
                 if shots_in else "(appears in )")
        entries.append({"raw": "", "parsed": True, "ref": ref, "paren": paren,
                        "status": "fully_preserved",
                        "note": "按本段 subject_definitions 逐项保持。",
                        "appears_in": shots_in})
    retention = {"entries": entries}

    # 详细描述：段首一行 + 每镜一行
    style = str(tl.get("style") or ir.style.get("medium") or
                ir.style.get("look") or "").strip()
    intro = ["本段为 %d 镜段落%s。" % (n_shots, ("，风格：" + style) if style else "")]
    shots: list = []
    for t in takes:
        parts: list = []
        if t.framing:
            parts.append(t.framing)
        if t.action:
            parts.append(t.action.strip().rstrip("."))
        if t.detail:
            parts.append(t.detail.strip().rstrip("."))
        dialogue: list = []
        for d in t.dialogue:
            text = str(d.get("text") or "").strip()
            if not text:
                continue
            who = str(d.get("speaker") or "S1").strip() or "S1"
            lang = str(d.get("lang") or "zh").strip() or "zh"
            ref = ref_of.get(str(d.get("subject") or ""), "")
            head = ("%s (%s) 说" % (ref, who)) if ref else ("(%s) 说" % who)
            dialogue.append("%s <d>[%s]%s</d>。" % (head, lang, text))
        body = " ".join(parts).strip()
        for s in t.onscreen_text:
            text = str(s.get("text") or "").strip()
            if text:
                body += ("；" if body else "") + "画面文字「%s」" % text
        # 运镜词表用 h3 那一份，不在这里复制第二份（复制就会分叉）
        cam = h3._camera_sentence(t.camera)
        if cam:
            body += (" " if body else "") + cam.rstrip(".")
        if body and not body.endswith(("。", "！", "？")):
            body += "。"
        if dialogue:
            body = (body + " " if body else "") + "".join(dialogue)
        ts_raw = ""
        if t.index > 1 and t.t_start is not None:
            ts_raw = "At %s," % h3.format_ts(t.t_start)
        shots.append({"index": t.index, "t_start": t.t_start, "ts_raw": ts_raw,
                      "body": body, "extra_lines": []})
    description = {"lines": [], "intro": intro, "shots": shots, "spec": []}

    soundscape = " ".join(x.strip() for x in (tl.get("soundscape") or [])
                          if str(x).strip()) or "N/A"
    music = " ".join(x.strip() for x in (tl.get("music") or [])
                     if str(x).strip()) or "N/A"
    return {
        "format": "h3-six-section",
        "order": list(FIELDS),
        "preamble": "",
        "definitions": definitions,
        "summary": summary,
        "retention": retention,
        "detailed_description": description,
        "overall_soundscape": {"lines": [soundscape]},
        "non_diegetic_music": {"lines": [music]},
        "duration_s": duration,
    }


def synthesize(ir: IR) -> str:
    """IR → 六段文本。**总是从结构化字段重建**，不读骨架。"""
    return render_payload(build_payload(ir))


def render(ir: IR, *, replay: bool = True) -> str:
    """IR → H3 六段式终稿。确定性，零模型。

    `replay=True`（默认）：IR 带着从真稿 parse 出来的六段骨架时逐字回放。
    `replay=False`：忽略骨架，从 entities/timeline 重新合成（改过 IR 之后用这条）。
    """
    if replay:
        payload = _payload_of(ir)
        if payload is not None:
            return render_payload(payload)
    return synthesize(ir)


# ─────────────────────────────────────────────────────────────
# 判定
# ─────────────────────────────────────────────────────────────

#: 六段式独有的字段（与基础模式共享的只有 overall_soundscape / non_diegetic_music）。
_EXCLUSIVE_FIELDS = ("subject_definitions", "summary", "retention_analysis",
                     "detailed_description")


def looks_like(text: str) -> bool:
    """这段文本是不是六段式稿。

    判据比 `h3.looks_like` 多一条：**至少命中一个六段式独有字段**。因为六段式与
    三字段共享 `overall_soundscape` / `non_diegetic_music` 两个名字，只数命中数
    会把三字段终稿误判成六段式（实践里踩过）。
    """
    t = _strip_fence(text or "")
    hits = {m.group(1) for m in _SECTION_RE.finditer(t)}
    return len(hits) >= 2 and bool(hits & set(_EXCLUSIVE_FIELDS))


def _collect_defined(sections: dict) -> tuple:
    definitions = _parse_definitions(sections.get("subject_definitions", ""))
    subjects = {s["ref"] for s in definitions["subjects"]}
    video = {v["ref"] for v in definitions["video"]}
    return subjects, video


#: `IR.normalize()` 在时间线没写时长时补的结构兜底值。**它不是事实**：
#: `parse()` 出来的六段稿若 summary 没写时长，`timeline.duration_s` 就会是这个数；
#: `check()` 不能拿它判时间戳越界（否则同一份稿「传不传 ir」结论不同）。
_SENTINEL_DURATION_S = 8.0


def _ir_duration(ir: IR | None) -> float | None:
    """IR 里可以作为「时间戳越界」判据的时长；没有就返回 None（不猜）。

    优先级：六段骨架里登记过的时长 > 没有骨架时的 `timeline.duration_s`。

    有骨架（`parse()` 的产物）时以骨架为准：`six_section.duration_s` 为 `None`
    说明**原稿 summary 本来就没写时长**，此时 `timeline.duration_s` 的 8.0 只是
    `IR.normalize()` 的结构兜底，不是用户声明的事实；只有 IR 被显式改成别的值
    （用户手改 IR 给了真时长）才采用。这样同一份「无时长」稿，
    `check(text)` 与 `check(text, IR.from_dict(parse(text)))` 都是
    `H3S-DURATION-UNKNOWN` warn，而不是一个 warn、一个 `H3S-TS-RANGE` error。
    """
    tl = getattr(ir, "timeline", None) or {}
    try:
        ir_dur = float(tl.get("duration_s")) if tl.get("duration_s") is not None else None
    except (TypeError, ValueError):
        ir_dur = None
    payload = _payload_of(ir)
    if payload is not None:
        known = payload.get("duration_s")
        if known is not None:
            try:
                return float(known)
            except (TypeError, ValueError):
                return None
        if ir_dur is None or abs(ir_dur - _SENTINEL_DURATION_S) <= 1e-9:
            return None
        return ir_dur
    return ir_dur


def check(text: str, ir: IR | None = None, *, duration_s: float | None = None,
          music_policy: str = "any") -> list:
    """H3 六段式的机检闸。零模型。

    每条判据都守一个**可分别判**的错：缺段、段序错、时间戳越界各有独立错误码，
    不会互相顶替（测试里逐条钉住「只报它」）。`music_policy` 与三字段路径同义：
    `any`（默认）允许 `N/A` 或一段配乐描述；`na` 是「产出端不配乐」那条产线的
    严格要求，必须严格 `N/A`。
    """
    issues: list = []

    def err(code: str, msg: str, fix: str = "") -> None:
        issues.append(Issue("error", code, msg, fix))

    def warn(code: str, msg: str, fix: str = "") -> None:
        issues.append(Issue("warn", code, msg, fix))

    raw = text or ""
    if "```" in raw:
        err("H3S-FENCE", "prompt 里带 Markdown 代码围栏",
            "去掉 ``` 再提交；模型会把它们当正文")

    _preamble, sections, order = split_sections(raw)

    # ── 段完整性 / 顺序 ─────────────────────────────────────
    missing = [f for f in FIELDS if f not in sections]
    for name in missing:
        err("H3S-MISSING-SECTION", "缺段：%s" % name,
            "六段必须齐全且顺序固定：%s" % " → ".join(FIELDS))
    present = [f for f in FIELDS if f in sections]
    if order != present:
        err("H3S-SECTION-ORDER", "段序不是规范顺序（实际：%s）" % " → ".join(order),
            "顺序固定为 " + " → ".join(FIELDS))
    seen: set = set()
    for name in order:
        if name in seen:
            err("H3S-SECTION-DUP", "段重复：%s" % name, "每个段只出现一次")
        seen.add(name)
    for name in present:
        if not sections[name].strip():
            err("H3S-SECTION-EMPTY", "%s 段为空" % name, "填内容；空段模型读不出任何指令")

    # ── 主体定义与引用 ─────────────────────────────────────
    subjects_defined: set = set()
    video_defined: set = set()
    if "subject_definitions" in sections:
        subjects_defined, video_defined = _collect_defined(sections)

    # ── 段落时长：显式参数 > summary > IR ────────────────────
    duration = duration_s
    summary_duration = None
    if "summary" in sections:
        summary = _parse_summary(sections["summary"])
        summary_duration = summary.get("duration_s")
        if not summary.get("mode_tag"):
            warn("H3S-SUMMARY-TAG",
                 "summary 首行没有 [video continuation] / [reference generation] 标记",
                 "产线口径用方括号标记段落类型，便于人读与机判")
    if duration is None:
        duration = summary_duration
    if duration is None and ir is not None:
        # 不能直接用 timeline.duration_s：IR.normalize() 在没时长时会写 8.0 兜底，
        # 那是结构不是事实（见 _ir_duration）。
        duration = _ir_duration(ir)

    # ── 详细描述：镜头 / 时间戳 / 引用 / 台词 ────────────────
    if "detailed_description" in sections:
        desc = sections["detailed_description"]
        parsed = _parse_description(desc)
        shots = parsed["shots"]
        if not shots:
            err("H3S-NO-SHOTS", "detailed_description 里没有 [Shot N] 标记",
                "每镜以 [Shot N] 起行；这是六段式的时间轴")
        else:
            idxs = [s["index"] for s in shots]
            if idxs != list(range(1, len(idxs) + 1)):
                err("H3S-SHOT-SEQ", "[Shot N] 不是 1..N 连号（实际：%s）"
                    % "、".join(str(i) for i in idxs),
                    "镜头号是结构，必须从 1 起连号")
            if shots[0]["t_start"] is not None:
                warn("H3S-SHOT1-TS",
                     "Shot 1 带了时间戳 %s" % h3.format_ts(shots[0]["t_start"]),
                     "首镜不写时间戳；开场时间戳会把首帧锁成置景图（铁律 11）")

        stamps = _at_seconds(desc)
        for a, b in zip(stamps, stamps[1:], strict=False):
            if b <= a:
                err("H3S-TS-ORDER",
                    "At 时间戳没有严格递增：%s → %s"
                    % (h3.format_ts(a), h3.format_ts(b)),
                    "切点与动作节拍必须严格递增")
                break
        if stamps:
            if duration is None:
                warn("H3S-DURATION-UNKNOWN",
                     "解析不出段落时长（summary 里没有「N秒」；IR 的 duration_s 也不可用）",
                     "在 summary 里写「N秒续接段落」；否则时间戳越界无法判")
            else:
                over = [s for s in stamps if s >= float(duration) - 1e-9]
                if over:
                    err("H3S-TS-RANGE",
                        "时间戳 %s 越过段落时长 %.3fs"
                        % ("、".join(h3.format_ts(s) for s in over[:3]),
                           float(duration)),
                        "At 必须早于段落时长；时长以 summary 声明为准")

        if "subject_definitions" in sections:
            used = set(re.findall(r"<Subject\s+\d+>", desc))
            undefined = sorted(used - subjects_defined,
                               key=lambda r: int(re.search(r"\d+", r).group(0)))
            if undefined:
                err("H3S-REF-UNDEFINED",
                    "详细描述引用了未定义的主体：%s" % "、".join(undefined),
                    "先在 subject_definitions 里定义该 <Subject N>")

        for i, d in enumerate(h3.D_RE.finditer(desc), 1):
            if not d.group(1):
                err("H3S-D-LANG", "第 %d 个 <d> 缺语言标签" % i,
                    "写成 <d>[zh]台词</d>；语言标签是载荷前缀，不能省")

    # ── 保留分析引用 ───────────────────────────────────────
    if "retention_analysis" in sections and "subject_definitions" in sections:
        for e in _parse_retention(sections["retention_analysis"])["entries"]:
            ref = e.get("ref") or ""
            if ref.startswith("<Subject") and ref not in subjects_defined:
                err("H3S-RETENTION-REF", "保留分析引用了未定义的主体：%s" % ref,
                    "先在 subject_definitions 里定义它")
            elif ref.startswith("<Video") and ref not in video_defined:
                err("H3S-RETENTION-REF", "保留分析引用了未定义的视频源：%s" % ref,
                    "续接段要先在 subject_definitions 里写 <Video N>")

    # ── 配乐 / 声景取值 ────────────────────────────────────
    if "non_diegetic_music" in sections:
        music = sections["non_diegetic_music"].strip()
        bare = music.rstrip("。.！! ")
        if not music:
            err("H3S-MUSIC", "non_diegetic_music 为空",
                "无画外配乐写 N/A；有就写一两句配乐描述")
        elif bare.upper() in ("N/A", "NA"):
            pass
        elif music_policy == "na":
            err("H3S-MUSIC",
                "non_diegetic_music = %r；`music_policy=na` 要求严格写 N/A"
                % music[:24],
                "改成 N/A；人物能听见的音乐属画内声，写进声景")
        elif len(re.findall(r"[\u3400-\u9fffA-Za-z]{2,}", music)) < 1:
            err("H3S-MUSIC",
                "non_diegetic_music = %r 既不是 N/A，也不是配乐描述" % music[:24],
                "无画外配乐写 N/A；有就写一两句配乐描述")
    if "overall_soundscape" in sections:
        sound = sections["overall_soundscape"].strip()
        if sound and sound.rstrip("。.").upper() in ("N/A", "NA"):
            warn("H3S-SOUNDSCAPE-NA", "overall_soundscape 是 N/A",
                 "只有全片静音才允许 N/A")

    return issues


def verdict(issues: list) -> str:
    if any(i.level == "error" for i in issues):
        return "fail"
    if any(i.level == "warn" for i in issues):
        return "warn"
    return "pass"


def report(issues: list) -> str:
    """给人看的一段。空清单也要有话说——「没报错」不等于「检查过」。"""
    if not issues:
        return ("0 error / 0 warn（只覆盖可机检子集：六段齐序 / 参考标签 / "
                "镜头时间戳 / 台词边界 / 配乐取值）")
    lines: list = []
    for i in issues:
        lines.append("%s %s %s" % ("ERROR" if i.level == "error" else "WARN ",
                                   i.code, i.message))
        if i.fix:
            lines.append("      → " + i.fix)
    return "\n".join(lines)


__all__ = ["FIELDS", "parse", "render", "synthesize", "build_payload",
           "render_payload", "split_sections", "duration_from_summary",
           "looks_like", "check", "verdict", "report"]
