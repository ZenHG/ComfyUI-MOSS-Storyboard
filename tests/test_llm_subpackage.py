# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""可选 LLM 子包的纯函数测试：零 GPU、零网络、零 llama-server。

跑法：`python tests/test_llm_subpackage.py`
覆盖的是**会静默坏掉**的那几处：提示词包 sha、JSON 容错解析、must_keep 逐字对账、
宫格段数对账、MTP 元数据判定、外部 API 端点拼接。真实出词在真机取证（见 docs/20 §五）。
"""

import hashlib
import importlib.machinery
import importlib.util
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + detail) if detail else ""))


def load_pkg(name="moss_sb_t"):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(ROOT, "__init__.py"))
    spec = importlib.util.spec_from_loader(name, loader,
                                           origin=os.path.join(ROOT, "__init__.py"), is_package=True)
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [ROOT]
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def write_gguf(path, kv):
    """造一个只有 KV 头的最小 GGUF（够判 nextn 元数据用，不含张量）。"""
    def enc(v):
        if isinstance(v, bool):
            return struct.pack("<I", 7) + struct.pack("<?", v)
        if isinstance(v, int):
            return struct.pack("<I", 4) + struct.pack("<I", v)
        if isinstance(v, float):
            return struct.pack("<I", 6) + struct.pack("<f", v)
        b = v.encode("utf-8")
        return struct.pack("<I", 8) + struct.pack("<Q", len(b)) + b

    buf = bytearray(b"GGUF") + struct.pack("<I", 3) + struct.pack("<Q", 0)
    buf += struct.pack("<Q", len(kv))
    for k, v in kv.items():
        kb = k.encode("utf-8")
        buf += struct.pack("<Q", len(kb)) + kb + enc(v)
    with open(path, "wb") as f:
        f.write(bytes(buf))


def main():
    load_pkg()
    import importlib
    nodes = importlib.import_module("moss_sb_t.moss_llm.nodes")
    pack_mod = importlib.import_module("moss_sb_t.moss_llm.pack")
    server = importlib.import_module("moss_sb_t.moss_llm.server")

    print("== 1. 提示词包：sha 校验与篡改检测 ==")
    pdir = os.path.join(ROOT, "moss_llm", "prompts")
    pack = pack_mod.load_pack(pdir)
    check("包能加载且 12 个文件", len(pack["files"]) == 12, str(len(pack["files"])))
    check("每个 sha 都是 64 位十六进制",
          all(len(f["sha256"]) == 64 for f in pack["files"]))
    check("每条都有许可与语言标注",
          all(f.get("license") and f.get("lang") for f in pack["files"]))
    # 篡改一份提示词（改到临时副本，不动仓库）
    with tempfile.TemporaryDirectory() as td:
        for f in os.listdir(pdir):
            with open(os.path.join(pdir, f), "rb") as src:
                data = src.read()
            if f == "caption.faithful.en.txt":
                data += b"\n# tampered\n"
            with open(os.path.join(td, f), "wb") as dst:
                dst.write(data)
        try:
            pack_mod.load_pack(td)
            check("改一个字必须判红", False, "居然加载成功了")
        except RuntimeError as e:
            check("改一个字必须判红（%s）" % "sha", "sha 与 manifest 不符" in str(e),
                  str(e).splitlines()[0][:60])
    try:
        pack_mod.load_pack(os.path.join(ROOT, "moss_llm"))
        check("缺 manifest 时报错带生成命令", False, "没报错")
    except RuntimeError as e:
        check("缺 manifest 时报错带生成命令", "--write" in str(e), str(e)[:60])

    print("\n== 2. 返回内容解析：围栏 / 前缀 / 容错 ==")
    check("剥代码围栏", nodes._strip_fences("```json\n{\"a\":1}\n```") == '{"a":1}')
    check("剥 English caption: 前缀",
          nodes._strip_fences("English caption: a cat") == "a cat")
    check("剥 <point> 噪声", "<point>" not in nodes._strip_fences("a <point>1,2</point> b"))
    obj, err = nodes._loads_lenient('prose {"caption":"x"} more prose')
    check("前后有废话时截取 JSON", obj == {"caption": "x"} and err is None, str(obj))
    obj, err = nodes._loads_lenient("完全没有大括号")
    check("彻底不是 JSON 时返回 None + 原因", obj is None and err is not None)

    print("\n== 3. 取提示词与对账 ==")
    check("优先取 rewritten_prompt",
          nodes._pick_prompt({"caption": "A", "rewritten_prompt": "B"}) == "B")
    check("反推取 caption", nodes._pick_prompt({"caption": "A"}) == "A")
    check("编辑指令取 instruction", nodes._pick_prompt({"instruction": "I"}) == "I")
    check("已知键全缺时返回空串（不把 JSON 对象当提示词）",
          nodes._pick_prompt({"x": 1}) == "")
    check("正文只有空白也算没有正文",
          nodes._pick_prompt({"rewritten_prompt": "   ", "notes": ["n"]}) == "")
    miss = nodes._missing_must_keep("画面里有一个白瓷杯", "白瓷杯；路上小心")
    check("must_keep 逐项对账只报没命中的", miss == ["路上小心"], str(miss))
    check("大小写/空白/引号归一后不误报",
          nodes._missing_must_keep('A red scarf on the chair, sign reads OPEN',
                                   'red scarf；"OPEN"；RED  SCARF') == [])
    check("重复项只核一次、不重复计数",
          nodes._missing_must_keep("甲", "甲；甲；甲") == [] and
          nodes._missing_must_keep("甲", "乙；乙；乙") == ["乙"])

    # 回归：模型返回 JSON 空壳（注入实测形态）时，绝不能把 JSON 本身当提示词。
    smith0 = nodes.MossPromptSmith()
    orig_chat = smith0._chat
    smith0._chat = lambda *a, **k: ('{"rewritten_prompt": "", "wh_ratio": "", '
                                    '"must_keep": [], "open_choices": [], "notes": []}',
                                    {"total_tokens": 1})
    try:
        prompt0, _obj0, _u0 = smith0._run_once("http://x", "s", "u", None, 0.7, 1024, 42,
                                               False, "k", [])
    finally:
        smith0._chat = orig_chat
    check("空 rewritten_prompt 不再被兜底成 JSON 字符串", prompt0 == "", repr(prompt0[:60]))
    check("分号两种写法都认",
          nodes._missing_must_keep("甲 乙", "甲；乙") == [] and
          nodes._missing_must_keep("甲", "甲;乙") == ["乙"])
    check("滤掉 null/None 占位",
          nodes._truthy_list([None, "real", "None", "", "null"]) == ["real"])

    smith = nodes.MossPromptSmith()
    out = smith._checks("[STYLE]: s\n[PANEL 1]: a\n[PANEL 2]: b", None, "扩写",
                        "moss_storyboard", 4, "")
    check("段数≠格数要点名", any("段数 2 ≠ 宫格数 4" in o for o in out), str(out))
    out = smith._checks("[STYLE]: s\n[PANEL 1]: a\n[PANEL 2]: b", None, "扩写",
                        "moss_storyboard", 2, "")
    check("段数=格数不出声", not any("段数" in o for o in out), str(out))

    print("\n== 4. MTP 元数据判定（不看文件名）==")
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "model.gguf")
        write_gguf(p, {"general.architecture": "qwen35", "qwen35.block_count": 33,
                       "qwen35.nextn_predict_layers": 1})
        check("有 nextn_predict_layers=1 → 判 1", server.gguf_mtp_layers(p) == 1)
        write_gguf(p, {"general.architecture": "qwen35", "qwen35.nextn_predict_layers": 0})
        check("取值为 0 → 判 0（不能只看键名）", server.gguf_mtp_layers(p) == 0)
        write_gguf(p, {"general.architecture": "llama", "llama.block_count": 16})
        check("无 nextn 键 → 判 0", server.gguf_mtp_layers(p) == 0)
        write_gguf(p, {"general.architecture": "qwen35", "tokenizer.ggml.tokens": "arr",
                       "qwen35.nextn_predict_layers": 2})
        check("混着 tokenizer 大表也能读到", server.gguf_mtp_layers(p) == 2)
        with open(p, "wb") as f:
            f.write(b"NOTGGUF....")
        check("不是 GGUF → 判 0 不抛", server.gguf_mtp_layers(p) == 0)

    print("\n== 4b. 侧车身份指纹（防「借用别人的模型」）==")
    a1 = server.instance_alias("x/moss-te.gguf", "y/mmproj-Q8_0.gguf", 8192)
    a2 = server.instance_alias("x/moss-te.gguf", "y/mmproj-Q8_0.gguf", 8192)
    a3 = server.instance_alias("x/qwen3vl.gguf", "y/mmproj-Q8_0.gguf", 8192)
    a4 = server.instance_alias("x/moss-te.gguf", "", 8192)
    check("同实例同指纹（才允许复用）", a1 == a2, "%s %s" % (a1, a2))
    check("换模型必须换指纹", a1 != a3)
    check("摘掉 mmproj 必须换指纹（纯文本臂不能复用带图的实例）", a1 != a4)
    check("指纹带可读前缀且定长", a1.startswith("moss-llm-") and len(a1) == 19, a1)

    print("\n== 5. 外部 API 通道：端点拼接与 Key 来源 ==")
    ep, key, src = nodes._api_setup("https://api.example.com/v1/", "glm-4v", "", "NO_SUCH_ENV")
    check("补全 chat/completions 且吃掉尾斜杠",
          ep == "https://api.example.com/v1/chat/completions", ep)
    check("没 Key 时说明是本地端点可留空", not key and "未提供" in src, src)
    os.environ["MOSS_TEST_KEY"] = "sk-abcdef"
    _ep, key, src = nodes._api_setup("x/v1", "m", "", "MOSS_TEST_KEY")
    check("从环境变量读 Key 并写明来源", key == "sk-abcdef" and "MOSS_TEST_KEY" in src, src)
    _ep, key, src = nodes._api_setup("x/v1", "m", "sk-inline", "")
    check("写在工作流里的 Key 要警告会随 JSON 分享", "随 JSON 分享" in src, src)
    check("已含 chat/completions 的 URL 不重复拼",
          nodes._api_setup("a/b/chat/completions", "m", "", "")[0] == "a/b/chat/completions")
    for bad, needle in ((("", "m"), "api_base"), (("x/v1", ""), "api_model")):
        try:
            nodes._api_setup(bad[0], bad[1], "", "")
            check("缺 %s 要报错" % needle, False, "没报错")
        except ValueError as e:
            check("缺 %s 要报错" % needle, needle in str(e), str(e)[:50])

    print("\n== 6. 任务边界（不静默改）==")
    #: 存档值一律 ASCII id（2026-10-30 晚场：初版把中文写进枚举，英文工作流里会出现中文）
    base = {"channel": "local", "dialect": "moss_image", "model": "x", "text": "",
            "length": "standard", "temperature": 0.2, "max_tokens": 512, "seed": 0}
    for task, needle in (("caption", "必须接"), ("expand", "必须填")):
        try:
            smith.smith(task=task, **base)
            check("边界报错（%s）" % needle, False, "没报错")
        except ValueError as e:
            check("边界报错含「%s」" % needle, needle in str(e), str(e).splitlines()[0][:50])

    print("\n== 6b. 旧稿枚举值归一（初版把中文写进了存档）==")
    try:
        smith.smith(task="反推", **base)
        check("旧值「反推」先归一再判边界", False, "没报错")
    except ValueError as e:
        check("旧值「反推」先归一成 caption，再报边界", "任务=caption" in str(e), str(e)[:60])
    all_ids = set(nodes._TASKS + nodes._CHANNELS + nodes._LENGTHS + nodes._LANGS
                  + nodes._RUNTIMES + nodes._ONOFF + nodes._MTP + nodes._KV
                  + [nodes._MMPROJ_AUTO, nodes._MMPROJ_NONE])
    bad = {k: v for k, v in nodes._LEGACY_VALUES.items() if v not in all_ids}
    check("旧中文值都映射到合法 id（%d 条）" % len(nodes._LEGACY_VALUES), not bad, str(bad))
    check("中文枚举都有归一映射（任务/通道/篇幅/语言/开关/运行态/MTP/mmproj）",
          all(k in nodes._LEGACY_VALUES for k in
              ["反推", "扩写", "编辑指令", "本地 GGUF", "外部 API", "简短", "标准", "详细",
               "自动", "中文", "英文", "关", "开", "用现有", "更新到最新版",
               "自动匹配", "(不使用/纯文本)"]))

    print("\n== 7. mmproj 配对：按投影宽度硬校验，无歧义才自动选 ==")
    def mk(path, arch=None, emb=None, proj=None):
        kv = {}
        if arch:
            kv["general.architecture"] = arch
            kv["%s.embedding_length" % arch] = emb
        if proj is not None:
            kv["clip.vision.projection_dim"] = proj
        write_gguf(path, kv)

    with tempfile.TemporaryDirectory() as td:
        m = os.path.join(td, "some-model-9b-q4_k_m.gguf")
        mk(m, "qwen35", 4096)
        check("目录里没有塔 → 返回空串", nodes._resolve_mmproj(m, {})[0] == "")
        t1 = os.path.join(td, "mmproj-only.gguf")
        mk(t1, proj=4096)
        check("唯一且宽度相符 → 自动选",
              nodes._resolve_mmproj(m, {})[0].endswith("mmproj-only.gguf"))
        t2 = os.path.join(td, "mmproj-other.gguf")
        mk(t2, proj=4096)
        try:
            nodes._resolve_mmproj(m, {})
            check("两个都能配上 → 报错让用户选", False, "没报错")
        except ValueError as e:
            check("两个都能配上 → 报错让用户选", "显式选" in str(e), str(e).splitlines()[0][:40])
        bad = os.path.join(td, "bad-tower.gguf")
        mk(bad, proj=2560)
        try:
            nodes._resolve_mmproj(m, {}, bad)
            check("显式选错宽度 → 报错带两边数字", False, "没报错")
        except ValueError as e:
            check("显式选错宽度 → 报错带两边数字",
                  "不配对" in str(e) and "2560" in str(e) and "4096" in str(e),
                  str(e).splitlines()[0][:50])
        os.remove(bad)
        os.remove(t2)
        os.remove(t1)
        t3 = os.path.join(td, "mmproj-f16.gguf")          # 同目录但宽度 2560
        mk(t3, proj=2560)
        try:
            nodes._resolve_mmproj(m, {})
            check("唯一的塔宽度不符 → 报错列候选宽度", False, "没报错")
        except ValueError as e:
            check("唯一的塔宽度不符 → 报错列候选宽度",
                  "2560" in str(e) and "4096" in str(e), str(e).splitlines()[0][:50])
        os.remove(t3)
        mk(os.path.join(td, "mmproj-f16.gguf"), proj=4096)
        mk(os.path.join(td, "some-model-9b.mmproj-q8_0.gguf"), proj=4096)
        check("多个候选时同名权重优先（不是按字母序猜）",
              nodes._resolve_mmproj(m, {})[0].endswith("some-model-9b.mmproj-q8_0.gguf"),
              nodes._resolve_mmproj(m, {})[0])

    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        m = os.path.join(td1, "moss-te-6.6g.gguf")
        mk(m, "qwen35", 4096)
        t = os.path.join(td2, "Qwen3.5-9B.mmproj-Q8_0.gguf")
        mk(t, proj=4096)
        got, _ = nodes._resolve_mmproj(m, {"Qwen3.5-9B.mmproj-Q8_0.gguf": t})
        check("模型在 A 目录、塔在 B 目录（扫描表里）→ 仍能配上", got == t, got)
        check("配对成功后不留「未验证」告警", nodes._check_pair(m, t) == "")

    print("\n== 8. 运行时装包：按平台/后端挑官方预编译包（纯函数）==")
    ri = importlib.import_module("moss_sb_t.moss_llm.runtime_install")
    tag = "b12018"
    assets = [{"name": n, "browser_download_url": "https://example.invalid/" + n}
              for n in (
                  "llama-b12018-bin-win-cpu-x64.zip",
                  "llama-b12018-bin-win-cuda-12.4-x64.zip",
                  "llama-b12018-bin-win-cuda-13.4-x64.zip",
                  "llama-b12018-bin-win-cuda-14.0-x64.zip",
                  "cudart-llama-bin-win-cuda-12.4-x64.zip",
                  "cudart-llama-bin-win-cuda-13.4-x64.zip",
                  "llama-b12018-bin-win-vulkan-x64.zip",
                  "llama-b12018-bin-win-rocm-10.0-x64.zip",
                  "llama-b12018-bin-win-sycl-x64.zip",
                  "llama-b12018-bin-ubuntu-x64.tar.gz",
                  "llama-b12018-bin-ubuntu-cuda-12.8-x64.tar.gz",
                  "cudart-llama-b12018-bin-ubuntu-cuda-12.8-x64.tar.gz",
                  "llama-b12018-bin-ubuntu-arm64.tar.gz",
                  "llama-b12018-bin-macos-arm64.tar.gz",
                  "llama-b12018-bin-macos-x64.tar.gz",
              )]
    pick = lambda plat, be, mc: [x[1] for x in ri._pick(assets, tag, plat, be, mc)]  # noqa: E731
    check("Windows+N 卡按驱动上限选 CUDA（不越级到 14.0）",
          pick("windows-x64", "cuda", (13, 9))[0].endswith("cuda-13.4-x64.zip"), str(pick("windows-x64", "cuda", (13, 9))))
    check("老驱动降到 CUDA 12.4", pick("windows-x64", "cuda", (12, 9))[0].endswith("cuda-12.4-x64.zip"))
    check("驱动未知时选最低 CUDA（兼容老卡）", pick("windows-x64", "cuda", None)[0].endswith("cuda-12.4-x64.zip"))
    check("CUDA 主包自动配同版本 cudart",
          any("cudart" in n and "13.4" in n for n in pick("windows-x64", "cuda", (13, 9))),
          str(pick("windows-x64", "cuda", (13, 9))))
    check("AMD/Intel 走 Vulkan", pick("windows-x64", "vulkan", None)[0].endswith("vulkan-x64.zip"))
    check("AMD 显式要 ROCm 时给 ROCm 包", pick("windows-x64", "rocm", None)[0].endswith("rocm-10.0-x64.zip"))
    check("无卡走 CPU 包", pick("windows-x64", "cpu", None)[0].endswith("win-cpu-x64.zip"))
    check("Linux CPU 包没有 backend token 也要认",
          pick("linux-x64", "cpu", None) == ["llama-b12018-bin-ubuntu-x64.tar.gz"])
    check("Linux CUDA 用 .tar.gz 并配 cudart",
          len(pick("linux-x64", "cuda", (13, 9))) == 2, str(pick("linux-x64", "cuda", (13, 9))))
    check("macOS 只有 Metal 包（没有 backend token）",
          pick("macos-arm64", "metal", None) == ["llama-b12018-bin-macos-arm64.tar.gz"])
    check("build 号不符 → 一个都不挑", ri._pick(assets, "b99999", "windows-x64", "cuda", None) == [])
    check("型号表：RTX 50/20 → CUDA 13（Turing 起）；GTX 10 → CUDA 12；非 N 卡 → None",
          (ri.cuda_cap_from_name("NVIDIA GeForce RTX 5070") == (13, 9)
           and ri.cuda_cap_from_name("NVIDIA GeForce RTX 2080 Ti") == (13, 9)
           and ri.cuda_cap_from_name("NVIDIA GeForce GTX 1080") == (12, 9)
           and ri.cuda_cap_from_name("Intel Arc A770") is None))

    print("\n== 9. 下载兜底：镜像顺序 + sha256 校验 ==")
    url = "https://github.com/x/y/releases/download/b1/a.zip"
    old_env = {k: os.environ.get(k) for k in ("MOSS_LLM_MIRROR", "MOSS_LLM_MIRRORS")}
    try:
        os.environ["MOSS_LLM_MIRROR"] = "https://m1.example/"
        os.environ["MOSS_LLM_MIRRORS"] = "https://m2.example,https://m3.example/"
        srcs = ri._sources(url)
        check("来源顺序：用户镜像 > 直连 > 自定义镜像链",
              srcs == ["https://m1.example/" + url, url,
                       "https://m2.example/" + url, "https://m3.example/" + url], str(srcs))
        os.environ.pop("MOSS_LLM_MIRRORS")
        check("没配 MOSS_LLM_MIRRORS 时用内置镜像链",
              ri._sources(url)[2].startswith(ri._MIRRORS[0]))
        os.environ.pop("MOSS_LLM_MIRROR")
        check("无摘要时不启用第三方镜像", ri._sources(url, allow_mirror=False) == [url])
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    with tempfile.TemporaryDirectory() as td:
        bad = os.path.join(td, "bad.bin")
        open(bad, "wb").write(b"bad")
        good = os.path.join(td, "good.bin")
        open(good, "wb").write(b"good")
        want = hashlib.sha256(b"good").hexdigest()
        dest = os.path.join(td, "out.bin")
        _p, src = ri._download(url, dest, 4, want,
                               sources=[Path(bad).as_uri(), Path(good).as_uri()])
        check("坏源被丢弃、好源通过并落盘",
              open(dest, "rb").read() == b"good" and src == "file", src)
        _p, src = ri._download(url, dest, 4, want, sources=[])
        check("已校验的缓存直接复用", src == "缓存", src)
        try:
            ri._download(url, os.path.join(td, "x.bin"), 4, want,
                         sources=[Path(bad).as_uri()])
            check("所有源失败 → 显性报错", False, "没报错")
        except RuntimeError as e:
            check("所有源失败 → 显性报错", "所有来源都不可用" in str(e),
                  str(e).splitlines()[0])

    print("\n== 10. 合流：侧车启动命令是纯函数（参数拼错只有真机才炸）==")
    srv = server.LlamaServer(model="m.gguf", ctx=8192)
    cmd, notes = srv.build_cmd("/x/llama-server", flags=set())
    check("带上 fit / fit-target（显存适配）",
          "--fit" in cmd and "--fit-target" in cmd, " ".join(cmd))
    check("带上 KV cache 量化（q8_0 省一半显存）",
          "--cache-type-k" in cmd and "q8_0" in cmd)
    check("带上 ubatch 与 parallel", "--ubatch-size" in cmd and "--parallel" in cmd)
    check("默认**不设**服务端口令（否则 /tokenize 回 401，真实 token 数被静默降级成估算）",
          "--api-key" not in cmd)
    check("alias 与实例指纹同源（端口占用时据此判能不能复用）",
          "--alias" in cmd and srv.alias() in cmd)

    cmd2, notes2 = srv.build_cmd("/x/llama-server", flags={"--jinja", "--ctx-size"})
    check("--help 探测不到的开关一律不加（老版本会启动即退出）",
          "--fit" not in cmd2 and "--cache-type-k" not in cmd2, " ".join(cmd2))
    check("不支持 fit 时出声，不静默少一层保护",
          any("--fit" in n for n in notes2), str(notes2))

    with tempfile.TemporaryDirectory() as td:
        mp = os.path.join(td, "mmproj-Q8_0.gguf")
        with open(mp, "wb") as f:
            f.write(b"GGUF")
        c3, _n = server.LlamaServer(model="m.gguf", mmproj=mp).build_cmd(
            "/x/llama-server", flags=set())
        check("mmproj 文件存在才加 --mmproj", "--mmproj" in c3 and mp in c3)
        c4, _n = srv.build_cmd("/x/llama-server", flags=set())
        check("没给 mmproj 就不加（纯文本臂不会被塞一个塔）", "--mmproj" not in c4)

    check("mtp=off 时不开投机解码",
          server.LlamaServer(model="m.gguf", mtp="off").mtp_enabled() is False)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "m.gguf")
        write_gguf(p, {"general.architecture": "qwen35", "qwen35.nextn_predict_layers": 1})
        check("mtp=auto 读元数据判（不看文件名）",
              server.LlamaServer(model=p).mtp_enabled() is True)

    print("\n== 11. 合流：图像预处理只有一份（张量入口与文件入口同一套纪律）==")
    try:
        import numpy as np
    except ImportError:                                   # pragma: no cover
        np = None
    if np is None:
        check("跳过：没有 numpy（torch 也没装时的最小环境）", True)
    else:
        from moss_sb_t.moss_llm import vision as V
        prep = V.prepare_tensor(np.zeros((1, 1024, 2048, 3), dtype="float32"))
        check("张量入口缩到最长边 1536（以前是原尺寸直传，白烧一两千 token）",
              (prep.width, prep.height) == (1536, 768), "%sx%s" % (prep.width, prep.height))
        check("编成 JPEG q95 4:4:4（不是原尺寸 PNG）",
              prep.data_url.startswith("data:image/jpeg;base64,"), prep.data_url[:30])
        check("缩放这件事写进 notes（不静默改输入）",
              any("缩放" in n for n in prep.notes), str(prep.notes))
        with tempfile.TemporaryDirectory() as td:
            from PIL import Image
            pth = os.path.join(td, "a.png")
            Image.new("RGBA", (400, 200), (255, 0, 0, 0)).save(pth)
            pf = V.prepare(pth)
            check("文件入口把透明合成到白底（直接丢 alpha 会变黑底）",
                  any("白底" in n for n in pf.notes), str(pf.notes))
            check("文件入口同样出 JPEG", pf.data_url.startswith("data:image/jpeg;base64,"))

    print("\n== 12. 合流：compile 任务（零模型、确定性、可断言）==")
    ex_dir = os.path.join(ROOT, "moss_llm", "smith", "examples")
    with open(os.path.join(ex_dir, "01-portrait-photoreal.json"), encoding="utf-8") as f:
        ir_text = f.read()
    # channel=api 且 api_base 为空：真去调模型会立刻报错。能出结果就证明没走模型。
    base_c = {"task": "compile", "channel": "api", "api_base": "",
              "dialect": "moss_image", "model": "(未找到 GGUF 模型)", "text": ir_text,
              "length": "standard", "temperature": 0.2, "max_tokens": 512, "seed": 0}
    out, rep = smith.smith(target="moss-image", **base_c)
    check("没有权重、没有 api_base 也能出提示词（零模型）", bool(out.strip()))
    check("报告写明「未调用模型」", "未调用模型" in rep, rep.splitlines()[0])
    check("报告带不变式结论（M1–M6）", "不变式" in rep, rep.splitlines()[1])
    check("确定性：同 IR 两次编译逐字节相同", smith.smith(target="moss-image", **base_c)[0] == out)

    out2, rep2 = smith.smith(target="sdxl", **base_c)
    check("同一 IR 换后端 → 换方言（不是原样复制）", out2 != out and bool(out2.strip()))

    with open(os.path.join(ex_dir, "05-h3-t2va.json"), encoding="utf-8") as f:
        h3_ir = f.read()
    out3, rep3 = smith.smith(target="minimax-h3", **{**base_c, "text": h3_ir})
    check("视频后端也走同一条路（timeline 是 IR 的一等字段）",
          "[Shot 1]" in out3, out3[:40])

    for bad, needle, tag in [
        ("{}", "entities", "缺 entities"),
        ("", "空的", "空输入"),
        ("任务：做个海报", "认不出", "不是结构化的"),
    ]:
        try:
            smith.smith(target="moss-image", **{**base_c, "text": bad})
            check("坏 IR 要报错（%s）" % tag, False, "没报错")
        except ValueError as e:
            check("坏 IR 要报错（%s）" % tag, needle in str(e), str(e).splitlines()[0][:70])
    try:
        smith.smith(target="nope", **base_c)
        check("未知后端要报错并列出可选项", False, "没报错")
    except ValueError as e:
        check("未知后端要报错并列出可选项", "moss-image" in str(e), str(e).splitlines()[0][:70])

    reg_mod = importlib.import_module("moss_sb_t.moss_llm.smith.registry")
    names = set(reg_mod.Registry.load().names())
    extra = [t for t in nodes._TARGETS if t not in names]
    missing = [t for t in sorted(names) if t not in nodes._TARGETS]
    check("节点的 target 列表与 registry 双向一致（%d 个，镜像不许漂移）"
          % len(nodes._TARGETS), not extra and not missing,
          "多=%s 漏=%s" % (extra, missing))
    check("compile 在任务枚举里", "compile" in nodes._TASKS)
    check("编译任务的中文旧值也有归一映射", nodes._LEGACY_VALUES.get("编译") == "compile")

    print("\n== 13. 合流：llama-server 只有一个查找入口，转发不是副本 ==")
    paths_mod = importlib.import_module("moss_sb_t.moss_llm.smith.paths")
    llm_mod = importlib.import_module("moss_sb_t.moss_llm.smith.llm")
    check("smith.llm.LlamaServer 就是父包那个类（不是又抄了一份）",
          llm_mod.LlamaServer is server.LlamaServer)
    with tempfile.TemporaryDirectory() as td:
        exe = os.path.join(td, "llama-server.exe" if os.name == "nt" else "llama-server")
        with open(exe, "wb") as f:
            f.write(b"x")
        old = os.environ.get("MOSS_LLM_SERVER")
        os.environ["MOSS_LLM_SERVER"] = exe
        try:
            check("smith.paths 与父包找到同一个可执行文件",
                  paths_mod.find_llama_server() == exe and server.find_server_exe() == exe,
                  paths_mod.find_llama_server())
            check("支持性探测走父包（同一个带缓存的 --help 结果）",
                  paths_mod.supports_flag(exe, "--fit") is False)   # 假 exe 问不出帮助
        finally:
            if old is None:
                os.environ.pop("MOSS_LLM_SERVER", None)
            else:
                os.environ["MOSS_LLM_SERVER"] = old

    print("\n== 14 · 运行时目录可配（MOSS_LLM_RUNTIME_DIR） ==")
    old_rt = os.environ.get("MOSS_LLM_RUNTIME_DIR")
    try:
        os.environ["MOSS_LLM_RUNTIME_DIR"] = os.path.join(tempfile.gettempdir(), "moss-rt-test")
        check("运行时目录跟随环境变量",
              str(ri._runtime_dir()).endswith("moss-rt-test"), str(ri._runtime_dir()))
    finally:
        if old_rt is None:
            os.environ.pop("MOSS_LLM_RUNTIME_DIR", None)
        else:
            os.environ["MOSS_LLM_RUNTIME_DIR"] = old_rt

    print("\n== 15. 端口占用自动顺延 ==")
    orig_port_free = server.port_free
    try:
        server.port_free = lambda p: p not in (8091, 8092)
        check("被占用的端口顺延到下一个空闲端口", server.pick_free_port(8091) == 8093,
              str(server.pick_free_port(8091)))
        server.port_free = lambda p: False
        check("连续 20 个都占用时明确放弃（不抢）", server.pick_free_port(8091) is None)
    finally:
        server.port_free = orig_port_free

    print("\n== PE 改写器模式（模型目录自带 system_prompt.txt） ==")
    import tempfile as _tf
    pe_text = ("You are an Image Prompt Rewriting Expert.\\n"
               "Return JSON with fields rewritten_prompt and wh_ratio.")
    with _tf.TemporaryDirectory() as td:
        gguf = Path(td) / "pe_t2i_heretic-Q4_K_M.gguf"
        gguf.write_bytes(b"x")
        (Path(td) / "system_prompt.txt").write_text(pe_text, encoding="utf-8")
        got, path = nodes._pe_system_prompt(str(gguf))
        check("PE：pe_t2i 模型同目录 system_prompt.txt 命中", got == pe_text and path.endswith("system_prompt.txt"))

        renamed = Path(td) / "some_other_model.gguf"
        renamed.write_bytes(b"x")
        got2, _ = nodes._pe_system_prompt(str(renamed))
        check("PE：模型改名后仍按内容命中", got2 == pe_text)

        (Path(td) / "system_prompt.txt").write_text("just a normal note", encoding="utf-8")
        got3, path3 = nodes._pe_system_prompt(str(gguf))
        check("PE：普通 txt 不像 PE 时不命中", got3 == "" and path3 == "")

        (Path(td) / "system_prompt.txt").unlink()
        got4, path4 = nodes._pe_system_prompt(str(gguf))
        check("PE：没有 system_prompt.txt 时不命中", got4 == "" and path4 == "")

        (Path(td) / "system_prompt.txt").write_text(pe_text, encoding="utf-8")
        got5, _ = nodes._pe_system_for("expand", str(gguf))
        got6, _ = nodes._pe_system_for("caption", str(gguf))
        check("PE：expand 启用、caption 不启用", got5 == pe_text and got6 == "")

        (Path(td) / "pe_t2i_heretic-Q4_K_M.system_prompt.txt").write_text(pe_text, encoding="utf-8")
        (Path(td) / "system_prompt.txt").unlink()
        got7, path7 = nodes._pe_system_prompt(str(gguf))
        check("PE：<stem>.system_prompt.txt 命名命中", got7 == pe_text and path7.endswith(".system_prompt.txt"))

    got_pick = nodes._pick_prompt({"rewritten_prompt": "x", "wh_ratio": "3:2"})
    check("PE JSON：_pick_prompt 取到 rewritten_prompt", got_pick == "x", got_pick)

    # PE-I2I：T2I 与 I2I 的 system prompt 必须按任务分开，不许交叉误用。
    i2i_text = ("Rewrite the user's image-edit instruction. Return JSON with fields "
                "rewritten_prompt, wh_ratio and ratio_follow. Set ratio_follow to "
                "\"<image1>\" when the output follows an input image.")
    with _tf.TemporaryDirectory() as td:
        gguf = Path(td) / "pe_i2i_heretic-Q4_K_M.gguf"
        gguf.write_bytes(b"x")
        (Path(td) / "pe_i2i.system_prompt.txt").write_text(i2i_text, encoding="utf-8")
        g_edit, p_edit = nodes._pe_system_for("edit", str(gguf))
        g_exp, _ = nodes._pe_system_for("expand", str(gguf))
        check("PE-I2I：edit 命中 I2I 提示词", g_edit == i2i_text and p_edit.endswith("pe_i2i.system_prompt.txt"))
        check("PE-I2I：expand 不会误用 I2I 提示词", g_exp == "", g_exp[:40])

        (Path(td) / "pe_t2i.system_prompt.txt").write_text(pe_text, encoding="utf-8")
        g_edit2, p_edit2 = nodes._pe_system_for("edit", str(gguf))
        g_exp2, p_exp2 = nodes._pe_system_for("expand", str(gguf))
        check("PE：同目录两份提示词时 edit 取 I2I", g_edit2 == i2i_text and p_edit2.endswith("pe_i2i.system_prompt.txt"))
        check("PE：同目录两份提示词时 expand 取 T2I", g_exp2 == pe_text and p_exp2.endswith("pe_t2i.system_prompt.txt"))

        (Path(td) / "pe_i2i.system_prompt.txt").unlink()
        (Path(td) / "pe_t2i.system_prompt.txt").unlink()
        g_edit3, _ = nodes._pe_system_for("edit", str(gguf))
        check("PE：没有提示词时 edit 走原路径", g_edit3 == "")

    check("PE：I2I 特征（ratio_follow）只被 I2I 判定认",
          nodes._looks_like_pe_i2i(i2i_text) and not nodes._looks_like_pe_t2i(i2i_text))

    print("\n== 13. 两处出声：PE 绕过参数 / 接图无视觉塔 ==")
    import contextlib
    from unittest import mock

    def smith_report(task, image, mmproj, pe_text, mmproj_path, **overrides):
        with tempfile.TemporaryDirectory() as td:
            gguf = os.path.join(td, "stub.gguf")
            with open(gguf, "wb") as f:
                f.write(b"x")
            key = "stub.gguf"
            patches = [
                mock.patch.object(nodes, "_scan_gguf", lambda: ({key: gguf}, {})),
                mock.patch.object(nodes, "_resolve_mmproj", lambda *a: (mmproj_path, "")),
                mock.patch.object(nodes, "_pe_system_for",
                                  lambda t, m: (pe_text, "stub.txt") if pe_text else ("", "")),
                mock.patch.object(nodes.GgufLocalServer, "acquire",
                                  classmethod(lambda cls, *a, **k: ("http://127.0.0.1:0", []))),
                mock.patch.object(nodes.MossPromptSmith, "_chat",
                                  lambda self, *a, **k: ('{"rewritten_prompt":"x"}', {"total_tokens": 1})),
            ]
            for name, value in overrides.items():
                patches.append(mock.patch.object(nodes, name, value))
            with contextlib.ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                return nodes.MossPromptSmith().smith(
                    task=task, channel="local", dialect="moss_image", model=key,
                    text="把背景换成雪夜", length="standard", temperature=0.2,
                    max_tokens=256, seed=0, image=image, mmproj=mmproj,
                    release_after_run=False)[1]

    rep_pe = smith_report("edit", object(), "auto", "PE", "/fake/stub.mmproj.gguf",
                          _PE_BYPASSED=("SENTINEL_PE",))
    check("PE 分支真的把绕过参数写进 report（哨兵反证）",
          "PE 绕过参数：SENTINEL_PE" in rep_pe, rep_pe[-70:])
    rep_vis = smith_report("expand", object(), "auto", "", "", _VISION_HINT="SENTINEL_V")
    check("接图无视觉塔时真的出声（哨兵反证）", "SENTINEL_V" in rep_vis, rep_vis[-70:])
    rep_none = smith_report("expand", object(), nodes._MMPROJ_NONE, "", "")
    check("显式选「不使用」时不误报", nodes._VISION_HINT not in rep_none, rep_none[-70:])
    check("常量写全了被绕过的参数",
          nodes._PE_BYPASSED == ("dialect", "length", "must_keep", "panels", "style_lock"),
          str(nodes._PE_BYPASSED))
    check("视觉塔提示给全三条路",
          all(k in nodes._VISION_HINT for k in ("①", "②", "③")), nodes._VISION_HINT[:40])

    # ---- 协议层：端点判定 + 三套 body/read 收口（`moss_llm/protocols.py`）
    pr = nodes._proto
    cases = [("https://api.deepseek.com/v1", "openai", "https://api.deepseek.com/v1/chat/completions"),
             ("https://api.deepseek.com/anthropic", "anthropic",
              "https://api.deepseek.com/anthropic/v1/messages"),
             ("https://generativelanguage.googleapis.com/v1beta", "gemini",
              "https://generativelanguage.googleapis.com/v1beta/models/gm:generateContent")]
    for base, want_proto, want_ep in cases:
        ep = pr.endpoint(base, "gm")
        check("协议按端点判定：%s" % want_proto,
              pr.detect(ep) == want_proto and ep == want_ep, "%s | %s" % (ep, pr.detect(ep)))
    check("已写全的端点不重复拼",
          pr.endpoint("https://x/y/chat/completions") == "https://x/y/chat/completions")
    check("三种协议都能取到正文",
          pr.read(pr.OPENAI, {"choices": [{"message": {"content": "a"}}]})[0] == "a"
          and pr.read(pr.ANTHROPIC, {"content": [{"type": "text", "text": "b"}]})[0] == "b"
          and pr.read(pr.GEMINI, {"candidates": [{"content": {"parts": [{"text": "c"}]}}]})[0] == "c")
    try:
        pr.read(pr.GEMINI, {"promptFeedback": {"blockReason": "SAFETY"}})
        check("Gemini 无候选要出声", False, "没报错")
    except RuntimeError as e:
        check("Gemini 无候选要出声", "SAFETY" in str(e), str(e)[:40])


    print("\n结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
