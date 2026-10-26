# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""官方 prompt-enhancer 草稿编译：格式 / 转换 / 边界 / state / CLI。

零 GPU、零宿主；与官方 `compile_prompt.py` 的 8 项测试对齐，
并补本包多出的 state v3 映射与 256 元素上限。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import editor as E  # noqa: E402
from core import skill_draft as S  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("[OK]   " + name)
    else:
        FAIL.append(name)
        print("[FAIL] " + name + (("  " + str(detail)) if detail else ""))


def raises(name, fn, needle=""):
    try:
        fn()
    except Exception as exc:
        check(name, needle in str(exc), str(exc))
    else:
        check(name, False, "没有报错")


DRAFT = {
    "width": 1536,
    "height": 1024,
    "bbox_coord_space": "abs",
    "bbox_format": "xyxy",
    "summary": {"meta": ["poster"], "canvas": "single", "caption": "A ceramic campaign."},
    "items": [
        {"label": "text", "caption": "Dark-green lettering.",
         "text": '把日常，过成诗。\n"MOSS" 🪴', "bbox": [928, 272, 1408, 448]},
        {"label": "object", "caption": "A cup.",
         "bbox": [176, 224, 800, 864], "palette": ["#112233", "dark green"]},
    ],
}


def test_caption():
    p, rep = S.compile_draft({"caption": 'An ivory cup marked "茶".\nSecond line.'})
    check("caption 是纯文本", p == 'An ivory cup marked "茶".\nSecond line.', p)
    check("caption report 类型", rep["kind"] == "caption" and rep["items"] == 0, rep)
    raises("caption 空串拒绝", lambda: S.compile_draft({"caption": ""}), "非空")
    raises("caption 零宽拒绝", lambda: S.compile_draft({"caption": "\u200b"}), "非空")
    raises("caption 带额外字段拒绝",
           lambda: S.compile_draft({"caption": "x", "items": []}), "未知字段")
    raises("caption 非字符串拒绝", lambda: S.compile_draft({"caption": 3}), "非空")


def test_layout_basic():
    before = copy.deepcopy(DRAFT)
    prompt, rep = S.compile_draft(copy.deepcopy(DRAFT))
    out = json.loads(prompt)
    check("layout 输出 abs_centralized/yxyx",
          out["bbox_coord_space"] == "abs_centralized" and out["bbox_format"] == "yxyx", out)
    check("layout 去掉画布宽高", "width" not in out and "height" not in out, list(out))
    check("text bbox 官方期望", out["items"][0]["bbox"] == [-240, 160, -64, 640], out["items"][0])
    check("object bbox 官方期望", out["items"][1]["bbox"] == [-288, -592, 352, 32], out["items"][1])
    check("text 原样保留", out["items"][0]["text"] == DRAFT["items"][0]["text"], out["items"][0]["text"])
    check("summary 原样保留", out["summary"] == DRAFT["summary"], out["summary"])
    check("item 可选字段保留", out["items"][1]["palette"] == ["#112233", "dark green"], out["items"][1])
    check("编译不修改输入", DRAFT == before)
    check("输出无真实换行", "\n" not in prompt)
    check("report 画布/元素数", rep["kind"] == "layout" and rep["width"] == 1536
          and rep["height"] == 1024 and rep["items"] == 2, rep)
    check("report labels 计数", rep["labels"] == {"text": 1, "object": 1}, rep["labels"])


def test_geometry_edges():
    check("小数边 floor/ceil", S._centered_box([10.9, 20.1, 100.01, 200.99], 768, 1344, "box")
          == [-652, -374, -471, -283])
    check("贴边框", S._centered_box([0, 0, 768, 1344], 768, 1344, "box")
          == [-672, -384, 672, 384])
    for i, box in enumerate([
        [-1, 0, 100, 100], [0, 0, 1537, 100], [0, 0, 100, 1025],
        [100, 0, 50, 100], [0, 0, 0, 100], [0, 0, 100],
        [True, 0, 100, 100], [0, 0, float("nan"), 100],
        [0, 0, float("inf"), 100], [0, 0, "100", 100],
        [0, 0, 0.00000000001, 100],
    ]):
        d = copy.deepcopy(DRAFT)
        d["items"][0]["bbox"] = box
        raises("非法 bbox #%d 拒绝" % i, lambda d=d: S.compile_draft(d), "bbox")


def test_dimensions_and_conventions():
    for field, value in (("width", 1023), ("height", 0), ("width", True),
                         ("height", 1024.0), ("width", 1024.5),
                         ("bbox_coord_space", "abs_centralized"),
                         ("bbox_coord_space", "normalized_1000"),
                         ("bbox_format", "yxyx")):
        d = copy.deepcopy(DRAFT)
        d[field] = value
        raises("%s=%r 拒绝" % (field, value), lambda d=d: S.compile_draft(d))


def test_fields_and_schema():
    cases = []
    for where, mutate in (
        ("根", lambda d: d.update(children=[])),
        ("summary", lambda d: d["summary"].update(extra=1)),
        ("item", lambda d: d["items"][0].update(font_size=32)),
        ("item", lambda d: d["items"][0].update(parent="cup")),
    ):
        d = copy.deepcopy(DRAFT)
        mutate(d)
        cases.append((where, d))
    for where, d in cases:
        raises("%s 未知字段拒绝" % where, lambda d=d: S.compile_draft(d), "未知字段")

    d = copy.deepcopy(DRAFT)
    d["summary"]["canvas"] = "poster"
    raises("summary.canvas 非法拒绝", lambda: S.compile_draft(d), "summary.canvas")

    d = copy.deepcopy(DRAFT)
    d["summary"]["meta"] = "poster"
    raises("summary.meta 非数组拒绝", lambda: S.compile_draft(d), "summary.meta")

    d = copy.deepcopy(DRAFT)
    d["items"][1]["tags"] = "cup"
    raises("item.tags 非数组拒绝", lambda: S.compile_draft(d), "tags")

    d = copy.deepcopy(DRAFT)
    d["items"][0]["label"] = "background"
    raises("label 非法拒绝", lambda: S.compile_draft(d), "label")

    d = copy.deepcopy(DRAFT)
    del d["items"][0]["text"]
    raises("text 元素缺 text 拒绝", lambda: S.compile_draft(d), "text")

    d = copy.deepcopy(DRAFT)
    d["items"][0]["text"] = "\u200b"
    raises("text 零宽拒绝", lambda: S.compile_draft(d), "非空")

    d = copy.deepcopy(DRAFT)
    d["items"] = []
    raises("items 空数组拒绝", lambda: S.compile_draft(d), "非空")


def test_json_edges():
    raises("重复键拒绝", lambda: S.loads('{"caption":"one","caption":"two"}'), "重复键")
    raises("NaN 拒绝", lambda: S.loads('{"caption":NaN}'), "NaN")
    raises("Infinity 拒绝", lambda: S.loads('{"caption":Infinity}'), "Infinity")
    raises("非对象根拒绝", lambda: S.loads("[]"), "JSON 对象")
    raises("超长草稿拒绝", lambda: S.loads("x" * (S.MAX_DRAFT_CHARS + 1)), "超过上限")


def test_item_cap():
    base = copy.deepcopy(DRAFT)
    base["items"] = [{"label": "object", "caption": "x", "bbox": [0, 0, 16, 16]}
                     for _ in range(S.MAX_ITEMS)]
    p, rep = S.compile_draft(base)
    check("256 个元素可编译", rep["items"] == S.MAX_ITEMS, rep["items"])
    base["items"].append({"label": "object", "caption": "x", "bbox": [0, 0, 16, 16]})
    raises("257 个元素拒绝", lambda: S.compile_draft(base), "上限")


def test_state_mapping():
    st, notes = S.draft_to_state(copy.deepcopy(DRAFT))
    E.parse_state(json.dumps(st, ensure_ascii=False))
    check("state preset=free", st["preset"] == "free", st["preset"])
    check("state canvas", st["canvas"] == {"width": 1536, "height": 1024}, st["canvas"])
    check("state style=summary.caption", st["style"] == DRAFT["summary"]["caption"], st["style"])
    check("state 元素数", len(st["items"]) == 2, len(st["items"]))
    check("state text 保留换行", st["items"][0]["text"] == DRAFT["items"][0]["text"], st["items"][0])
    check("state bbox 归一化", st["items"][1]["bbox"] == [0.114583, 0.21875, 0.520833, 0.84375],
          st["items"][1]["bbox"])
    check("state 合法 palette 保留", st["items"][1].get("palette") == ["#112233"], st["items"][1])
    check("state 丢弃 summary.meta 有说明", any("summary.meta" in n for n in notes), notes)
    check("state 丢弃 summary.canvas 有说明", any("summary.canvas" in n for n in notes), notes)
    check("state 丢弃非 hex palette 有说明", any("palette" in n for n in notes), notes)
    raises("caption 不能转 state", lambda: S.draft_to_state({"caption": "x"}), "caption")

    big = copy.deepcopy(DRAFT)
    big["width"] = big["height"] = 8192
    big["items"] = [{"label": "object", "caption": "x", "bbox": [0, 0, 16, 16]}]
    p, _ = S.compile_draft(big)
    check("8192 画布 compile 可用", isinstance(p, str) and json.loads(p)["items"], p[:40])
    raises("8192 画布 state 拒绝", lambda: S.draft_to_state(big), "4096")

    small = copy.deepcopy(DRAFT)
    small["width"] = small["height"] = 16
    small["items"] = [{"label": "object", "caption": "x", "bbox": [0, 0, 8, 8]}]
    p, _ = S.compile_draft(small)
    check("16 画布 compile 可用（官方口径）", isinstance(p, str) and json.loads(p)["items"], p[:40])
    raises("16 画布 state 拒绝", lambda: S.draft_to_state(small), "64..4096")

    huge = copy.deepcopy(DRAFT)
    huge["items"][0]["bbox"] = [0, 0, 10 ** 400, 16]
    raises("超大整数 bbox 拒绝（不抛 OverflowError）",
           lambda: S.compile_draft(huge), "有限数字")


def test_cli():
    script = os.path.join(ROOT, "tools", "compile_skill_draft.py")
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "draft.json")
        out = os.path.join(td, "prompt.txt")
        state = os.path.join(td, "state.json")
        original = json.dumps(DRAFT, ensure_ascii=False, indent=2)
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(original)
        r = subprocess.run([sys.executable, script, src, "--output", out, "--state", state],
                           capture_output=True, text=True)
        check("CLI 成功", r.returncode == 0, r.stderr)
        with open(out, encoding="utf-8") as fh:
            prompt = fh.read().strip()
        with open(state, encoding="utf-8") as fh:
            st = json.load(fh)
        check("CLI prompt 与库一致", prompt == S.compile_draft(DRAFT)[0], prompt[:60])
        check("CLI state 可解析", st["preset"] == "free" and st["canvas"]["width"] == 1536, st)
        with open(src, encoding="utf-8") as fh:
            check("CLI 不覆盖输入", fh.read() == original)

        with open(src, "w", encoding="utf-8") as fh:
            fh.write('{"caption":"one","caption":"two"}')
        before = "existing result"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(before)
        r = subprocess.run([sys.executable, script, src, "--output", out],
                           capture_output=True, text=True)
        with open(out, encoding="utf-8") as fh:
            check("CLI 失败不覆盖输出", r.returncode != 0 and fh.read() == before, r.stderr)

        with open(src, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"caption": "x"}))
        r = subprocess.run([sys.executable, script, src, "--state", state],
                           capture_output=True, text=True)
        check("CLI caption + --state 拒绝", r.returncode != 0, r.stderr)

        r = subprocess.run([sys.executable, script, src, "--output", src],
                           capture_output=True, text=True)
        check("CLI 拒绝覆盖输入", r.returncode != 0, r.stderr)


def main():
    test_caption()
    test_layout_basic()
    test_geometry_edges()
    test_dimensions_and_conventions()
    test_fields_and_schema()
    test_json_edges()
    test_item_cap()
    test_state_mapping()
    test_cli()
    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
