# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""提示词包：加载、sha 校验、manifest 生成。

为什么sha 进 manifest：提示词是"看不见的代码"——改一个字，输出契约就变了。
manifest 让运行时报出「用的是哪一版、哪个文件、什么哈希」，改词必须升版本，
否则加载直接失败（不静默用被改过的提示词）。

    python -m moss_llm.pack --write     # 改完提示词后重新生成 manifest
"""

import hashlib
import json
import sys
from pathlib import Path

#: 包内文件的固定清单。id = 运行时取用键；path = 相对 prompts/ 的文件名。
FILES = [
    ("caption.faithful.en", "caption.faithful.en.txt", "en"),
    ("caption.clean.en", "caption.clean.en.txt", "en"),
    ("caption.pass1.en", "caption.pass1.en.txt", "en"),
    ("caption.pass2.en", "caption.pass2.en.txt", "en"),
    ("expand.method.en", "expand.method.en.txt", "en"),
    ("expand.moss_image.en", "expand.moss_image.en.txt", "en"),
    ("expand.moss_storyboard.zh", "expand.moss_storyboard.zh.txt", "zh"),
    ("expand.cn_natural.zh", "expand.cn_natural.zh.txt", "zh"),
    ("expand.generic_natural.en", "expand.generic_natural.en.txt", "en"),
    ("expand.generic_tags.en", "expand.generic_tags.en.txt", "en"),
    ("expand.cinematic.en", "expand.cinematic.en.txt", "en"),
    # H3 音视频：扩写任务 + target=minimax-h3 时启用（不占 dialect 枚举）
    ("expand.h3.en", "expand.h3.en.txt", "en"),
    ("expand.h3_six.en", "expand.h3_six.en.txt", "en"),
    # 看图出 H3：图 + 一句补充 → 三字段 / 六段式
    ("caption.h3.en", "caption.h3.en.txt", "en"),
    ("caption.h3_six.en", "caption.h3_six.en.txt", "en"),
    ("edit.i2i.en", "edit.i2i.en.txt", "en"),
]

PACK_NAME = "moss-llm-prompts"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(prompts_dir):
    """按 FILES 重写 manifest.json。返回 manifest dict。"""
    prompts_dir = Path(prompts_dir)
    files = []
    for fid, rel, lang in FILES:
        p = prompts_dir / rel
        if not p.is_file():
            raise FileNotFoundError("提示词包缺文件：%s" % p)
        files.append({"id": fid, "path": rel, "sha256": sha256_file(p), "lang": lang,
                      "license": "MIT", "source": "Original; method informed by public sources"})
    man = {"pack": PACK_NAME, "version": "1.8.0", "updated": _today(),
           "files": files}
    (prompts_dir / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return man


def _today():
    import datetime
    return datetime.date.today().isoformat()


def load_pack(prompts_dir):
    """读 manifest → 校验每个文件 sha → 返回 {pack, version, updated, files, texts}。

    sha 不符直接 raise：提示词被改过但没升版本，输出契约就不再是报告里那个。
    """
    prompts_dir = Path(prompts_dir)
    man_path = prompts_dir / "manifest.json"
    if not man_path.is_file():
        raise RuntimeError(
            "提示词包缺 manifest.json（%s）。\n"
            "    生成它：python -m moss_llm.pack --write" % man_path)
    man = json.loads(man_path.read_text(encoding="utf-8"))
    texts, checked = {}, []
    for entry in man.get("files") or []:
        p = prompts_dir / entry["path"]
        if not p.is_file():
            raise RuntimeError("manifest 列了 %s 但文件不在（%s）" % (entry["path"], p))
        got = sha256_file(p)
        if got != entry.get("sha256"):
            raise RuntimeError(
                "提示词 %s 的 sha 与 manifest 不符（改过字就要升版本）。\n"
                "    确认要保留改动：python -m moss_llm.pack --write；\n"
                "    想还原：git checkout -- moss_llm/prompts/" % entry["path"])
        texts[entry["id"]] = p.read_text(encoding="utf-8")
        checked.append({"id": entry["id"], "path": entry["path"], "sha256": got,
                        "lang": entry.get("lang", ""), "license": entry.get("license", "")})
    missing = [fid for fid, _rel, _lang in FILES if fid not in texts]
    if missing:
        raise RuntimeError("提示词包缺条目：%s" % "、".join(missing))
    return {"pack": man.get("pack", PACK_NAME), "version": man.get("version", "?"),
            "updated": man.get("updated", ""), "files": checked, "texts": texts}


if __name__ == "__main__":
    here = Path(__file__).resolve().parent / "prompts"
    if "--write" in sys.argv:
        man = write_manifest(here)
        print("manifest 已写：%d 个文件，pack %s v%s" % (len(man["files"]), man["pack"], man["version"]))
    else:
        pack = load_pack(here)
        print("pack %s v%s：%d 个文件，sha 全部相符" % (pack["pack"], pack["version"], len(pack["files"])))
        for f in pack["files"]:
            print("  %-28s %s %s" % (f["id"], f["sha256"][:8], f["path"]))
