#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""发布前**开源卫生**检查（唯一真相源在 `RELEASING.md` §2）。

为什么单独一条门：`test_contract.py` 的「仓库卫生」只管**代码能不能跑**（无绝对路径、无死代码），
它不回答「这一版**能不能公开**」——公开面多出三件事：
  ① 凭据会不会跟着出门（一旦进过 git 历史就擦不干净，判据只能是"别让它进来"）；
  ② 发出去的包里混了什么（`.comfyignore` 排错一个目录，用户装到的东西就不一样）；
  ③ 元数据里的占位符（`PublisherId = TODO`）——registry 上**发布后不可改**。

本脚本**只读**，不改任何文件。退出码表达成败（0 = 可以发）。
被 `tools/release.py` 的前置步骤调用，也可以单独跑：

    python tools/check_release_ready.py

⚠ 它扫的是 **git 跟踪的文件**，不是磁盘上的文件 —— 发出去的就是这一批。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, ".comfy_registry_token")
PASS: list[str] = []
FAIL: list[str] = []


def say(msg=""):
    print(msg)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK" if cond else "FAIL", name, ("  " + detail) if detail else ""))
    return cond


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def tracked() -> list[str]:
    out = (git("ls-files").stdout or "")
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


# --------------------------------------------------------------------------- S1 凭据
#: 常见凭据前缀。匹配到**位置**就报，但**绝不打印值**。
SECRET_PATTERNS = (
    ("GitHub PAT", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("GitHub fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("OpenAI/Anthropic 风格", re.compile(r"\bsk-[A-Za-z0-9\-_]{24,}")),
    ("Comfy registry PAT", re.compile(r"\bcomfyui-[A-Za-z0-9\-_]{20,}")),
)


def s1_secrets(files: list[str]) -> None:
    say("\nS1 凭据不跟着出门")
    # 已知真值（存在才读）：拿它做**精确子串**匹配，比任何形态猜测都可靠。
    known = ""
    if os.path.isfile(TOKEN_FILE):
        with io.open(TOKEN_FILE, encoding="utf-8-sig") as fh:
            known = fh.read().strip()
    hits = []
    for rel in files:
        if rel == ".comfy_registry_token":
            hits.append(rel + "（**凭据文件本身被跟踪**）")
            continue
        if not rel.endswith((".py", ".js", ".mjs", ".md", ".toml", ".yml", ".yaml",
                             ".json", ".txt", ".cfg", ".ini")):
            continue
        try:
            body = read(rel)
        except OSError:
            continue
        if known and known in body:
            hits.append(rel + "（含 token 字面量）")
            continue
        for label, rx in SECRET_PATTERNS:
            m = rx.search(body)
            if m:
                line = body[: m.start()].count("\n") + 1
                hits.append("%s:%d（%s 形态）" % (rel, line, label))
                break
    check("受控文件里无凭据字面量", not hits, "；".join(hits[:5]))

    # 凭据文件必须被忽略，且**不能**已被跟踪（tracked() 里已查过一次，这里查 ignore 规则）
    if os.path.isfile(TOKEN_FILE):
        r = git("check-ignore", ".comfy_registry_token")
        check("凭据文件命中忽略规则", r.returncode == 0)
    else:
        cut = "（本机没有该文件，跳过）"
        say("  [--] 凭据文件不存在%s" % cut)

    # 提交信息也不能带（历史一旦沾上就要 rewrite + force push）
    log = (git("log", "--format=%B", "-30").stdout or "")
    bad = []
    if known and known in log:
        bad.append("近 30 条提交信息里出现 token 字面量")
    for label, rx in SECRET_PATTERNS:
        if rx.search(log):
            bad.append("近 30 条提交信息里出现 %s 形态" % label)
    check("近 30 条提交信息无凭据", not bad, "；".join(bad))


# --------------------------------------------------------------------------- S2 绝对路径
#: 会被打进发布包的目录 / 文件（`.comfyignore` 之外的部分）。
SHIPPED_PREFIX = ("core/", "v3/", "web/", "examples/")
#: 文档不随 registry 包发，但**随仓库发**（README 逐篇链接 `docs/`）⇒ 同样是公开面。
#: 只认盘符路径（`I:/` · `C:\` · `D:\` …）；`<ComfyUI>` / `/path/to` 这类占位符不在此列。
#: 前一位不能是字母数字（`https://` 的冒号前是 `s`，不能误判成 `s:` 盘符）。
DOC_DRIVE_RE = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z]:[\\/]")


def s2_abs_paths(files: list[str]) -> None:
    say("\nS2 发布范围内无本机绝对路径")
    hits = []
    for rel in files:
        shipped = rel in ("__init__.py", "nodes.py") or rel.startswith(SHIPPED_PREFIX)
        if not shipped or not rel.endswith((".py", ".js", ".mjs", ".json")):
            continue
        for i, line in enumerate(read(rel).splitlines(), 1):
            if re.search(r"['\"][A-Za-z]:[\\/]", line):
                hits.append("%s:%d" % (rel, i))
    check("发布范围无硬编码盘符路径", not hits, "；".join(hits[:5]))

    # 文档按**磁盘**扫，不看 git 索引：`docs/07` 的泄漏正是在未跟踪时写下的。
    docs_dir = os.path.join(ROOT, "docs")
    names = sorted(n for n in os.listdir(docs_dir) if n.endswith(".md")) if os.path.isdir(docs_dir) else []
    doc_hits = []
    for name in names:
        rel = "docs/" + name
        for i, line in enumerate(read(rel).splitlines(), 1):
            if DOC_DRIVE_RE.search(line):
                doc_hits.append("%s:%d" % (rel, i))
    check("文档（docs/*.md）里无本机盘符路径", not doc_hits, "；".join(doc_hits[:6]))


# --------------------------------------------------------------------------- S3 临时/备份
JUNK_RE = re.compile(
    r"(\.bak($|[-.\d])|\.m\d+\.txt$|^node\.zip$|_sabotage|\.orig$|\.rej$|"
    r"^\.DS_Store$|^Thumbs\.db$|__pycache__|\.pyc$|^dist/|^build/)", re.I)


def s3_junk(files: list[str]) -> None:
    say("\nS3 没有临时/备份文件混进仓库")
    junk = [f for f in files if JUNK_RE.search(f)]
    check("受控文件里无临时件", not junk, "；".join(junk[:6]))


# --------------------------------------------------------------------------- S4 打包口径
def comfyignore_rules() -> list[str]:
    p = os.path.join(ROOT, ".comfyignore")
    if not os.path.isfile(p):
        return []
    return [ln.rstrip("\n") for ln in io.open(p, encoding="utf-8")]


def ignored(rel: str, rules: list[str]) -> bool:
    rel = rel.replace("\\", "/")
    for raw in rules:
        if not raw or raw.lstrip().startswith("#"):
            continue
        pat = raw.strip().lstrip("/")
        if raw.strip().endswith("/"):
            base = pat.rstrip("/")
            if rel == base or rel.startswith(base + "/"):
                return True
        elif rel == pat:
            return True
    return False


def s4_package(files: list[str]) -> None:
    say("\nS4 `.comfyignore` 口径：排开发件、不排运行期")
    rules = comfyignore_rules()
    if not check(".comfyignore 存在且有规则", bool(rules), "%d 行" % len(rules)):
        return
    must_exclude = ["tests/test_core.py", "tools/smoke_nodes.py", "docs/01-layout-and-panels.md",
                    ".github/workflows/ci.yml", "CHANGES.md", "AGENTS.md"]
    must_keep = ["__init__.py", "nodes.py", "core/layout.py", "core/sampling.py",
                 "core/editor.py", "core/presets.py",
                 "v3/_compat.py", "web/moss_sb_i18n.js", "web/moss_sb_editor.js",
                 "pyproject.toml", "LICENSE", "README.md", "requirements.txt", "THIRD-PARTY-NOTICES.md",
                 "examples/moss-storyboard-4panels-ui.json",
                 "examples/moss-layout-editor-poster.json",
                 "examples/moss-layout-editor-storyboard.json"]
    bad_ex = [f for f in must_exclude if not ignored(f, rules)]
    bad_keep = [f for f in must_keep if ignored(f, rules)]
    check("开发件全部被排除", not bad_ex, "漏排：%s" % bad_ex[:5] if bad_ex else "")
    check("运行期文件全部保留", not bad_keep, "误排：%s" % bad_keep[:5] if bad_keep else "")

    shipped = [f for f in files if not ignored(f, rules)]
    say("  包内 %d 个文件（受控 %d；运行期必留 %d 项）" % (len(shipped), len(files), len(must_keep)))
    # 🔴 未跟踪 = 根本不在 `git ls-files` 里 = 不进包，而 `.comfyignore` 检查看不出来
    #    （2026-10-24 实测：`core/editor.py` 等运行期文件未跟踪，门却 29/0 全绿）。
    missing = [f for f in must_keep if f not in shipped]
    check("运行期清单 %d 项全部在包内（未跟踪 ⇒ 不进包）" % len(must_keep), not missing,
          "缺：%s" % "；".join(missing[:5]) if missing else "")
    leaked = [f for f in shipped if JUNK_RE.search(f)]
    check("包内无临时件", not leaked, "；".join(leaked[:4]))
    # 🔴 中文文件名必须**判红**，不是警告：`comfy node pack` 按 latin-1 解路径，
    #    中文名会被**静默跳过**（2026-10-23 实测：`examples/MOSS 故事板宫格.json` 没进包，
    #    而 README 正叫用户去开它 ⇒ 用户装完找不到文件，两边都不报错）。
    cjk = [f for f in shipped if re.search(r"[\u4e00-\u9fff]", f)]
    check("包内路径全为 ASCII（中文名会被 registry 打包静默丢弃）", not cjk, "；".join(cjk[:5]))


# --------------------------------------------------------------------------- S5 版本
def s5_version() -> str:
    say("\nS5 版本号五处一致")
    def grab(rel, rx):
        try:
            m = re.search(rx, read(rel), re.M)
        except OSError:
            return None
        return m.group(1) if m else None

    found = {
        "__init__.py": grab("__init__.py", r'__version__\s*=\s*"([^"]+)"'),
        "pyproject.toml": grab("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        "CHANGES.md": grab("CHANGES.md", r"^## ([\d.]+)"),
        "README.md": grab("README.md", r"^\|\s*版本\s*\|\s*\*\*([\d.]+)\*\*"),
        "README_EN.md": grab("README_EN.md", r"^\|\s*Version\s*\|\s*\*\*([\d.]+)\*\*"),
    }
    vals = set(found.values())
    check("五处版本号一致", len(vals) == 1 and None not in vals, str(found))
    return sorted(v for v in found.values() if v)[0] if len(vals) == 1 else ""


# --------------------------------------------------------------------------- S6 元数据
def s6_metadata() -> None:
    say("\nS6 registry 元数据没有占位符")
    body = read("pyproject.toml")
    pub = re.search(r'^PublisherId\s*=\s*"([^"]*)"', body, re.M)
    val = pub.group(1) if pub else ""
    check("PublisherId 已填真实值", bool(val) and "TODO" not in val.upper() and "CHANGE" not in val.upper(),
          repr(val))
    check("project.name 不含 ComfyUI（registry 规范）",
          not re.search(r'^name\s*=\s*"[^"]*comfyui', body, re.M | re.I))
    check("license 用 { file = … } 形式", bool(re.search(r"^license\s*=\s*\{\s*file\s*=", body, re.M)))
    check("requires-comfyui 已声明", bool(re.search(r"^requires-comfyui\s*=", body, re.M)))
    for key in ("Icon", "Banner"):
        m = re.search(r'^%s\s*=\s*"([^"]+)"' % key, body, re.M)
        ok = bool(m) and m.group(1).startswith("https://")
        check("%s 是 https 直链" % key, ok, m.group(1) if m else "缺失")
    check("仓库元数据指向本仓",
          "ZenHG/ComfyUI-MOSS-Storyboard" in body and "TODO" not in body.split("[project.urls]")[1][:400])


# --------------------------------------------------------------------------- S7 体积
def s7_size(files: list[str]) -> None:
    say("\nS7 无异常大文件")
    big = []
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        mb = os.path.getsize(p) / 1048576.0
        if mb > 1.0 and not rel.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            big.append("%s（%.1f MB）" % (rel, mb))
    check("受控文件里没有 >1MB 的非图片", not big, "；".join(big[:4]))


# --------------------------------------------------------------------------- S8 发布说明
def s8_notes(version: str) -> None:
    say("\nS8 发布说明（两个渠道的用户可见文案）")
    p = os.path.join(ROOT, "RELEASE-NOTES.md")
    if not check("RELEASE-NOTES.md 存在", os.path.isfile(p)):
        return
    body = io.open(p, encoding="utf-8").read()
    sec = re.search(r"^## %s\b.*?(?=^## |\Z)" % re.escape(version), body, re.M | re.S)
    if not check("有本版（%s）一节" % version, bool(sec)):
        return
    seg = sec.group(0)
    if not check("本版一节是中英双语（含 <!-- EN --> 分隔）", "<!-- EN -->" in seg):
        return
    zh, en = seg.split("<!-- EN -->", 1)
    zh_n = len(zh.strip())
    en_n = len(en.strip())
    check("中文段 ≤700 字符", zh_n <= 700, "%d" % zh_n)
    check("英文段 ≤1700 字符", en_n <= 1700, "%d" % en_n)
    check("有升级动作（中）", "升级动作" in zh)
    check("有 Action required（英）", "Action required" in en)
    leaked = [w for w in ("CHANGES.md", "tests/", "tools/", "nodes.py", "docs/", "机检", "断言")
              if w in seg]
    check("用户可见文案里没有内部记号", not leaked, "；".join(leaked))


# --------------------------------------------------------------------------- S9 提交信息
# 提交信息一旦推上去，改动就只能靠改写历史（force push + 重建 tag），代价远高于改文件，
# 所以必须在这一步之前挡住。判据词用 \u 转义写：门禁源码本身也在公开面上，词不落明文。
#: 内部意图 / 内部沟通：仓库是给读者的，不是过程记录。
MSG_BANNED = (
    "\u5ba3\u4f20",
    "\u7528\u6237\u8bf4",
    "\u7528\u6237\u8981\u6c42",
    "\u7528\u6237\u6307\u51fa",
    "\u6211\u8ba9",
    "\u5e2e\u6211",
)
#: 本机痕迹：盘符路径与本机用户名（author 不算，这里只看提交信息正文）。
MSG_MACHINE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|Administrator", re.I)


def s9_commits() -> None:
    say("\nS9 提交信息（推上去就只能改写历史）")
    out = git("log", "-n", "50", "--format=%h%x1f%B%x1e").stdout or ""
    leaks, machine = [], []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, _, body = rec.partition("\x1f")
        sha = sha.strip()
        lines = body.splitlines()
        for word in MSG_BANNED:
            if word in body:
                ln = next((i for i, s in enumerate(lines, 1) if word in s), 0)
                leaks.append("%s:%d" % (sha, ln))
        if MSG_MACHINE.search(body):
            ln = next((i for i, s in enumerate(lines, 1) if MSG_MACHINE.search(s)), 0)
            machine.append("%s:%d" % (sha, ln))
    # 只报位置（短 hash:行号），不打印命中的原文 —— 日志本身也是公开面。
    check("最近 50 条提交信息无内部措辞", not leaks,
          "；".join(leaks[:5]) + ("（改写要 force push，先改再推）" if leaks else ""))
    check("最近 50 条提交信息无本机路径/用户名", not machine, "；".join(machine[:5]))


def main() -> int:
    say("MOSS Storyboard · 发布前开源卫生检查")
    say("=" * 64)
    files = tracked()
    if not check("仓库里有受控文件", bool(files), "%d 个" % len(files)):
        return 1
    s1_secrets(files)
    s2_abs_paths(files)
    s3_junk(files)
    s4_package(files)
    version = s5_version()
    s6_metadata()
    s7_size(files)
    if version:
        s8_notes(version)
    else:
        say("\nS8 跳过（版本号不一致，先修 S5）")
    s9_commits()

    say("\n" + "=" * 64)
    say("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        say("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
