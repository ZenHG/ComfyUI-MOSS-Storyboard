# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""M1a-2：节点 `MossPromptSmith` caption 路径护栏的单测。零 GPU、零网络（桩服务端）。

覆盖四件事：

1. **类型闸接到节点**：`guard_caption_json` 用 M1a 的同一份字段判据
   （`smith.reverse.gate_character_kind`）把误标成 subject 的物体降级为 props，
   `kind_downgrades` / `age_dropped` 变成节点 report 里用户看得见的说明；
2. **实体上限**：inventory 的 subjects/props/materials/text 超限时截断并出声；
   pass1 的 JSON schema 带同样的 `maxItems`；
3. **`_chat` 的结构化四级降级 + 重复保护**：json_schema → json_object → 无 response_format
   → 再摘 chat_template_kwargs；JSON 请求带 repeat_penalty，服务端拒绝时降级出声；
4. **既有行为不回归**：两遍式顺序（pass1→pass2）、解析失败重试一次（`_JSON_RETRY`）、
   thinking 关闭。

跑：python tests/test_node_caption_guard.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import moss_llm.nodes as nodes                                    # noqa: E402
from moss_llm.pack import load_pack                               # noqa: E402
from moss_llm.smith.reverse import gate_character_kind            # noqa: E402

PACK = load_pack(nodes._PROMPTS_DIR)


# ── 桩服务端 ─────────────────────────────────────────────────

def answer(content: str, tokens: int = 10) -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"total_tokens": tokens}}


class StubPost:
    """替换 `nodes._post`：按脚本依次返回响应或抛 HTTP 400，并记录每次 payload。"""

    def __init__(self, script):
        self.script = list(script)
        self.payloads = []

    def __call__(self, endpoint, payload, key=None):
        self.payloads.append(json.loads(json.dumps(payload)))
        step = self.script.pop(0) if self.script else answer("{}")
        if isinstance(step, Exception):
            raise step
        return step


def make_node() -> nodes.MossPromptSmith:
    """建一个可直接调 `_chat` / `_run_*` 的节点实例（不起侧车）。"""
    node = nodes.MossPromptSmith()
    node._model_id = "test-model"
    node._degraded = []
    node._json_unsupported = False
    node._thinking_unsupported = False
    node._chat_notes = []
    node._img_notes = []
    return node


# ── 1. 类型闸 / 实体上限（纯函数） ───────────────────────────

class TestCaptionGuard(unittest.TestCase):
    def test_person_subject_is_untouched(self):
        inv = {"subjects": [{"role": "woman", "count": 1, "appearance": "blue coat",
                             "wardrobe": "blue coat", "gaze": "at camera",
                             "expression": "calm", "pose": "standing"}],
               "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(len(out["subjects"]), 1)
        self.assertEqual(out["environment"]["props"], [])
        self.assertEqual(notes, [])

    def test_object_subject_downgraded_and_age_dropped(self):
        inv = {"subjects": [{"kind": "object", "role": "coffee grinder",
                             "appearance": "brushed stainless steel", "age_years": 80,
                             "gender": "female"}],
               "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(out["subjects"], [])                       # 已移出 subjects
        self.assertEqual(len(out["environment"]["props"]), 1)
        self.assertIn("coffee grinder", out["environment"]["props"][0])
        self.assertIn("brushed stainless steel", out["environment"]["props"][0])
        joined = "\n".join(notes)
        self.assertIn("反推类型闸", joined)
        self.assertIn("反推年龄闸", joined)
        self.assertIn("80", joined)                                 # 点名丢掉的年龄

    def test_field_based_downgrade_when_kind_missing(self):
        """没有 kind 也能判：没有人物线索、只有 appearance 的 subject 降级。"""
        inv = {"subjects": [{"role": "window", "count": 1,
                             "appearance": "cracked glass and rain"}],
               "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(out["subjects"], [])
        self.assertEqual(len(out["environment"]["props"]), 1)
        self.assertIn("appearance", "\n".join(notes))

    def test_entity_caps_are_enforced_and_reported(self):
        many_people = [{"role": "person %d" % i, "gaze": "forward", "pose": "standing"}
                       for i in range(20)]
        inv = {"subjects": many_people,
               "environment": {"props": ["prop %d" % i for i in range(40)]},
               "materials": [{"surface": "x"}] * 20,
               "text": [{"text": "t"}] * 30}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(len(out["subjects"]), nodes._CAPTION_MAX_SUBJECTS)
        self.assertEqual(len(out["environment"]["props"]), nodes._CAPTION_MAX_PROPS)
        self.assertEqual(len(out["materials"]), nodes._CAPTION_MAX_MATERIALS)
        self.assertEqual(len(out["text"]), nodes._CAPTION_MAX_TEXT)
        joined = "\n".join(notes)
        self.assertIn("反推实体上限", joined)
        self.assertIn("%d→%d" % (20, nodes._CAPTION_MAX_SUBJECTS), joined)

    def test_arrays_at_cap_are_flagged_as_possible_truncation(self):
        """恰好达到上限也要出声：json_schema 的 maxItems 可能在语法层先静默截断。"""
        people = [{"role": "person %d" % i, "gaze": "forward", "pose": "standing"}
                  for i in range(nodes._CAPTION_MAX_SUBJECTS)]
        inv = {"subjects": people,
               "environment": {"props": ["p%d" % i
                                         for i in range(nodes._CAPTION_MAX_PROPS)]}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(len(out["subjects"]), nodes._CAPTION_MAX_SUBJECTS)
        self.assertEqual(len(out["environment"]["props"]), nodes._CAPTION_MAX_PROPS)
        joined = "\n".join(notes)
        self.assertIn("capped", joined)                          # 标记存在
        self.assertIn("达到上限", joined)
        self.assertIn("疑似重复/截断", joined)
        self.assertIn("请人工确认", joined)

    def test_schema_carries_the_same_caps(self):
        inv = nodes.CAPTION_INVENTORY_SCHEMA
        self.assertEqual(inv["properties"]["subjects"]["maxItems"],
                         nodes._CAPTION_MAX_SUBJECTS)
        self.assertEqual(inv["properties"]["environment"]["properties"]["props"]["maxItems"],
                         nodes._CAPTION_MAX_PROPS)
        self.assertEqual(inv["properties"]["materials"]["maxItems"],
                         nodes._CAPTION_MAX_MATERIALS)
        self.assertEqual(inv["properties"]["text"]["maxItems"], nodes._CAPTION_MAX_TEXT)
        one = nodes.CAPTION_ONESHOT_SCHEMA["properties"]
        self.assertEqual(one["must_keep"]["maxItems"], nodes._CAPTION_MAX_KEEP)
        self.assertEqual(one["visible_text"]["maxItems"], nodes._CAPTION_MAX_TEXT)
        self.assertEqual(set(nodes.CAPTION_PASS2_SCHEMA["required"]), {"caption"})
        json.dumps(nodes.CAPTION_INVENTORY_SCHEMA)                  # 仍是合法 JSON

    def test_shared_gate_is_the_same_function(self):
        """节点侧复用的是 M1a 的判据函数（只换了字段名），不是另写一套。"""
        kind, reason = gate_character_kind(
            "character", {"appearance": "glossy glaze"},
            person_fields=nodes._CAPTION_PERSON_CUES, object_fields=("appearance",))
        self.assertEqual(kind, "object")
        self.assertIn("appearance", reason)
        # 节点包装与它同向：同一份输入判成物体
        is_obj, why = nodes._caption_subject_is_object(
            {"role": "vase", "appearance": "glossy glaze"})
        self.assertTrue(is_obj)
        self.assertIn("appearance", why)

    def test_old_behavior_would_be_red(self):
        """反证：护栏之前的 inventory（物体留在 subjects）必然违反判据。"""
        def mislabeled(inv):
            return [s for s in inv.get("subjects") or []
                    if nodes._caption_subject_is_object(s)[0]]

        legacy = {"subjects": [{"role": "coffee grinder", "appearance": "steel",
                                "age_years": 80}]}
        self.assertEqual(len(mislabeled(legacy)), 1)
        fixed, _notes = nodes.guard_caption_json(legacy)
        self.assertEqual(mislabeled(fixed), [])                     # 修后必绿

    def test_unknown_kind_falls_through_the_field_gate(self):
        """未知 kind（降级档可能写 animal/people）不能跳过字段闸：物体必须进 props 且出声。"""
        inv = {"subjects": [
            {"kind": "animal", "appearance": "bronze horse statue"},
            {"kind": "people", "role": "mug", "count": 2,
             "appearance": "ceramic"},
        ], "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(out["subjects"], [])
        props = out["environment"]["props"]
        self.assertEqual(len(props), 2)
        self.assertTrue(any("bronze horse statue" in p for p in props))
        self.assertTrue(any("2 ×" in p for p in props))              # count 保留
        joined = "\n".join(notes)
        self.assertIn("未知 kind", joined)                           # 出声，不静默放行
        self.assertIn("反推类型闸", joined)

    def test_unknown_kind_with_person_cue_keeps_person(self):
        """未知 kind 但有 person cue（gaze/expression/pose）→ 字段闸保留 person，不误降级。"""
        inv = {"subjects": [{"kind": "people", "role": "woman", "count": 1,
                             "appearance": "red coat", "gaze": "at camera",
                             "expression": "calm", "pose": "standing"}],
               "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(len(out["subjects"]), 1)
        self.assertEqual(out["environment"]["props"], [])
        self.assertEqual(notes, [])

    def test_explicit_person_with_only_appearance_is_downgraded(self):
        """显式 kind=person 也受字段闸：只有 appearance、无任何 person cue → 降级并说明。"""
        inv = {"subjects": [{"kind": "person", "role": "woman",
                             "appearance": "shoulder-length black hair, pale skin"}],
               "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        self.assertEqual(out["subjects"], [])
        self.assertEqual(len(out["environment"]["props"]), 1)
        joined = "\n".join(notes)
        self.assertIn("反推类型闸", joined)
        self.assertIn("appearance", joined)                          # 原因点名字段

    def test_downgraded_count_is_preserved_and_reported(self):
        """count>1（两只杯子）是可见事实：写进 props 文案，并在 report 点明。"""
        inv = {"subjects": [{"kind": "object", "role": "mug", "count": 2,
                             "appearance": "ceramic"}],
               "environment": {"props": []}}
        out, notes = nodes.guard_caption_json(inv)
        prop = out["environment"]["props"][0]
        self.assertIn("2 ×", prop)
        self.assertIn("ceramic", prop)
        self.assertIn("count=2", "\n".join(notes))


# ── 2. `_chat`：结构化四级降级 + 重复保护 ────────────────────

class TestCaptionChat(unittest.TestCase):
    def setUp(self):
        self.node = make_node()
        self._old_post = nodes._post

    def tearDown(self):
        nodes._post = self._old_post

    def use_post(self, script):
        stub = StubPost(script)
        nodes._post = stub
        return stub

    def _chat(self, **kw):
        kw.setdefault("want_json", True)
        return self.node._chat("http://stub", "sys", "user", None, 0.2, 100, 42,
                               thinking=False, key=None, **kw)

    def test_json_request_sends_schema_repeat_and_thinking(self):
        stub = self.use_post([answer('{"caption":"x"}')])
        raw, usage = self._chat(schema=nodes.CAPTION_PASS2_SCHEMA)
        self.assertEqual(raw, '{"caption":"x"}')
        payload = stub.payloads[0]
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(payload["response_format"]["json_schema"]["schema"],
                         nodes.CAPTION_PASS2_SCHEMA)
        self.assertEqual(payload["repeat_penalty"], nodes._CAPTION_REPEAT_PENALTY)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})

    def test_schema_degrades_to_json_object(self):
        # 服务端不认 json_schema（重复参数它认）：先摘 repeat_penalty 仍 400，再降
        # 到 json_object。两级降级都要出声。
        stub = self.use_post([nodes._Http400("no schema"), nodes._Http400("no schema"),
                              answer('{"caption":"x"}')])
        self._chat(schema=nodes.CAPTION_PASS2_SCHEMA)
        self.assertEqual(len(stub.payloads), 3)
        self.assertEqual(stub.payloads[0]["response_format"]["type"], "json_schema")
        self.assertNotIn("repeat_penalty", stub.payloads[1])        # 先摘采样参数
        self.assertEqual(stub.payloads[2]["response_format"], {"type": "json_object"})
        self.assertEqual(stub.payloads[2]["repeat_penalty"],
                         nodes._CAPTION_REPEAT_PENALTY)
        self.assertTrue(any("不认 json_schema" in n for n in self.node._chat_notes))

    def test_four_level_degradation_and_flags(self):
        """schema → object → 无 response_format → 再摘 chat_template_kwargs。"""
        script = [nodes._Http400("400")] * 8 + [answer('{"caption":"x"}')]
        stub = self.use_post(script)
        self._chat(schema=nodes.CAPTION_PASS2_SCHEMA)
        kinds = [((p.get("response_format") or {}).get("type", "none"))
                 for p in stub.payloads]
        self.assertIn("json_schema", kinds)
        self.assertIn("json_object", kinds)
        self.assertIn("none", kinds)                                # 无 response_format 档
        self.assertNotIn("response_format", stub.payloads[-1])
        self.assertNotIn("chat_template_kwargs", stub.payloads[-1])
        self.assertTrue(self.node._json_unsupported)
        self.assertTrue(self.node._thinking_unsupported)

    def test_repeat_penalty_dropped_when_rejected(self):
        stub = self.use_post([nodes._Http400("no repeat_penalty"),
                              answer('{"caption":"x"}')])
        self._chat(schema=nodes.CAPTION_PASS2_SCHEMA)
        self.assertEqual(len(stub.payloads), 2)
        self.assertIn("repeat_penalty", stub.payloads[0])
        self.assertNotIn("repeat_penalty", stub.payloads[1])
        self.assertTrue(any("repeat_penalty" in n for n in self.node._chat_notes))

    def test_non_json_request_unchanged(self):
        stub = self.use_post([answer("plain text")])
        raw, _usage = self.node._chat("http://stub", "sys", "user", None, 0.7, 50, 1,
                                      want_json=False, thinking=False, key=None)
        self.assertEqual(raw, "plain text")
        payload = stub.payloads[0]
        self.assertNotIn("response_format", payload)
        self.assertNotIn("repeat_penalty", payload)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})


# ── 3. 两遍式 / 单遍重试（执行路径不回归） ───────────────────

class _RunCase(unittest.TestCase):
    def setUp(self):
        self.node = nodes.MossPromptSmith()
        self.node._chat_notes = []
        self.calls = []

    def fake_chat(self, *seq):
        """按脚本返回 (raw, usage)；记录 (schema, user)。"""
        queue = list(seq)

        def _chat(endpoint, system, user, image, temperature, max_tokens, seed,
                  want_json=True, thinking=False, key=None, schema=None):
            self.calls.append({"system": system, "user": user, "schema": schema,
                               "temperature": temperature, "thinking": thinking})
            item = queue.pop(0) if queue else ("", {})
            return item if isinstance(item, tuple) else (item, {})

        self.node._chat = _chat


class TestCaptionPaths(_RunCase):
    def test_two_pass_runs_pass1_then_pass2_with_guarded_inventory(self):
        inv = {"subjects": [{"kind": "object", "role": "window",
                             "appearance": "cracked glass", "age_years": 80},
                            {"kind": "person", "role": "woman", "gaze": "at camera",
                             "expression": "calm"}],
               "environment": {"props": []}}
        self.fake_chat((json.dumps(inv), {"total_tokens": 5}),
                       ('{"caption":"A woman stands by a window."}', {"total_tokens": 9}))
        report = []
        prompt, obj, usage = self.node._run_caption_two_pass(
            "http://stub", PACK, "SYS1", "USER", None, 0.1, 100, 1, False, None, report)
        self.assertEqual(prompt, "A woman stands by a window.")
        self.assertEqual(usage["total_tokens"], 9)
        self.assertEqual(len(self.calls), 2)
        self.assertIs(self.calls[0]["schema"], nodes.CAPTION_INVENTORY_SCHEMA)
        self.assertIs(self.calls[1]["schema"], nodes.CAPTION_PASS2_SCHEMA)
        # pass2 收到的是护栏后的 inventory：物体已到 props、subject 只剩人
        sent = json.loads(self.calls[1]["user"])
        self.assertEqual(len(sent["subjects"]), 1)
        self.assertIn("window — cracked glass", sent["environment"]["props"])
        joined = "\n".join(report)
        self.assertIn("反推类型闸", joined)
        self.assertIn("双遍：第一遍盘点", joined)

    def test_two_pass_invalid_pass1_falls_back_without_pass2(self):
        self.fake_chat(("not json at all", {}))
        report = []
        prompt, obj, _usage = self.node._run_caption_two_pass(
            "http://stub", PACK, "SYS", "USER", None, 0.1, 100, 1, False, None, report)
        self.assertEqual(prompt, "not json at all")
        self.assertIsNone(obj)
        self.assertEqual(len(self.calls), 1)                        # 没有再跑 pass2
        self.assertIn("第一遍盘点不是合法 JSON", "\n".join(report))

    def test_single_pass_retry_unchanged(self):
        self.fake_chat(("garbage {", {}), ('{"caption":"B"}', {"total_tokens": 3}))
        report = []
        prompt, obj, _usage = self.node._run_once(
            "http://stub", "SYS", "USER", None, 0.5, 100, 1, False, None, report, None)
        self.assertEqual(prompt, "B")
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.calls[1]["system"].endswith(nodes._JSON_RETRY))
        self.assertAlmostEqual(self.calls[1]["temperature"], 0.4)
        self.assertIn("首次返回不是合法 JSON", "\n".join(report))

    def test_single_pass_retry_failure_falls_back_to_text(self):
        self.fake_chat(("bad one", {}), ("bad two", {}))
        report = []
        prompt, obj, _usage = self.node._run_once(
            "http://stub", "SYS", "USER", None, 0.5, 100, 1, False, None, report, None)
        self.assertEqual(prompt, "bad two")
        self.assertIsNone(obj)
        self.assertIn("重试后仍不是合法 JSON", "\n".join(report))


if __name__ == "__main__":
    unittest.main(verbosity=2)
