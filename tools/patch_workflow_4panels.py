# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""把 UI 工作流改成「4 格 / 画布 1024×576」并保留用户加的 LoRA 与其它改动。

只做**定点改写**，不重建文件：
- 保留用户的 LoRA 节点、连线、节点位置、分区、说明。
- 按 widget **名字**取值，不按位置硬编码 —— 位置是给 ComfyUI 用的，改名才是给人用的。

跑法：python tools/patch_workflow_4panels.py [--edge 496] [--dry]
"""

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import layout as L  # noqa: E402
from core import prompt as T  # noqa: E402
from _paths import host_root  # noqa: E402  （脚本同目录，见 tools/_paths.py）

COMFY = host_root()
WF = os.path.join(COMFY, "user", "default", "workflows", "MOSS 故事板宫格.json")

#: 4 格版提示词：第 1 段全局改成「恰好 4 个面板」，并只保留前 4 段分镜。
#: ⚠ 这是**内容改写**，不是参数改写。8 格原稿留在 .bak 里。
STYLE_4 = ("[STYLE]: 单一故事板画布分割为严格对称网格，恰好 4 个面板，每个面板展示雨夜咖啡店叙事的顺序推进，"
           "所有面板由细白边框线整齐分隔，完全没有粗框、黑边或跨面板全景溢出；苏晚：黑发低马尾、米白高领针织衫、"
           "哑光织物、自然裸肤毛孔；陈屿：深灰外套、黑衬衫、短发、修长手指、哑光面料；"
           "固定环境：雨夜咖啡店靠窗双人桌、湿街反光、玻璃雨痕、暖黄吊灯、柠檬水杯、咖啡杯；"
           "相机规格：35mm 胶片摄影、真实演员、自然原始皮肤纹理、有机胶片颗粒、锐利光学镜头焦点、浅景深、近距取景")

# Layout / Sampler 的 widget 名（与 nodes.py 的 INPUT_TYPES 顺序一致，靠名字定位）
LAYOUT_W = ["prompt", "panels", "panel_aspect", "panel_edge", "canvas_aspect", "columns",
            "rows", "gutter", "margin", "cell_gray", "invert", "label"]
SAMPLER_W = ["positive", "negative", "steps", "cfg", "sampler_name", "scheduler", "seed",
             "<control_after_generate>", "denoise", "panels", "panel_aspect", "panel_edge",
             "canvas_aspect", "gutter", "margin", "init", "label", "reborder", "border_px", "shave"]


def main(argv):
    edge = 496
    if "--edge" in argv:
        edge = int(argv[argv.index("--edge") + 1])
    dry = "--dry" in argv

    with open(WF, encoding="utf-8") as fh:
        wf = json.load(fh)

    lay = L.build_layout(panels=4, panel_edge=edge, panel_aspect="16:9", gutter=8, margin=8,
                         canvas_aspect="auto")
    print("目标：%s" % lay.spec())
    print("      画布 %d×%d（%.2fMP）单格 %dx%d" % (lay.width, lay.height, lay.megapixels,
                                                lay.panel_w, lay.panel_h))

    changed = []
    for n in wf["nodes"]:
        if n["type"] == "MossStoryboardLayout":
            w = n["widgets_values"]
            for key, val in (("prompt", ""), ("panels", 4), ("panel_edge", edge)):
                i = LAYOUT_W.index(key)
                if w[i] != val:
                    changed.append("Layout.%s: %r -> %r" % (key, w[i], val))
                    w[i] = val
        elif n["type"] == "MossStoryboardSampler":
            w = n["widgets_values"]
            head, caps = T.split_panels(w[SAMPLER_W.index("positive")])
            # 只保留前 4 段分镜，并把全局段换成 4 格版
            keep = [c for _, c in caps[:4]]
            new_prompt = STYLE_4 + "\n\n" + "\n\n".join("[PANEL %d]: %s" % (i + 1, c)
                                                        for i, c in enumerate(keep))
            for key, val in (("positive", new_prompt), ("panels", 4), ("panel_edge", edge)):
                i = SAMPLER_W.index(key)
                if w[i] != val:
                    changed.append("Sampler.%s: %s -> %s" % (
                        key,
                        "（%d 字）" % len(str(w[i])) if key == "positive" else repr(w[i]),
                        "（%d 字）" % len(val) if key == "positive" else repr(val)))
                    w[i] = val

    if not changed:
        print("没有需要改的（已是 4 格 / edge=%d）" % edge)
        return 0
    for c in changed:
        print("  · " + c)

    if dry:
        print("dry-run，未写盘")
        return 0

    bak = WF + ".bak-8panels-512"
    if not os.path.exists(bak):
        shutil.copy2(WF, bak)
        print("已备份原 8 格版 -> %s" % os.path.basename(bak))

    with open(WF, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print("已写回 %s" % WF)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
