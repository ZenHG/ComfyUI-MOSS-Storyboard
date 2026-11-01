# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""G7 · 双盲判读协议工具（packet / merge / calibrate）。

三段流水线（全部离线、零第三方依赖、确定性）：

1. ``packet``   从模型产出（``out/g7/json/<model>/<img>.json``）生成双盲判读包：
                逐图 markdown（图路径 + caption + IR 实体表 + **候选事实清单**）+
                空白模板；同时生成双盲变体 A/B 与**人类判读包**。
                判读包里**不含任何机检结论**（binding/覆盖率/pass-fail 一律不进包）——
                否则「判读者」只是在给机检背书。
2. ``merge``    合并多名判读者结果：Cohen's κ（两名）/ 两两 κ 最小值（>2 名）、
                分歧清单、第三名仲裁、事实准确率 + Wilson CI + Fisher + 符号检验 +
                幻觉 McNemar，最后按协议落**三档结论**之一：
                ``显著差`` / ``无显著差异`` / ``样本不足``。
                κ < 门槛（默认 0.6）时结论只能是「样本不足」，且进程返回非零（闸会红）。
3. ``calibrate`` 自动分 vs 判读排序的 Spearman ρ 与翻转率；ρ < 0.5 直接打印
                「该分不应进 README」，进程返回非零。分数再好听，排不对人就被删。

三条纪律写死在代码里，不靠人记：

- AI 判读者的结果文件必须带 ``ai_label: "AI 判读，不是人类"``，否则 merge 拒绝；
- 结论**只允许**三档（``CONCLUSION_TIERS``），本工具的任何输出都不出现「更好」这类话；
- 判读包与机检产物解耦：packet 只读 caption/IR，机检文件里的数字进不了包。

运行产物落 ``out/g7/adjudication/``（out/ 不入库）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "g7"
DEFAULT_OUT = OUT / "adjudication"

AI_LABEL = "AI 判读，不是人类"
VERDICTS = ("correct", "wrong", "unverifiable")
HALLUCINATION = ("present", "absent")
CONCLUSION_TIERS = ("显著差", "无显著差异", "样本不足")
PACKET_SCHEMA = "g7-adjudication-packet/1"
JUDGE_SCHEMA = "g7-adjudication/1"
MERGED_SCHEMA = "g7-adjudication-merged/1"
CALIB_SCHEMA = "g7-adjudication-calibration/1"

KAPPA_THRESHOLD = 0.6
RHO_THRESHOLD = 0.5
MIN_N_DEFAULT = 20

#: 维度 → 中文类别名（与 docs/G7 的判读单元表一致）
CATEGORIES = {
    "identity": "身份", "age": "年龄", "hair": "发型", "skin": "皮肤",
    "face": "面部", "expression": "表情", "pose": "姿态", "wardrobe": "服装",
    "props": "道具", "color": "颜色", "material": "材质", "spatial": "空间",
    "text": "图内文字", "medium": "媒介", "lighting": "光", "environment": "环境",
}

_KIND_CN = {"character": "人物", "object": "物体", "text": "图内文字",
            "background": "背景", "panel": "画格", "effect": "效果"}

#: 事实抽取的字段优先级：先身份，再逐字段轮转（每图每模型 3–9 条）。
_FIELD_SPECS = (("age", "age"), ("text", "text"), ("color", "color"),
                ("material", "material"), ("wardrobe", "wardrobe"),
                ("spatial", "spatial"), ("pose", "pose"), ("expression", "expression"),
                ("props", "props"), ("hair", "hair"))


# ─────────────────────────────────────────────────────────────
# 通用小工具
# ─────────────────────────────────────────────────────────────

def _load_json(path, what: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise SystemExit("找不到%s：%s" % (what, p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit("%s 不是合法 JSON：%s（%s）" % (what, p, exc)) from exc


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _norm_verdict(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    return s if s in VERDICTS else None


def _norm_hallucination(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("present", "yes", "true", "有", "存在"):
        return "present"
    if s in ("absent", "no", "false", "无", "不存在"):
        return "absent"
    return None


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    """Wilson 95% CI（返回百分数区间或 None）。"""
    if not n:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round((centre - half) * 100, 1), round((centre + half) * 100, 1)]


def fisher_p(a: int, b: int, c: int, d: int) -> float:
    """2×2 双侧 Fisher 精确 p。a/b = 模型 A 的对/错，c/d = 模型 B 的对/错。"""
    n = a + b + c + d
    if not n:
        return 1.0
    r1, r2, c1 = a + b, c + d, a + c
    lo, hi = max(0, c1 - r2), min(r1, c1)

    def hyp(x: int) -> float:
        return math.comb(r1, x) * math.comb(r2, c1 - x) / math.comb(n, c1)

    p0 = hyp(a)
    return min(1.0, sum(hyp(x) for x in range(lo, hi + 1) if hyp(x) <= p0 + 1e-12))


def sign_test_p(wins: int, losses: int) -> float:
    """逐图配对符号检验（双侧精确二项；平局剔除）。"""
    n = wins + losses
    if not n:
        return 1.0
    k = max(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def mcnemar_p(b: int, c: int) -> float:
    """McNemar 精确检验（b/c 为两个方向的翻转数）。"""
    n = b + c
    if not n:
        return 1.0
    k = max(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def cohens_kappa(pairs, categories) -> dict:
    """Cohen's κ。pairs = [(rater1, rater2), ...]；categories 为类别全集。"""
    pairs = [(a, b) for a, b in pairs if a in categories and b in categories]
    n = len(pairs)
    if not n:
        return {"n": 0, "kappa": None, "observed_agreement": None, "expected_agreement": None}
    cats = list(categories)
    mat = {c: dict.fromkeys(cats, 0) for c in cats}
    for a, b in pairs:
        mat[a][b] += 1
    observed = sum(mat[c][c] for c in cats) / n
    marg_a = {c: sum(mat[c].values()) / n for c in cats}
    marg_b = {c: sum(mat[r][c] for r in cats) / n for c in cats}
    expected = sum(marg_a[c] * marg_b[c] for c in cats)
    if expected >= 1.0:
        kappa = 1.0 if observed >= 1.0 else 0.0
    else:
        kappa = (observed - expected) / (1 - expected)
    return {"n": n, "kappa": round(kappa, 4),
            "observed_agreement": round(observed, 4),
            "expected_agreement": round(expected, 4)}


def _ranks(values):
    """平均秩（并列取平均）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_rho(xs, ys):
    """Spearman ρ（并列用平均秩）。n<3 或方差为 0 返回 None。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if not dx or not dy:
        return None
    return round(num / (dx * dy), 4)


def _rate(num: int, den: int):
    return round(num / den, 4) if den else None


# ─────────────────────────────────────────────────────────────
# 事实抽取：从 IR 生成「候选可核事实」
# ─────────────────────────────────────────────────────────────

def _field_value(entity: dict, field: str):
    d = entity.get("descriptors") or {}
    if field == "age":
        return entity.get("age_years")
    if field == "text":
        return entity.get("text_content")
    if field == "spatial":
        return (entity.get("spatial") or {}).get("anchor")
    if field == "color":
        return d.get("color") or (entity.get("typography") or {}).get("color")
    return d.get(field)


def _claim_text(entity: dict, field: str, value) -> str:
    eid = entity.get("id")
    label = (entity.get("label") or "").strip()
    who = "%s%s" % (eid, "（%s）" % label if label else "")
    if field == "identity":
        kind = _KIND_CN.get(entity.get("kind") or "", entity.get("kind") or "实体")
        return "画面中存在%s（kind=%s，label=%s）" % (kind, entity.get("kind"), label or "(空)")
    if field == "age":
        return "实体 %s 的年龄为 %s 岁" % (who, value)
    if field == "text":
        return "图内文字「%s」的逐字内容正确" % str(value).replace("\n", "／")
    if field == "spatial":
        return "实体 %s 位于 %s" % (who, value)
    if field == "wardrobe":
        return "实体 %s 的服装为 %s" % (who, json.dumps(value, ensure_ascii=False))
    if field == "props":
        return "实体 %s 的道具为 %s" % (who, json.dumps(value, ensure_ascii=False))
    names = {"color": "颜色", "material": "材质", "pose": "姿态",
             "expression": "表情", "hair": "发型"}
    return "实体 %s 的%s为 %s" % (who, names.get(field, field), value)


def extract_claims(model: str, image_id: str, payload: dict,
                   max_claims: int = 9) -> list:
    """一份模型产出 → 候选事实清单（确定性；每图每模型 3–9 条）。

    只读 IR（caption 由 IR 渲染，判读时要判的是「图与断言是否相符」）。
    机检结论不参与——这是双盲协议的前提。
    """
    ir = payload.get("ir") or {}
    entities = [e for e in (ir.get("entities") or []) if e.get("id")]
    claims = []

    def add(entity, field, value) -> None:
        cid = "%s|%s|%s.%s" % (model, image_id, entity.get("id"), field)
        claims.append({"id": cid, "entity_id": entity.get("id"), "field": field,
                       "category": CATEGORIES.get(field, field),
                       "text": _claim_text(entity, field, value)})

    # 第一轮：身份（最多前 6 个实体；每图每模型 3–9 条的「3」从这里起步）
    for entity in entities[:6]:
        add(entity, "identity", None)

    # 第二轮：字段轮转（先每个实体给一个字段，避免一条长描述吃满配额）
    for field, _cat in _FIELD_SPECS:
        for entity in entities:
            if len(claims) >= max_claims:
                break
            value = _field_value(entity, field)
            if value in (None, "", [], {}):
                continue
            add(entity, field, value)
        if len(claims) >= max_claims:
            break

    # 图像级：媒介 / 主光（空间还有配额时才加，避免盖过实体事实）
    style = ir.get("style") or {}
    if len(claims) < max_claims and style.get("medium"):
        claims.append({"id": "%s|%s|_image.medium" % (model, image_id),
                       "entity_id": "_image", "field": "medium", "category": "媒介",
                       "text": "画面媒介为 %s" % style["medium"]})
    lighting = ir.get("lighting") or {}
    if len(claims) < max_claims and lighting.get("key"):
        claims.append({"id": "%s|%s|_image.lighting" % (model, image_id),
                       "entity_id": "_image", "field": "lighting", "category": "光",
                       "text": "画面主光为 %s" % lighting["key"]})
    return claims[:max_claims]


# ─────────────────────────────────────────────────────────────
# packet：判读包（双盲变体 + 人类包）
# ─────────────────────────────────────────────────────────────

def _entity_table(ir: dict) -> str:
    rows = ["| id | kind | label | age | anchor | 描述字段 | text_content |",
            "|---|---|---|---|---|---|---|"]
    for e in ir.get("entities") or []:
        d = e.get("descriptors") or {}
        desc = []
        for k in ("gender", "hair", "skin", "face", "wardrobe", "props", "color",
                  "material", "build", "detail", "tags", "expression", "pose"):
            if d.get(k):
                desc.append("%s=%s" % (k, json.dumps(d[k], ensure_ascii=False)))
        typ = e.get("typography") or {}
        if typ:
            desc.append("typography=%s" % json.dumps(typ, ensure_ascii=False))
        sp = e.get("spatial") or {}
        rows.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            e.get("id"), e.get("kind"), e.get("label") or "",
            e.get("age_years") if e.get("age_years") is not None else "",
            sp.get("anchor") or "", "；".join(desc)[:800],
            json.dumps(e.get("text_content"), ensure_ascii=False)
            if e.get("text_content") else ""))
    return "\n".join(rows)


def _packet_md(unit: dict, variant: str, judge_kind: str,
               judge_name: str = "") -> str:
    """一页判读包（judge_kind = ai | human）。"""
    judge_line = ("- 判读者：**%s**" % AI_LABEL if judge_kind == "ai"
                  else "- 判读者：**人类**（本包供人填写；不含 AI 判读结果）")
    if judge_name:
        judge_line += "（判读者代号：`%s`）" % judge_name
    lines = [
        "# G7 判读包 · %s · %s" % (unit["image_id"], unit["model"]),
        "",
        "- 图片：`%s`（%s）" % (unit.get("image_path") or "(未提供)", unit.get("category") or "未分类"),
        "- 图片 sha256：`%s`" % (unit.get("image_sha256") or "(未提供)"),
        "- caption sha256：`%s`" % unit["caption_sha256"],
        "- IR sha256：`%s`" % unit["ir_sha256"],
        "- 变体：%s（事实顺序%s；claim id 与原包一致，按 id 对齐）"
        % (variant, "与 A 相反" if variant == "variant_b" else "原始"),
        judge_line,
        "- 纪律：本包**不含机检结论**（不得查看 binding.json / 机检表）；"
        "两名判读者互不可见；分歧交第三名仲裁。",
        "",
        "## 判读规则",
        "",
        "- `correct`：图中可核，且与断言一致；",
        "- `wrong`：图中可核，但与断言相反/错误（含凭空编出、把不可辨内容猜成具体内容）；",
        "- `unverifiable`：图中无法判断（如焦距、被遮挡、不可辨文字）；**不计入准确率分母**。",
        "- 整图幻觉 `present/absent`：是否存在图中看不到/无法支持的内容（漏记不算幻觉）。",
        "",
        "## 候选事实（在 JSON 模板里逐条填 verdict）",
        "",
        "| claim_id | 类别 | 断言 | verdict |",
        "|---|---|---|---|",
    ]
    for c in unit["claims"]:
        lines.append("| `%s` | %s | %s | 待填 |" % (c["id"], c["category"], c["text"]))
    if not unit["claims"]:
        lines.append("| — | — | 本图可用事实不足 3 条（模型 IR 过简），按实际能核的填 | 待填 |")
    lines += [
        "",
        "## 整图幻觉（在 JSON 模板里填 present / absent）",
        "",
        "## caption（成品散文，供对照）",
        "",
        "```text",
        unit.get("caption") or "(记录失败，无 caption)",
        "```",
        "",
        "## IR 实体表",
        "",
        unit.get("entity_table") or "(无实体)",
        "",
        "## 填写位置",
        "",
        "同目录/同名 JSON 模板（`templates/` 下，按 claim_id 对齐）：逐条填 "
        "correct / wrong / unverifiable；整图幻觉填 present / absent。",
        "",
    ]
    return "\n".join(lines)


def _blank_judge(judge_id: str, judge_type: str, variant: str, units: list,
                 reverse: bool = False) -> dict:
    def claims_of(unit):
        items = list(unit["claims"])
        if reverse:
            items = list(reversed(items))
        return {c["id"]: None for c in items}

    return {
        "schema": JUDGE_SCHEMA,
        "judge_id": judge_id,
        "judge_type": judge_type,
        "ai_label": AI_LABEL if judge_type == "ai" else "",
        "variant": variant,
        "units": [{
            "model": u["model"], "image_id": u["image_id"],
            "claims": claims_of(u),
            "hallucination": None, "notes": "",
        } for u in units],
    }


def _unit_from_payload(model: str, image_id: str, payload: dict,
                       image_meta: dict, claims: list) -> dict:
    ir = payload.get("ir") or {}
    image_sha = (payload.get("image_sha256")
                 or (image_meta or {}).get("sha256") or "")
    cap = payload.get("caption") or ""
    return {
        "model": model,
        "image_id": image_id,
        "category": (image_meta or {}).get("category") or payload.get("category") or "",
        "image_path": (image_meta or {}).get("path") or payload.get("image_path") or "",
        "image_sha256": image_sha,
        "caption": cap,
        "caption_sha256": _sha256(cap),
        "ir_sha256": _sha256(_canon(ir)),
        "claims": claims,
        "claims_total": len(claims),
        "entity_table": _entity_table(ir),
    }


def cmd_packet(args) -> int:
    json_dir = Path(args.json_dir)
    if not json_dir.is_dir():
        raise SystemExit("找不到模型产目录：%s（先跑 g7_caption）" % json_dir)
    images = {}
    if args.images and Path(args.images).is_file():
        data = _load_json(args.images, "图片清单")
        images = {i["id"]: i for i in (data.get("images") or [])}
    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else \
        sorted(p.name for p in json_dir.iterdir() if p.is_dir())
    ids = {s.strip() for s in args.ids.split(",") if s.strip()} if args.ids else None

    units, failed = [], []
    for model in models:
        for p in sorted((json_dir / model).glob("*.json")):
            image_id = p.stem
            if ids and image_id not in ids:
                continue
            payload = _load_json(p, "模型产出")
            if not payload.get("ok"):
                failed.append({"model": model, "image_id": image_id,
                               "error": str(payload.get("error") or "")[:160]})
                continue
            claims = extract_claims(model, image_id, payload, args.max_claims)
            units.append(_unit_from_payload(model, image_id, payload,
                                            images.get(image_id), claims))
    if not units:
        raise SystemExit("没有可判读单元（json_dir=%s，models=%s）" % (json_dir, models))

    dest = Path(args.out)
    # 双盲分派：--split judge_a,judge_b；A 保留原顺序，B 逆序（claim id 相同，防顺序锚定）
    split = [s.strip() for s in (args.split or "").split(",") if s.strip()]
    if not split:
        split = ["judge_a", "judge_b"]
    if len(split) > 2:
        raise SystemExit("--split 最多两个判读者名（本协议 = A/B 双盲 + 第三名仲裁）")
    variant_for_judge = {}
    for i, judge_name in enumerate(split):
        variant = ("variant_a", "variant_b")[i]
        variant_for_judge[judge_name] = variant
        for unit in units:
            ordered = unit if variant == "variant_a" else dict(
                unit, claims=list(reversed(unit["claims"])))
            md = _packet_md(ordered, variant, "ai", judge_name=judge_name)
            md_path = dest / "packets" / judge_name / unit["model"] / (
                "%s.md" % unit["image_id"])
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md, encoding="utf-8")
        _dump(dest / "templates" / ("%s.json" % judge_name),
              _blank_judge(judge_name, "ai", variant, units,
                           reverse=(variant == "variant_b")))
    _dump(dest / "templates" / "arbiter.json",
          _blank_judge("arbiter", "ai", "variant_a", units))
    # 人类判读包（不带任何 AI 判读结果）
    for unit in units:
        md = _packet_md(unit, "variant_a", "human")
        md_path = dest / "human_packet" / unit["model"] / ("%s.md" % unit["image_id"])
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
    _dump(dest / "human_packet" / "templates" / "human.json",
          _blank_judge("human", "human", "variant_a", units))

    manifest = {
        "schema": PACKET_SCHEMA,
        "generated_by": "tools/g7_adjudicate.py packet",
        "ai_label": AI_LABEL,
        "source": {"json_dir": str(json_dir), "images": str(args.images or ""),
                   "models": models, "units": len(units),
                   "failed_units": failed, "max_claims": args.max_claims},
        "variants": {"variant_a": {"order": "original"},
                     "variant_b": {"order": "reversed",
                                   "note": "claim id 与 A 相同；只改顺序，防顺序锚定"}},
        "split": variant_for_judge,
        "instruction": "判读者不得查看机检产物；AI 判读结果必须带 ai_label=%r；"
                       "分歧交第三名仲裁" % AI_LABEL,
        "units": [{"model": u["model"], "image_id": u["image_id"],
                   "category": u["category"], "image_sha256": u["image_sha256"],
                   "caption_sha256": u["caption_sha256"], "ir_sha256": u["ir_sha256"],
                   "claims_total": u["claims_total"],
                   "claims": [{"id": c["id"], "category": c["category"],
                               "text": c["text"]} for c in u["claims"]]} for u in units],
    }
    _dump(dest / "manifest.json", manifest)
    print("判读包已写：%s" % dest)
    print("  单元 %d 个（失败单元 %d 个，已单列不判）" % (len(units), len(failed)))
    print("  AI 标签：%s" % AI_LABEL)
    print("  人类包：%s（不含 AI 判读结果）" % (dest / "human_packet"))
    return 0


# ─────────────────────────────────────────────────────────────
# calib-packet：从 merged 的「verdict 不一致 claim」生成小样预校准包
# ─────────────────────────────────────────────────────────────
# 为什么需要它：第一轮双盲判读的 κ 只有 0.41（事实）/ 0.04（幻觉），主因是两名判读者
# 对「不可判」的口径不一致。要先把判据校准到 κ≥0.6，才允许进 60 图全量。校准集取
# **第一轮里两人判法不一致的 claim**（约 40–51 条，最难样本），让判读者在新判例下重判。
#
# 三条纪律：
# 1. **旧判定绝不入包**：包里只有 claim id / 类别 / 断言文本 / 图与 caption 与 IR，
#    不出现既有判法，也不出现「争议」字样——否则判读者只是在照抄既有判定。
# 2. 复用 packet 的 markdown/空白模板/manifest 结构，保证现有 merge 能直接消费。
# 3. A/B 顺序变体（judge_b 反序）防顺序锚定；claim 集合与 manifest 完全一致。

def _sha256_file(path: Path) -> str:
    """文件字节 sha256（用于 manifest 里记录 merged.json 指纹）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _disputed_from_merged(merged: dict, max_claims: int) -> list:
    """merged.json → 按单元分组的待重判 claim（只带 id/类别/文本，剥掉旧 verdict）。

    结构以 `cmd_merge` 的真实输出为准（schema=g7-adjudication-merged/1）：
    {"disagreements": [{"claim_id","model","image_id","category","claim","verdicts",...}]}
    这里**只取定位与文本**；`verdicts` 只用于「确认两人确实不一致」的筛选，绝不返回。
    """
    if not isinstance(merged, dict):
        raise SystemExit("merged.json 顶层不是对象（期望 schema=%s）" % MERGED_SCHEMA)
    disagreements = merged.get("disagreements")
    if disagreements is None:
        raise SystemExit(
            "merged.json 缺 `disagreements` 字段，无法抽取校准集。\n"
            "  期望：tools/g7_adjudicate.py merge 的输出（schema=%s）。\n"
            "  如果给的是某位判读者的结果文件，请先跑 merge。" % MERGED_SCHEMA)
    if not isinstance(disagreements, list):
        raise SystemExit("merged.json 的 `disagreements` 不是数组（实际 %s）"
                         % type(disagreements).__name__)
    if not disagreements:
        raise SystemExit(
            "merged.json 里没有「verdict 不一致」的 claim（两名判读者完全一致）。\n"
            "  预校准需要最难样本才有意义；请确认两份判读结果不是同一位判读者的副本，"
            "或换一份 merged。")
    grouped: dict = {}
    for d in disagreements:
        if not isinstance(d, dict):
            raise SystemExit("merged.json 的 disagreements 里有非对象条目：%r" % (d,))
        cid = str(d.get("claim_id") or "")
        model = str(d.get("model") or "")
        image_id = str(d.get("image_id") or "")
        if not (cid and model and image_id):
            raise SystemExit("disagreement 条目缺 claim_id/model/image_id：%r" % (d,))
        norm = {k: _norm_verdict(v) for k, v in (d.get("verdicts") or {}).items()}
        vals = [v for v in norm.values() if v in VERDICTS]
        if len(vals) < 2 or len(set(vals)) < 2:
            continue        # 防御：merge 已保证不一致，这里只做结构自检
        grouped.setdefault((model, image_id), []).append({
            "id": cid,
            "category": str(d.get("category") or ""),
            "text": str(d.get("claim") or ""),
        })
    if not grouped:
        raise SystemExit("merged.json 的 disagreements 里没有可用的两条不一致 verdict"
                         "（结构可能被改过）")
    selected = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda c: c["id"])[:max(1, int(max_claims))]
        selected.append((key, items))
    return selected


def cmd_calib_packet(args) -> int:
    merged_path = Path(args.merged)
    merged = _load_json(merged_path, "merged.json")
    json_dir = Path(args.json_dir)
    if not json_dir.is_dir():
        raise SystemExit("找不到模型产目录：%s（应与 merged 同版本）" % json_dir)
    images = {}
    if args.images and Path(args.images).is_file():
        data = _load_json(args.images, "图片清单")
        images = {i["id"]: i for i in (data.get("images") or [])}

    selected = _disputed_from_merged(merged, args.max_claims)
    units, missing = [], []
    for (model, image_id), claims in selected:
        p = json_dir / model / ("%s.json" % image_id)
        if not p.is_file():
            missing.append("%s/%s.json（文件不存在）" % (model, image_id))
            continue
        payload = _load_json(p, "模型产出")
        if not payload.get("ok"):
            missing.append("%s/%s.json（记录失败：%s）"
                           % (model, image_id, str(payload.get("error") or "")[:80]))
            continue
        units.append(_unit_from_payload(model, image_id, payload,
                                        images.get(image_id), claims))
    if missing:
        raise SystemExit(
            "merged 的 claim 在 json-dir 里找不到对应产出（%d 处）：\n  %s\n"
            "  json-dir 与 merged 版本不匹配？校准包必须用生成 merged 时同一批落盘。"
            % (len(missing), "\n  ".join(missing[:8])))
    if not units:
        raise SystemExit("没有可生成的校准单元（merged 与 json-dir 对不上）")

    dest = Path(args.out)
    split = [s.strip() for s in (args.split or "judge_a,judge_b").split(",") if s.strip()]
    if len(split) != 2:
        raise SystemExit("calib-packet 固定两名判读者（--split a,b），当前 %r" % split)

    variant_for_judge = {}
    for i, judge_name in enumerate(split):
        variant = ("variant_a", "variant_b")[i]
        variant_for_judge[judge_name] = variant
        for unit in units:
            ordered = (unit if variant == "variant_a"
                       else dict(unit, claims=list(reversed(unit["claims"]))))
            md = _packet_md(ordered, variant, "ai", judge_name=judge_name)
            # 统一模板路径：calib2/<judge>/<judge>.json（packet 的旧文案写的是 templates/）
            md = md.replace(
                "同目录/同名 JSON 模板（`templates/` 下，按 claim_id 对齐）",
                "本包模板：`%s/%s.json`（按 claim_id 对齐）" % (judge_name, judge_name))
            md_path = dest / judge_name / "packets" / unit["model"] / (
                "%s.md" % unit["image_id"])
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md, encoding="utf-8")
        # 模板路径统一：calib2/judge_a/judge_a.json、calib2/judge_b/judge_b.json
        _dump(dest / judge_name / ("%s.json" % judge_name),
              _blank_judge(judge_name, "ai", variant, units,
                           reverse=(variant == "variant_b")))

    claim_total = sum(len(u["claims"]) for u in units)
    manifest = {
        "schema": PACKET_SCHEMA,
        "generated_by": "tools/g7_adjudicate.py calib-packet",
        "ai_label": AI_LABEL,
        "source": {
            "kind": "calib-from-merged",
            "note": "校准集来源：从 round2 merged 的 verdict 不一致 claim 抽取"
                    "（只取 claim id/类别/文本；旧 verdict 不入包）",
            "merged": str(merged_path),
            "merged_sha256": _sha256_file(merged_path),
            "json_dir": str(json_dir),
            "images": str(args.images or ""),
            "units": len(units),
            "claims": claim_total,
            "max_claims": args.max_claims,
        },
        "variants": {"variant_a": {"order": "original"},
                     "variant_b": {"order": "reversed",
                                   "note": "claim id 与 A 相同；只改顺序，防顺序锚定"}},
        "split": variant_for_judge,
        "instruction": "判读者不得查看机检产物，也不得查看两轮旧判读结果；"
                       "AI 判读结果必须带 ai_label=%r；按 docs/58 的判例逐条重判"
                       % AI_LABEL,
        "units": [{"model": u["model"], "image_id": u["image_id"],
                   "category": u["category"], "image_sha256": u["image_sha256"],
                   "caption_sha256": u["caption_sha256"], "ir_sha256": u["ir_sha256"],
                   "claims_total": u["claims_total"],
                   "claims": [{"id": c["id"], "category": c["category"],
                               "text": c["text"]} for c in u["claims"]]}
                  for u in units],
    }
    # 自查：claim 集合必须与 manifest 完全一致（变体只是反序，不增不减）
    for judge_name in split:
        tpl = _load_json(dest / judge_name / ("%s.json" % judge_name), "空白模板")
        tpl_ids = [cid for u in tpl["units"] for cid in (u.get("claims") or {})]
        man_ids = [c["id"] for u in manifest["units"] for c in u["claims"]]
        if sorted(tpl_ids) != sorted(man_ids):
            raise SystemExit("内部错误：%s 模板的 claim 集合与 manifest 不一致" % judge_name)

    _dump(dest / "manifest.json", manifest)
    manifest_sha = _sha256_file(dest / "manifest.json")
    print("校准判读包已写：%s" % dest)
    print("  单元 %d 个；claim %d 条（每单元 ≤%d）"
          % (len(units), claim_total, args.max_claims))
    for u in units:
        print("    %-6s %-6s claims=%d" % (u["model"], u["image_id"],
                                           len(u["claims"])))
    print("  模板：%s / %s"
          % (dest / split[0] / ("%s.json" % split[0]),
             dest / split[1] / ("%s.json" % split[1])))
    print("  manifest sha256：%s" % manifest_sha)
    return 0


# ─────────────────────────────────────────────────────────────
# merge：一致性 + 仲裁 + 准确率/统计 + 三档结论
# ─────────────────────────────────────────────────────────────

def _load_judge(path, label: str) -> dict:
    j = _load_json(path, label)
    if j.get("schema") != JUDGE_SCHEMA:
        raise SystemExit("%s 的 schema=%r，期望 %r" % (path, j.get("schema"), JUDGE_SCHEMA))
    jtype = str(j.get("judge_type") or "ai").strip().lower()
    if jtype not in ("ai", "human"):
        raise SystemExit("%s 的 judge_type=%r 不合法（ai|human）" % (path, jtype))
    if jtype == "ai" and AI_LABEL not in str(j.get("ai_label") or ""):
        raise SystemExit("%s 是 AI 判读但缺 ai_label=%r —— 协议要求 AI 判读必须标注，"
                         "否则结论会被误读成人类判读" % (path, AI_LABEL))
    if not j.get("judge_id"):
        raise SystemExit("%s 缺 judge_id" % path)
    # 非法 verdict 不能静默当「没填」——否则一次抄错就会把 κ 悄悄抬高。
    for u in j.get("units") or []:
        for cid, v in (u.get("claims") or {}).items():
            if v is None:
                continue
            if str(v).strip().lower() not in VERDICTS:
                raise SystemExit(
                    "%s：claim %s 的 verdict=%r 不合法（只允许 %s）"
                    % (path, cid, v, " / ".join(VERDICTS)))
        hv = u.get("hallucination")
        if hv is not None and _norm_hallucination(hv) is None:
            raise SystemExit(
                "%s：unit %s/%s 的 hallucination=%r 不合法（只允许 present / absent）"
                % (path, u.get("model"), u.get("image_id"), hv))
    return j


def _unit_claims(judge: dict) -> dict:
    out = {}
    for u in judge.get("units") or []:
        key = (u.get("model"), u.get("image_id"))
        claims = {cid: _norm_verdict(v) for cid, v in (u.get("claims") or {}).items()}
        out[key] = {"claims": claims,
                    "hallucination": _norm_hallucination(u.get("hallucination")),
                    "notes": u.get("notes") or ""}
    return out


def _manifest_index(path) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    m = _load_json(p, "判读包 manifest")
    idx = {}
    for u in m.get("units") or []:
        for c in u.get("claims") or []:
            idx[c["id"]] = {"model": u.get("model"), "image_id": u.get("image_id"),
                            "category": c.get("category"), "text": c.get("text")}
    return idx


def _pairwise_kappas(list_of_maps: list, categories) -> dict:
    """两两 κ（>2 名判读者时报告最小值）。"""
    pairs = []
    for i in range(len(list_of_maps)):
        for j in range(i + 1, len(list_of_maps)):
            a, b = list_of_maps[i], list_of_maps[j]
            common = set(a) & set(b)
            pairs.append(cohens_kappa([(a[c], b[c]) for c in common
                                       if a[c] is not None and b[c] is not None],
                                      categories))
    usable = [p for p in pairs if p["kappa"] is not None]
    return {"pairwise": pairs,
            "min_kappa": min((p["kappa"] for p in usable), default=None),
            "n_pairs": len(pairs)}


def _accuracy_block(correct: int, wrong: int, unverifiable: int) -> dict:
    return {"correct": correct, "wrong": wrong, "unverifiable": unverifiable,
            "accuracy": _rate(correct, correct + wrong),
            "wilson95": wilson_ci(correct, correct + wrong),
            "resolved": correct + wrong}


def _conclusion_tier(kappa_ok: bool, n_resolved: int, fisher: float | None,
                     min_n: int, acc: dict) -> dict:
    """协议的唯一出口：三档结论之一。永不输出「更好」。"""
    if not kappa_ok:
        return {"tier": "样本不足",
                "reason": "判据一致性不足（κ 低于门槛），只能报样本/判据不足"}
    if n_resolved < min_n:
        return {"tier": "样本不足",
                "reason": "可判事实只剩 %d 条（< min_n=%d），不足以比较"
                          % (n_resolved, min_n)}
    if fisher is not None and fisher < 0.05:
        ranked = sorted((m for m in acc if acc[m]["accuracy"] is not None),
                        key=lambda m: -(acc[m]["accuracy"] or 0))
        direction = ""
        if len(ranked) == 2:
            direction = "%s 的事实准确率高于 %s" % (ranked[0], ranked[1])
        return {"tier": "显著差", "reason": "Fisher p=%.4f<0.05" % fisher,
                "direction": direction}
    return {"tier": "无显著差异",
            "reason": "Fisher p=%s（不显著 ≠ 无差异；见各模型 Wilson CI 与最小可检出差异）"
                      % ("n/a" if fisher is None else "%.4f" % fisher)}


def cmd_merge(args) -> int:
    judge_paths = [s.strip() for s in args.judges.split(",") if s.strip()]
    if len(judge_paths) < 2:
        raise SystemExit("merge 需要 ≥2 名判读者（--judges a.json,b.json）；"
                         "单判读者无法算一致性，协议不允许")
    judges = [_load_judge(p, "判读者结果") for p in judge_paths]
    ids = [j["judge_id"] for j in judges]
    if len(set(ids)) != len(ids):
        raise SystemExit("judge_id 重复：%s（双盲要求不同判读者）" % ids)
    arbiter = _load_judge(args.arbiter, "仲裁者结果") if args.arbiter else None
    if arbiter and arbiter["judge_id"] in ids:
        raise SystemExit("仲裁者不能同时是判读者：%s" % arbiter["judge_id"])
    manifest = _manifest_index(args.manifest or (Path(args.out) / "manifest.json"))
    maps = [_unit_claims(j) for j in judges]
    keys = sorted(set().union(*[set(m) for m in maps]))

    # ── 一致性：事实 verdict（2 名用 Cohen κ；>2 名取两两最小）+ 整图幻觉 ──
    claim_series = {}
    hall_series = {}
    for key in keys:
        claim_series[key] = [m.get(key, {}).get("claims", {}) for m in maps]
        hall_series[key] = [m.get(key, {}).get("hallucination") for m in maps]
    # claim_id → 每位判读者的 verdict（None=没给）
    cid_series = {}
    for key in keys:
        for j_index, claims in enumerate(claim_series[key]):
            for cid, verdict in claims.items():
                if verdict is None:
                    continue
                row = cid_series.setdefault(cid, [None] * len(judges))
                row[j_index] = verdict
    claim_pairs = []
    by_model_pairs = {}
    for cid, row in sorted(cid_series.items()):
        vals = [v for v in row if v is not None]
        if len(vals) < 2:
            continue
        model = (manifest.get(cid) or {}).get("model") or next(
            (k[0] for k in keys if cid.startswith(str(k[0]) + "|")), "?")
        claim_pairs.append((vals[0], vals[1]))
        by_model_pairs.setdefault(model, []).append((vals[0], vals[1]))
    claims_agreement = cohens_kappa(claim_pairs, VERDICTS)
    if len(judges) > 2:
        # >2 名：两两 κ，报最小值（保守）。不同判读者对的 claim id 集合取交集。
        per_judge = [{cid: row[i] for cid, row in cid_series.items()
                      if row[i] is not None} for i in range(len(judges))]
        claims_agreement["multi"] = _pairwise_kappas(per_judge, VERDICTS)
    per_model_kappa = {m: cohens_kappa(pairs, VERDICTS)
                       for m, pairs in by_model_pairs.items()}
    hall_pairs = []
    for key in keys:
        vals = [v for v in hall_series[key] if v is not None]
        if len(vals) >= 2:
            hall_pairs.append((vals[0], vals[1]))
    hall_agreement = cohens_kappa(hall_pairs, HALLUCINATION)
    kappa_values = [claims_agreement["kappa"]]
    if hall_agreement["kappa"] is not None:
        kappa_values.append(hall_agreement["kappa"])
    kappa_values += [v["kappa"] for v in per_model_kappa.values() if v["kappa"] is not None]
    kappa_values = [v for v in kappa_values if v is not None]
    min_kappa = min(kappa_values) if kappa_values else None
    kappa_ok = min_kappa is not None and min_kappa >= args.threshold

    # ── 仲裁与最终 truth ──
    arbiter_map = _unit_claims(arbiter) if arbiter else {}
    truth = {}
    disagreements = []
    unresolved = []
    for key in keys:
        model, image_id = key
        for cid in sorted({c for claims in claim_series[key] for c in claims}):
            vals = []
            for claims in claim_series[key]:
                if cid in claims and claims[cid] is not None:
                    vals.append(claims[cid])
            if not vals:
                continue
            arb = (arbiter_map.get(key, {}).get("claims") or {}).get(cid)
            if len(set(vals)) == 1:
                truth[cid] = vals[0]
            else:
                verdicts = {}
                for judge, claims in zip(judges, claim_series[key], strict=True):
                    got = claims.get(cid)
                    if got is not None:
                        verdicts[judge["judge_id"]] = got
                disagreements.append({
                    "claim_id": cid, "model": model, "image_id": image_id,
                    "category": (manifest.get(cid) or {}).get("category", ""),
                    "claim": (manifest.get(cid) or {}).get("text", ""),
                    "verdicts": verdicts,
                    "arbiter": arb,
                })
                if arb:
                    truth[cid] = arb
                else:
                    unresolved.append(cid)
    # 分歧里仲裁者给了结论的，也算 resolved
    unresolved = [cid for cid in unresolved if cid not in truth]

    # ── 准确率（剔除 unverifiable）+ Wilson；按模型 / 按类别 ──
    models = sorted({k[0] for k in keys})
    acc = {}
    per_category = {}
    unit_rows = []
    for model in models:
        correct = wrong = unver = 0
        for key in keys:
            if key[0] != model:
                continue
            mc = mw = mu = 0
            for cid, verdict in truth.items():
                if not cid.startswith("%s|%s|" % key):
                    continue
                if verdict == "correct":
                    correct += 1
                    mc += 1
                elif verdict == "wrong":
                    wrong += 1
                    mw += 1
                else:
                    unver += 1
                    mu += 1
                cat = (manifest.get(cid) or {}).get("category", "?")
                box = per_category.setdefault(cat, {}).setdefault(
                    model, {"correct": 0, "wrong": 0, "unverifiable": 0})
                box["correct" if verdict == "correct" else
                    "wrong" if verdict == "wrong" else "unverifiable"] += 1
            unit_rows.append({"model": model, "image_id": key[1],
                              "correct": mc, "wrong": mw, "unverifiable": mu,
                              "accuracy": _rate(mc, mc + mw), "resolved": mc + mw})
        acc[model] = _accuracy_block(correct, wrong, unver)
    cat_out = {}
    for cat, per_model in sorted(per_category.items()):
        cat_out[cat] = {m: _accuracy_block(b["correct"], b["wrong"], b["unverifiable"])
                        for m, b in per_model.items()}

    # ── 模型间检验：Fisher（事实级）+ 符号检验（逐图）────────────────────
    fisher = sign = None
    if len(models) == 2:
        a_model, b_model = models
        fisher = round(fisher_p(acc[a_model]["correct"], acc[a_model]["wrong"],
                                acc[b_model]["correct"], acc[b_model]["wrong"]), 4)
        per_img = {}
        for row in unit_rows:
            per_img.setdefault(row["image_id"], {})[row["model"]] = row["accuracy"]
        wins = losses = ties = 0
        for box in per_img.values():
            ra, rb = box.get(a_model), box.get(b_model)
            if ra is None or rb is None:
                continue
            if abs(ra - rb) < 1e-9:
                ties += 1
            elif ra > rb:
                wins += 1
            else:
                losses += 1
        sign = {"wins": wins, "losses": losses, "ties": ties,
                "p": round(sign_test_p(wins, losses), 4)}

    # ── 幻觉：各模型 present 率 + McNemar（按图配对）────────────────────
    hall = {}
    if len(models) == 2:
        b = c = 0
        for img in sorted({k[1] for k in keys}):
            va = hb = None
            for key in keys:
                if key[1] != img:
                    continue
                vals = [v for v in hall_series[key] if v is not None]
                if key[0] == models[0] and vals:
                    va = vals[0]
                elif key[0] == models[1] and vals:
                    hb = vals[0]
            if va == "present" and hb == "absent":
                b += 1
            elif va == "absent" and hb == "present":
                c += 1
        hall = {"mcnemar_b": b, "mcnemar_c": c, "p": round(mcnemar_p(b, c), 4)}
    hall_rates = {}
    for model in models:
        present = total = 0
        for key in keys:
            if key[0] != model:
                continue
            vals = [v for v in hall_series[key] if v is not None]
            if not vals:
                continue
            total += 1
            present += int(vals[0] == "present")
        hall_rates[model] = {"present": present, "judged": total,
                             "rate": _rate(present, total)}

    n_resolved = sum(acc[m]["resolved"] for m in models) if models else 0
    tier = _conclusion_tier(kappa_ok, n_resolved, fisher, args.min_n, acc)

    merged = {
        "schema": MERGED_SCHEMA,
        "generated_by": "tools/g7_adjudicate.py merge",
        "ai_label_required": AI_LABEL,
        "judges": [{"judge_id": j["judge_id"], "judge_type": j.get("judge_type"),
                    "variant": j.get("variant")} for j in judges],
        "arbiter": arbiter["judge_id"] if arbiter else None,
        "threshold": args.threshold,
        "min_n": args.min_n,
        "agreement": {
            "claims": claims_agreement,
            "claims_per_model": per_model_kappa,
            "hallucination": hall_agreement,
            "min_kappa": min_kappa,
            "ok": kappa_ok,
        },
        "disagreements": disagreements,
        "unresolved": unresolved,
        "accuracy": acc,
        "accuracy_per_category": cat_out,
        "units": unit_rows,
        "machine_compare": None,
        "between_models": {"fisher_p": fisher, "sign_test": sign},
        "hallucination": {"rates": hall_rates, "mcnemar": hall},
        "conclusion": tier,
        "evidence_level": ("AI 判读，不是人类" if all(j.get("judge_type") == "ai"
                                                     for j in judges) else "混合（含人类判读）"),
    }

    # ── 与机检三方对比（可选）：拿 binding.json 的覆盖率做一列并排 ──
    if args.binding:
        binding = _load_json(args.binding, "机检结果")
        compare = {}
        for model in models:
            box = (binding.get("summary") or {}).get("models", {}).get(model)
            if not box:
                continue
            classes = box.get("classes") or {}
            den = sum(c.get("applicable") or 0 for c in classes.values())
            bnd = sum(c.get("bound") or 0 for c in classes.values())
            compare[model] = {"machine_bound": bnd, "machine_applicable": den,
                              "machine_coverage": _rate(bnd, den),
                              "adjudicated_accuracy": acc[model]["accuracy"]}
        merged["machine_compare"] = compare

    _dump(Path(args.out) / "merged.json", merged)

    # ── 人读摘要 ─────────────────────────────────────────────
    print("判读者：%s；仲裁：%s" % ("、".join(ids), arbiter["judge_id"] if arbiter else "未提供"))
    print("一致性：事实 κ=%s（n=%d）、幻觉 κ=%s、逐模型最小 κ=%s；门槛 %.2f → %s"
          % (claims_agreement["kappa"], claims_agreement["n"],
             hall_agreement["kappa"], min_kappa, args.threshold,
             "PASS" if kappa_ok else "FAIL（判据不足）"))
    for model in models:
        a = acc[model]
        print("%s：正确 %d / 错 %d / 不可判 %d；准确率 %s（95%% CI %s）"
              % (model, a["correct"], a["wrong"], a["unverifiable"],
                 "n/a" if a["accuracy"] is None else "%.1f%%" % (100 * a["accuracy"]),
                 a["wilson95"]))
    if fisher is not None:
        print("模型间 Fisher p=%s；逐图符号检验 %s" % (fisher, sign))
    print("分歧 %d 条，未仲裁 %d 条" % (len(disagreements), len(unresolved)))
    print("结论（三档之一）：%s —— %s" % (tier["tier"], tier.get("reason", "")))
    print("证据等级：%s" % merged["evidence_level"])
    if not kappa_ok:
        print("⚠ 一致性低于 %.2f：本轮只能报「样本不足」（判据不足），不得比较模型优劣"
              % args.threshold, file=sys.stderr)
        return 1
    return 0


# ─────────────────────────────────────────────────────────────
# calibrate：自动分 vs 判读排序（Spearman ρ + 翻转率）
# ─────────────────────────────────────────────────────────────

def _scores_from_file(path) -> dict:
    data = _load_json(path, "自动分文件")
    rows = data.get("rows") if isinstance(data, dict) else data
    out = {}
    for r in rows or []:
        try:
            out[(r["model"], r["image_id"])] = float(r["score"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _scores_from_binding(path, binding_class: str | None) -> dict:
    """从机检 binding.json 提「自动分」：每个 (模型,图) 的平均/单类覆盖率。

    这是**自动分**示例来源（也是本轮唯一的自动分）；`calibrate` 只关心它与判读排序
    是否同向，不关心它本身是什么分数。分母为 0 的图不产生分数（不得把 n/a 当 1.0）。
    """
    binding = _load_json(path, "机检结果")
    summary = binding.get("summary") or {}
    out = {}
    for model, box in (summary.get("models") or {}).items():
        classes = {name: (c.get("per_image") or {})
                   for name, c in (box.get("classes") or {}).items()
                   if isinstance(c, dict)}
        if binding_class:
            classes = {binding_class: classes.get(binding_class, {})}
        images = set().union(*[set(v) for v in classes.values()]) if classes else set()
        for image_id in images:
            if binding_class:
                cov = (classes[binding_class].get(image_id) or {}).get("coverage")
            else:
                covs = [(v.get(image_id) or {}).get("coverage") for v in classes.values()]
                covs = [c for c in covs if c is not None]
                cov = round(sum(covs) / len(covs), 4) if covs else None
            if cov is not None:
                out[(model, image_id)] = float(cov)
    return out


def cmd_calibrate(args) -> int:
    merged = _load_json(args.merged, "合并结果")
    if args.scores:
        scores = _scores_from_file(args.scores)
    elif args.from_binding:
        scores = _scores_from_binding(args.from_binding, args.binding_class or None)
    else:
        raise SystemExit("没有自动分：给 --scores 或 --from-binding")
    if not scores:
        raise SystemExit("自动分是空的（检查 --from-binding 的路径/类别）")
    units = [u for u in (merged.get("units") or [])
             if u.get("accuracy") is not None and (u["model"], u["image_id"]) in scores]
    if len(units) < 3:
        raise SystemExit("可配对的 (模型,图) 单元只有 %d 个（<3），无法算 ρ" % len(units))
    xs = [scores[(u["model"], u["image_id"])] for u in units]
    ys = [u["accuracy"] for u in units]
    rho = spearman_rho(xs, ys)
    per_model = {}
    for model in sorted({u["model"] for u in units}):
        sub = [(scores[(u["model"], u["image_id"])], u["accuracy"])
               for u in units if u["model"] == model]
        per_model[model] = spearman_rho([p[0] for p in sub], [p[1] for p in sub])
    # 翻转率：按 truth 排序后看相邻对的自动分顺序是否反了（平局剔除）
    ordered = sorted(units, key=lambda u: -(u["accuracy"] or 0))
    flips = pairs = 0
    for a, b in zip(ordered, ordered[1:], strict=False):
        ra, rb = a["accuracy"], b["accuracy"]
        if ra is None or rb is None or abs(ra - rb) < 1e-9:
            continue
        pairs += 1
        sa = scores[(a["model"], a["image_id"])]
        sb = scores[(b["model"], b["image_id"])]
        if sa < sb:
            flips += 1
    flip_rate = round(flips / pairs, 4) if pairs else None
    readme_allowed = rho is not None and rho >= args.rho_threshold
    payload = {
        "schema": CALIB_SCHEMA,
        "generated_by": "tools/g7_adjudicate.py calibrate",
        "score_source": args.scores or args.from_binding,
        "binding_class": args.binding_class,
        "units": len(units),
        "rho": rho,
        "rho_threshold": args.rho_threshold,
        "per_model_rho": per_model,
        "flip_rate": flip_rate,
        "flips": flips,
        "adjacent_pairs": pairs,
        "readme_allowed": readme_allowed,
        "warning": "" if readme_allowed else "该分不应进 README",
    }
    _dump(Path(args.out) / "calibration.json", payload)
    print("自动分 vs 判读排序：Spearman ρ=%s（n=%d，逐模型 %s）"
          % ("n/a" if rho is None else rho, len(units), dict(per_model)))
    print("翻转率 %s（相邻对 %d，翻转 %d）"
          % ("n/a" if flip_rate is None else "%.1f%%" % (100 * flip_rate),
             pairs, flips))
    if not readme_allowed:
        print("ρ < %.1f：**该分不应进 README**（排序与判读不一致的分数不得当结论）"
              % args.rho_threshold, file=sys.stderr)
        return 1
    print("ρ ≥ %.1f：该分可用于排序（仍不作放行闸）" % args.rho_threshold)
    return 0


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="G7 双盲判读协议工具（packet / merge / calibrate）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("packet", help="生成双盲判读包 + 人类判读包（不含机检结论）")
    p.add_argument("--json-dir", default=str(OUT / "json"),
                   help="模型产目录（含 <model>/<img>.json）")
    p.add_argument("--images", default=str(OUT / "images.json"))
    p.add_argument("--models", default="A-4B,B-9B")
    p.add_argument("--ids", default="", help="逗号分隔；留空 = 全部")
    p.add_argument("--split", default="judge_a,judge_b",
                   help="判读者代号（逗号分隔，最多 2 个；A/B 双盲变体按此命名）")
    p.add_argument("--max-claims", type=int, default=9,
                   help="每图每模型最多保留几条候选事实（协议 3–9）")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.set_defaults(func=cmd_packet)

    cp = sub.add_parser(
        "calib-packet",
        help="从 merged 的 verdict 不一致 claim 生成小样预校准包（旧判定不入包）")
    cp.add_argument("--merged", required=True, help="第一轮 merge 输出的 merged.json")
    cp.add_argument("--json-dir", required=True,
                    help="与该 merged 同版本的模型产目录（含 <model>/<img>.json）")
    cp.add_argument("--images", default=str(OUT / "images.json"))
    cp.add_argument("--split", default="judge_a,judge_b",
                    help="两名判读者代号；judge_a=原始顺序，judge_b=反序")
    cp.add_argument("--max-claims", type=int, default=9,
                    help="每单元最多保留几条待重判 claim（默认 9）")
    cp.add_argument("--out", default=str(DEFAULT_OUT / "calib2"))
    cp.set_defaults(func=cmd_calib_packet)

    m = sub.add_parser("merge", help="合并判读者结果：κ/分歧/仲裁/准确率/三档结论")
    m.add_argument("--judges", required=True, help="逗号分隔的判读者结果 JSON（≥2）")
    m.add_argument("--arbiter", default="", help="第三名仲裁结果（只处理分歧）")
    m.add_argument("--manifest", default="", help="packet 生成的 manifest.json")
    m.add_argument("--binding", default="", help="可选：机检 binding.json（三方对比）")
    m.add_argument("--threshold", type=float, default=KAPPA_THRESHOLD)
    m.add_argument("--min-n", type=int, default=MIN_N_DEFAULT)
    m.add_argument("--out", default=str(DEFAULT_OUT))
    m.set_defaults(func=cmd_merge)

    c = sub.add_parser("calibrate", help="自动分 vs 判读排序：Spearman ρ + 翻转率")
    c.add_argument("--merged", default=str(DEFAULT_OUT / "merged.json"))
    c.add_argument("--scores", default="", help="自动分 JSON：{rows:[{model,image_id,score}]}")
    c.add_argument("--from-binding", default="",
                   help="或从机检 binding.json 提分（per-image coverage）")
    c.add_argument("--binding-class", default="", help="只用某一类覆盖率（如 color）")
    c.add_argument("--rho-threshold", type=float, default=RHO_THRESHOLD)
    c.add_argument("--out", default=str(DEFAULT_OUT))
    c.set_defaults(func=cmd_calibrate)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
