#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""发布前**开源卫生**检查（规则由维护者本地发布文档维护）。

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

import getpass
import io
import os
import re
import struct
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


#: 所有**文本类**受控文件的扩展名。S2 / S15 用同一份，避免两处口径分叉。
TEXT_EXT = (".py", ".js", ".mjs", ".json", ".md", ".txt", ".yml", ".yaml",
            ".toml", ".cfg", ".ini", ".csv", ".html", ".css")


def is_text(rel: str) -> bool:
    return rel.lower().endswith(TEXT_EXT)


def local_identities() -> list[str]:
    """本机用户名 / 主机名。CI 与通用容器上的值不参与匹配（否则每台机器结论不同）。

    只用来回答一个问题：**这句"本机指纹"是不是被写进公开文件了**。
    """
    cands = [os.environ.get("USERNAME") or "", os.environ.get("USER") or "",
             os.environ.get("COMPUTERNAME") or ""]
    try:
        cands.append(getpass.getuser())
    except Exception:
        pass
    generic = {"root", "user", "runner", "admin", "administrator", "ubuntu", "node"}
    out = []
    for c in cands:
        c = (c or "").strip()
        if len(c) >= 4 and c.lower() not in generic and c not in out:
            out.append(c)
    return out


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
#: 前一位不能是字母数字（`https://` 的冒号前是 `s`，不能误判成 `s:` 盘符），
#: 也不能是 `:` `?` `(` —— 否则正则里的 `(?i:\b` 会被当成 `i:` 盘符（2026-10-28 踩过）。
DOC_DRIVE_RE = re.compile(r"(?<![A-Za-z0-9+.\-:(?])[A-Za-z]:[\\/]")
#: 允许出现在公开面的盘符路径：**通用安装位置 / 测试夹具**，不指向任何人的机器。
#: 每加一条都要能回答「为什么它不是指纹」——别把它当擦屁股纸。
DRIVE_ALLOW = (
    "C:/Program Files/nodejs/node.exe",   # Node 默认安装位置，官方文档同款兜底
    "c:\\\\tmp",                          # JSON 转义夹具里的示意值
)


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

    # 🔴 第二个公开面：**GitHub 仓的全部受控文件**。上面两条只管 registry 包范围
    #    （`core/ v3/ web/ examples/` + `docs/*.md`），而 `tests/` `tools/` `CHANGES.md`
    #    `.github/` `pyproject.toml` 同样是公开的 —— 2026-10-28 才发现这些从未被扫过。
    ids = local_identities()
    github_hits = []
    for rel in files:
        if not is_text(rel) or rel == SELF:      # 规则表本身要写出允许的路径，见 S15
            continue
        for i, line in enumerate(read(rel).splitlines(), 1):
            if DOC_DRIVE_RE.search(line) or any(n in line for n in ids):
                if any(a in line for a in DRIVE_ALLOW):
                    continue
                github_hits.append("%s:%d" % (rel, i))
                break
    check("GitHub 公开面（全部 %d 个文本文件）无盘符路径/本机用户名" % len(files),
          not github_hits, "；".join(github_hits[:6]))


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
    must_exclude = ["tests/test_core.py", "tools/smoke_nodes.py", "docs/10-contract.md",
                    ".github/workflows/ci.yml", "CHANGES.md", "RELEASING.md"]
    must_keep = ["__init__.py", "nodes.py", "core/layout.py", "core/sampling.py",
                 "core/editor.py", "core/presets.py",
                 "v3/_compat.py", "web/moss_sb_i18n.js", "web/moss_sb_editor.js",
                 "pyproject.toml", "LICENSE", "README.md", "README_ZH.md", "requirements.txt",
                 "THIRD-PARTY-NOTICES.md", "assets/hero-storyboard.png",
                 "examples/moss-storyboard-4panels-ui.json",
                 "examples/moss-layout-editor-poster.json",
                 "examples/moss-layout-editor-storyboard.json",
                 "examples/moss-prompt-compiler.json"]
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
        "README.md": grab("README.md", r"^\|\s*Version\s*\|\s*\*\*([\d.]+)\*\*"),
        "README_ZH.md": grab("README_ZH.md", r"^\|\s*版本\s*\|\s*\*\*([\d.]+)\*\*"),
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
#
# 🔴 这份词表**文件内容（S15）与提交信息（S9）共用**。原来两处各写一份，
#    结果是文件里漏了整整一年的"谁跟我说的"，而提交信息查得很细。
#: (标签, 正则)。标签会出现在报错里，所以标签本身也不能是敏感词明文。
INTERNAL_VOICE = (
    # 协作者代号：公开面该写「为什么这么做」，不该写「谁跟我说的」。
    ("协作者代号", re.compile(r"(?<![A-Za-z0-9])\u0047\u0047(?![A-Za-z0-9])")),
    ("宣传口吻", re.compile("\u5ba3\u4f20")),
    ("用户说", re.compile("\u7528\u6237\u8bf4")),
    ("用户要求", re.compile("\u7528\u6237\u8981\u6c42")),
    ("用户指出", re.compile("\u7528\u6237\u6307\u51fa")),
    ("用户反馈", re.compile("\u7528\u6237\u53cd\u9988")),
    ("用户点名", re.compile("\u7528\u6237\u70b9\u540d")),
    ("我让", re.compile("\u6211\u8ba9")),
    ("帮我", re.compile("\u5e2e\u6211")),
)
#: 本机痕迹：盘符路径与本机用户名（author 不算，这里只看提交信息正文）。
MSG_MACHINE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|Administrator", re.I)


def pushed_shas() -> set[str] | None:
    """已推送提交的全集。没有远端时返回 `None`（此时一律按"未推送"从严判）。"""
    r = git("rev-list", "origin/main")
    if r.returncode != 0:
        return None
    return {ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()}


def s9_commits() -> None:
    """未推送的必须改（rebase 即可）；已推送的只报数，不判红。

    为什么分开：已推送的要 force push 才能改，那是维护者的决定，不是门禁能替人做的。
    如果一并判红，这道闸就永久红 —— 而永久红的闸等于没有闸（没人再看它）。
    """
    say("\nS9 提交信息（推上去就只能改写历史）")
    out = git("log", "-n", "80", "--format=%H%x1f%B%x1e").stdout or ""
    pushed = pushed_shas()
    hard_leak, hard_machine, legacy = [], [], []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, _, body = rec.partition("\x1f")
        sha = sha.strip()
        is_old = pushed is not None and sha in pushed
        for label, rx in INTERNAL_VOICE:
            m = rx.search(body)
            if m:
                ln = body[: m.start()].count("\n") + 1
                (legacy if is_old else hard_leak).append("%s:%d（%s）" % (sha[:7], ln, label))
                break
        m = MSG_MACHINE.search(body)
        if m:
            ln = body[: m.start()].count("\n") + 1
            (legacy if is_old else hard_machine).append("%s:%d" % (sha[:7], ln))
    # 只报位置（短 hash:行号），不打印命中的原文 —— 日志本身也是公开面。
    check("未推送提交信息无内部措辞", not hard_leak,
          "；".join(hard_leak[:5]) + ("（rebase 改掉再推）" if hard_leak else ""))
    check("未推送提交信息无本机路径/用户名", not hard_machine, "；".join(hard_machine[:5]))

    if legacy:
        say("  [!] 已推送历史里还有 %d 处（%s）—— 改写需 force push + 重建 tag，"
            "门禁不拦，请人工决定" % (len(legacy), "；".join(legacy[:3])))
    else:
        say("  [OK] 已推送历史里也没有内部措辞")


#: 两处口径本来就不同的闸（CI 按单文件报数，CONTRIBUTING 报 6 份合计）
DOC_COUNT_SKIP = {"tools/check_ui_workflow.py"}


def s10_doc_counts() -> None:
    """门禁项数在 CONTRIBUTING.md 与 ci.yml 各写了一遍：不一致 = 改了代码没改文档。

    只比对两处都列出的闸；数量阈值防"正则失配 ⇒ 空集合 ⇒ 全过"的假绿。
    """
    say("\nS10 门禁计数：两份清单必须对得上")
    paths = {"CONTRIBUTING.md": os.path.join(ROOT, "CONTRIBUTING.md"),
             "ci.yml": os.path.join(ROOT, ".github", "workflows", "ci.yml")}
    try:
        text = {k: open(v, encoding="utf-8").read() for k, v in paths.items()}
    except OSError as exc:
        check("两份门禁清单可读", False, str(exc))
        return
    contrib = {}
    for ln in text["CONTRIBUTING.md"].splitlines():
        m = re.match(r"\s*(?:python|node)\s+(\S+\.(?:py|mjs))\b.*?#\s*(\d+) 项", ln)
        if m:
            contrib.setdefault(m.group(1), int(m.group(2)))
    ci = {m.group(2): int(m.group(1)) for m in re.finditer(
        r"(\d+)/0\s+(?:每个文件\s+)?(\S+\.(?:py|mjs))", text["ci.yml"])}
    shared = sorted(set(contrib) & set(ci) - DOC_COUNT_SKIP)
    if not check("两处共同列出 ≥12 道闸（正则失配要响）", len(shared) >= 12, "共 %d" % len(shared)):
        return
    bad = ["%s CONTRIBUTING=%s ci.yml=%s" % (f, contrib[f], ci[f])
           for f in shared if contrib[f] != ci[f]]
    check("同名闸的项数两处一致", not bad, "；".join(bad)[:200])


def s11_public_refs(files: list[str]) -> None:
    """公开文档不许引用**仓库里不存在**的内部目录。

    为什么单列一条：`审计报告/` 是 `.gitignore` 掉的维护者工作区，公开读者根本看不到。
    2026-10-28 实测有 10 处 `README`/`docs` 直接引了它（死链），而当时的门禁一条都没响 ——
    S2 只查盘符路径，查不到"路径合法但仓库里没有"。
    """
    say("\nS11 公开文档只引仓库里真实存在的路径")
    PUBLIC = ("README.md", "README_ZH.md", "README_EN.md", "CHANGES.md",
              "RELEASE-NOTES.md", "RELEASING.md", "CONTRIBUTING.md", "SECURITY.md",
              "CODE_OF_CONDUCT.md", "THIRD-PARTY-NOTICES.md")
    targets = [f for f in files if f in PUBLIC or (f.startswith("docs/") and f.endswith(".md"))]
    #: 只在内部工作区存在的目录/文件名；出现即死链。
    INTERNAL = ("审计报告", "同步书", "施工/", "审核-新代码")
    hits = []
    for rel in targets:
        for i, line in enumerate(read(rel).splitlines(), 1):
            for key in INTERNAL:
                if key in line:
                    hits.append("%s:%d（%s）" % (rel, i, key))
    check("公开文档不引用内部工作区（%d 份文档）" % len(targets), not hits,
          "；".join(hits[:8]))

    #: 本地文件的相对链接必须真的存在（否则公开发出去就是 404）。
    broken = []
    link_re = re.compile(r"\]\((?!https?:|#)([^)\s]+)\)")
    for rel in targets:
        base = os.path.dirname(rel)
        for i, line in enumerate(read(rel).splitlines(), 1):
            for m in link_re.finditer(line):
                tgt = m.group(1).split("#")[0].strip()
                if not tgt or tgt.startswith("mailto:"):
                    continue
                path = os.path.normpath(os.path.join(base, tgt))
                if not os.path.exists(os.path.join(ROOT, path)):
                    broken.append("%s:%d → %s" % (rel, i, tgt))
    check("相对链接都指向真实文件", not broken, "；".join(broken[:8]))

    #: 反引号里的路径也要真的存在。`docs/_local/sheets/` 就是这么漏出去的：
    #: 它不是 markdown 链接，上面那条 `](...)` 看不见它，读者点不到、也找不到。
    #: 只认**已知顶层目录**开头的 token，否则 `core/layout.Layout` 这种「模块.属性」会误报。
    TOPS = ("core", "v3", "web", "tools", "tests", "docs", "examples", "assets", ".github")
    CODE_PATH_RE = re.compile(r"`((?:%s)/[^`\s]*)`" % "|".join(re.escape(t) for t in TOPS))
    CODE_EXT_RE = re.compile(
        r"\.(py|js|mjs|json|md|toml|ya?ml|txt|png|jpe?g|webp|gif|zip|cfg|ini|html|css)$", re.I)
    #: 文档里**故意提到、但不在仓库里**的路径 —— 它们是「我们挡住了什么」的说明。
    #: 不豁免的话，这类文档只在维护者机器上通过（因为目录还在），换个人 clone 就假红。
    GHOST_ALLOW = ("docs/_local",)
    ghost = []
    for rel in targets:
        if rel == "CHANGES.md":
            continue        # 历史记录里提到的文件本来就可能是「当初删掉的那个」
        for i, line in enumerate(read(rel).splitlines(), 1):
            for m in CODE_PATH_RE.finditer(line):
                token = m.group(1).rstrip(".,;:，。；：")
                is_dir = token.endswith("/")
                if not is_dir and not CODE_EXT_RE.search(token):
                    continue                      # 「模块.属性」之类，不是路径
                if any(c in token for c in "*<>{}"):
                    continue                      # 占位符
                if token.rstrip("/").startswith(GHOST_ALLOW):
                    continue                      # 文档里说明用的私有路径，见上
                if not os.path.exists(os.path.join(ROOT, token.rstrip("/"))):
                    ghost.append("%s:%d → %s" % (rel, i, token))
    check("反引号里的路径都真实存在（不是本机留档）", not ghost, "；".join(ghost[:8]))


# --------------------------------------------------------------------------- S12 临时产物
#: 名字形态。S3 的 `JUNK_RE` 管的是 `*.bak` / `*.orig` / `dist/` 那一类，**不认** `oi_tmp.json`
#: （2026-10-28 实测：7 MB 的 `/object_info` dump 就是这么被 `git add -A` 带进提交的）。
TEMP_NAME_RE = re.compile(
    r"(^|/)(?:[^/]*(?:_tmp|[-.]tmp|_dump|_bak)\.[A-Za-z0-9]+"
    r"|oi_[^/]*\.json|node\.zip|core\.\d+)$", re.I)
#: 体积口径只对文本类生效（图片有自己的阈值，见 S7）。
TEXT_EXT = (".py", ".js", ".mjs", ".json", ".md", ".txt", ".yml", ".yaml",
            ".toml", ".cfg", ".ini", ".csv", ".html", ".css")
TEXT_SIZE_LIMIT = 512 * 1024


def s12_temp_artifacts(files: list[str]) -> None:
    say("\nS12 临时产物 / 文本 dump 不跟着提交走")
    by_name = [f for f in files if TEMP_NAME_RE.search(f)]
    check("受控文件里没有临时产物名", not by_name, "；".join(by_name[:5]))

    big = []
    for rel in files:
        if not rel.lower().endswith(TEXT_EXT):
            continue
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p) and os.path.getsize(p) > TEXT_SIZE_LIMIT:
            big.append("%s（%.0f KB）" % (rel, os.path.getsize(p) / 1024))
    check("受控文本文件都 ≤512 KB（更大的多半是 dump）", not big, "；".join(big[:4]))

    # 暂存区单独看一次：事故发生在**提交那一刻**，而本脚本平时是发布前才跑。
    staged = [ln.strip().replace("\\", "/")
              for ln in (git("diff", "--cached", "--name-only").stdout or "").splitlines() if ln.strip()]
    hits = [f for f in staged if TEMP_NAME_RE.search(f)]
    check("暂存区没有临时产物（`git add -A` 最爱顺手带上）", not hits, "；".join(hits[:5]))


# --------------------------------------------------------------------------- S13 提交格式
#: 允许 `type(scope): 描述`；scope 含版本号是合法的（`feat(0.2.2): …` 在历史里出现过）。
MSG_PREFIX_RE = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|perf|build|ci|style|revert)"
    r"(\([a-z0-9][a-z0-9.,\-]*\))?: \S")
#: 这些是工具生成的，不套模板。
MSG_SKIP_RE = re.compile(r"^(Merge |Revert \"|fixup! |squash! )")


def _width(s: str) -> int:
    """按终端列宽算：CJK 占 2 列。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def s13_commit_format() -> None:
    say("\nS13 提交信息格式（前缀 / 长度）")
    out = git("log", "-n", "50", "--format=%h%x1f%s%x1f%b%x1e").stdout or ""
    bad_prefix, long_subject, long_body = [], [], []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, _, rest = rec.partition("\x1f")
        subject, _, body = rest.partition("\x1f")
        sha = sha.strip()
        if MSG_SKIP_RE.match(subject):
            continue
        if not MSG_PREFIX_RE.match(subject):
            bad_prefix.append("%s（%s）" % (sha, subject[:28]))
        if len(subject) > 100:
            long_subject.append("%s（%d 字符）" % (sha, len(subject)))
        for i, line in enumerate(body.splitlines(), 2):
            if _width(line) > 120:
                long_body.append("%s:%d（%d 列）" % (sha, i, _width(line)))
    check("subject 形如 `type(scope): 描述`", not bad_prefix, "；".join(bad_prefix[:5]))
    check("subject ≤100 字符", not long_subject, "；".join(long_subject[:5]))
    check("正文每行 ≤120 列（中文按 2 列）", not long_body, "；".join(long_body[:5]))


# --------------------------------------------------------------------------- S14 SPDX
#: 第三方原文照抄目录不套本仓的许可头（它有自己的 LICENSE 与 PROVENANCE）。
SPDX_SKIP_PREFIX = ("docs/reference/",)


def s14_spdx(files: list[str]) -> None:
    say("\nS14 源码文件都带 SPDX 许可头")
    srcs = [f for f in files if f.endswith((".py", ".js", ".mjs"))
            and not f.startswith(SPDX_SKIP_PREFIX)]
    miss = []
    for rel in srcs:
        try:
            head = "\n".join(read(rel).splitlines()[:5])
        except OSError:
            continue
        if "SPDX-License-Identifier" not in head:
            miss.append(rel)
    check("源码 %d 个都有 SPDX 标识" % len(srcs), not miss, "；".join(miss[:6]))


# --------------------------------------------------------------------------- S15 内部措辞
#: 规则表本身必须把那些词写出来才能检查它们 —— 唯一豁免项，明写在代码里。
#: 别把豁免扩大到别处：一处豁免就是一处洞。
SELF = "tools/check_release_ready.py"


def s15_internal_voice(files: list[str]) -> None:
    """公开文件里不许出现「谁跟我说的」，只许出现「为什么这么做」。

    为什么单列：S9 只管提交信息，S2 只管盘符路径。**文件内容里的内部措辞一直是空白**——
    2026-10-28 实测 5 处（协作者代号 4 处、直接引述对话 1 处），而当时 14 道闸全绿。
    这些字会随 README / 源码 / 数据文件一起出现在 GitHub 和 registry 包上。
    """
    say("\nS15 公开文件里没有内部称呼 / 会话措辞")
    hits = []
    for rel in files:
        if rel == SELF or not is_text(rel):
            continue
        for i, line in enumerate(read(rel).splitlines(), 1):
            for label, rx in INTERNAL_VOICE:
                if rx.search(line):
                    hits.append("%s:%d（%s）" % (rel, i, label))
                    break
    check("受控文件里无内部称呼 / 会话措辞（%d 个文本文件）" % len(files), not hits,
          "；".join(hits[:8]))


# --------------------------------------------------------------------------- S16 黑名单
def s16_blacklist_travels(files: list[str]) -> None:
    """黑名单必须**随仓库走**：写进 `.gitignore`，不能只写在 `.git/info/exclude`。

    为什么单列：`.git/info/exclude` 不在版本库里。2026-10-28 实测 `docs/_local/`
    （26 MB 私有对照图 + 一次性脚本）只被它挡着 —— 换个人 clone 下来 `git add -A`
    就原样带走，而任何门禁都看不出来（那些文件"本来就没被跟踪"）。
    """
    say("\nS16 黑名单随仓库走（`.git/info/exclude` 不算数）")
    out = (git("ls-files", "--others", "--exclude-standard").stdout or "")
    loose = [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]
    check("工作树没有「未跟踪且未被忽略」的文件（`git add -A` 会原样带走）",
          not loose, "；".join(loose[:6]))

    local_file = os.path.join(ROOT, ".git", "info", "exclude")
    local_rules = []
    if os.path.isfile(local_file):
        with io.open(local_file, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                s = ln.strip()
                if s and not s.startswith("#"):
                    local_rules.append(s)
    gi = read(".gitignore") if os.path.isfile(os.path.join(ROOT, ".gitignore")) else ""
    orphan = [r for r in local_rules if r.rstrip("/") not in gi]
    check("`.git/info/exclude` 的 %d 条规则在 `.gitignore` 里都有" % len(local_rules),
          not orphan, "只在本地生效：%s" % "；".join(orphan[:5]) if orphan else "")


# --------------------------------------------------------------------------- S17 二进制
#: PNG 里这些块是**内容**，其余（tEXt / zTXt / iTXt）是**文字**——ComfyUI 存图会把
#: 整条 workflow JSON 塞进 tEXt，里面可能有本机路径、模型文件名、甚至提示词原文。
PNG_TEXT_CHUNKS = {"tEXt", "zTXt", "iTXt"}


def s17_binary_meta(files: list[str]) -> None:
    say("\nS17 二进制文件里没有本机元数据")
    bad = []
    for rel in files:
        if not rel.lower().endswith(".png"):
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            data = fh.read()
        i = 8
        while i + 8 <= len(data):
            try:
                (ln,) = struct.unpack(">I", data[i:i + 4])
                typ = data[i + 4:i + 8].decode("latin-1")
            except (struct.error, UnicodeDecodeError):
                break
            if typ in PNG_TEXT_CHUNKS:
                bad.append("%s（%s 块）" % (rel, typ))
                break
            if typ == "IEND":
                break
            i += 12 + ln
    check("PNG 里没有 tEXt/zTXt/iTXt 文本块", not bad, "；".join(bad[:5]))


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
    s10_doc_counts()
    s11_public_refs(files)
    s12_temp_artifacts(files)
    s13_commit_format()
    s14_spdx(files)
    s15_internal_voice(files)
    s16_blacklist_travels(files)
    s17_binary_meta(files)

    say("\n" + "=" * 64)
    say("结果：通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        say("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
