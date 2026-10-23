#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量出图：不同风格 / 不同提示词 / 不同比例，供宣传素材使用。

跑法：
    python tools/make_gallery.py                 # 全部用例
    python tools/make_gallery.py --only anime    # 只跑名字含 anime 的
    python tools/make_gallery.py --dry           # 只打印计划，不提交

产物：`<ComfyUI>/output/moss/gallery/<slug>_grid_*.png` + 逐格 `<slug>_panel_*.png`

前置：宿主在跑、模型已就位。工作流取自仓库内示例（节点 id 固定：4=宫格几何，5=生成）。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ui_to_api as U  # noqa: E402  （同目录工具）
from _paths import host_root  # noqa: E402

WF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "examples", "moss-storyboard-4panels-ui.json")
API = os.environ.get("MOSSSB_API", "http://127.0.0.1:8188")

#: 用例：slug / 格数 / 单格比例 / 单格长边 / 提示词
#: 提示词格式：第一段全局（风格 + 角色 + 环境 + 相机），随后每格一段 `[PANEL n]`。
CASES = [
    ("cinematic-rain", 4, "16:9", 496, """电影级写实，雨夜咖啡店，单一画布分成严格对称的 4 格，细白线分隔；
苏晚：黑发低马尾、米白高领针织衫、哑光织物、自然裸肤毛孔；
陈屿：深灰外套、黑衬衫、短发、修长手指；
固定环境：靠窗双人桌、湿街反光、玻璃雨痕、暖黄吊灯、柠檬水杯、咖啡杯；
35mm 胶片摄影、真实演员、原始皮肤纹理、有机胶片颗粒、浅景深

[PANEL 1]: 中景，两人对坐，她把柠檬水杯推向桌面正中
[PANEL 2]: 特写，她的眼睫与玻璃上的雨痕重叠
[PANEL 3]: 过肩，他抬眼看向窗外的霓虹
[PANEL 4]: 桌面特写，两只手停在杯沿，水珠沿杯壁滑下"""),

    ("anime-girl-cat", 6, "16:9", 496, """日式赛璐璐动画，明亮通透，单一画布分成 2 行 3 列的 6 格，细白线分隔；
少女：栗色齐肩短发、白色水手服、红色领结、大眼、脸颊微红；
三花猫：橘白黑三色、圆脸、绿眼睛；
场景：夏日午后的木质走廊、风铃、绿荫、蝉鸣感；动画分镜、干净线稿、柔和阴影

[PANEL 1]: 全景，少女坐在走廊台阶上，猫从远处走来
[PANEL 2]: 中景，少女伸手，猫停下抬头
[PANEL 3]: 特写，猫的绿眼睛映出风铃
[PANEL 4]: 近景，少女微笑，风吹起短发
[PANEL 5]: 双人中景，猫跳上她的膝头
[PANEL 6]: 远景，一人一猫在树影里，画面上方风铃"""),

    ("ink-mountain", 4, "9:16", 496, """中国水墨画，宣纸质感，单一竖幅画布分成上下 4 格，细白线分隔；
墨色浓淡分明，留白构图，远处淡墨山峦，近处焦墨松枝，雾气留白；
题款位置留空，不写任何文字；写意笔触、飞白、淡赭石点染

[PANEL 1]: 远景，云雾中的层叠山峦，淡墨晕染
[PANEL 2]: 中景，孤舟横于江心，船上一人披蓑
[PANEL 3]: 近景，崖边松枝横出，墨色最重
[PANEL 4]: 特写，江面水纹与倒影，极简数笔"""),

    ("cyberpunk-street", 9, "16:9", 496, """赛博朋克夜景，单一画布分成 3 行 3 列的 9 格，细白线分隔；
霓虹紫青双色光、湿滑沥青反光、全息广告牌、蒸汽从井盖升起、繁体霓虹字招牌；
主角：黑色机能风衣、义体左臂、银灰短发；电影感、变形宽银幕镜头、强对比

[PANEL 1]: 大全景，雨中的霓虹街道，人群剪影
[PANEL 2]: 中景，主角站在街角，义体手臂反光
[PANEL 3]: 特写，义体手指捏着一枚芯片
[PANEL 4]: 仰拍，全息广告牌上的人脸俯视
[PANEL 5]: 中近景，主角侧脸，霓虹映在眼里
[PANEL 6]: 特写，雨滴打在金属手背
[PANEL 7]: 过肩，巷口有两个人影靠近
[PANEL 8]: 低角度，主角迈步，水花溅起
[PANEL 9]: 远景，身影融入霓虹街道尽头"""),

    ("oil-still-life", 4, "1:1", 496, """古典油画静物，单一正方形画布分成 2 行 2 列的 4 格，细白线分隔；
伦勃朗式侧光、深褐背景、厚重笔触、可见油画肌理与颜料堆叠；
物件：黄铜水壶、剥开的石榴、深红葡萄酒、摊开的旧书、白蜡烛

[PANEL 1]: 整体，桌面静物组合，烛光从左侧打来
[PANEL 2]: 特写，石榴籽的高光与汁水
[PANEL 3]: 特写，黄铜水壶的曲面反光
[PANEL 4]: 近景，旧书页与酒杯边缘"""),

    ("manga-action", 6, "9:16", 496, """黑白漫画分镜，网点纸质感，单一竖幅画布分成 3 行 2 列的 6 格，细白线分隔；
高对比黑白、速度线、爆炸网点、拟声字留白（不写文字）；
主角：短发剑士、长外套、握日本刀；动态构图、强透视

[PANEL 1]: 全景，剑士背对镜头立于废墟
[PANEL 2]: 特写，握刀的手，指节发白
[PANEL 3]: 中景，拔刀瞬间，速度线四射
[PANEL 4]: 仰拍，跃起，外套翻飞
[PANEL 5]: 特写，刀锋与火花
[PANEL 6]: 远景，敌人倒下，尘埃落定"""),

    ("food-4panel", 4, "4:3", 496, """美食摄影，单一画布分成 2 行 2 列的 4 格，细白线分隔；
自然侧光、浅景深、木质桌面、亚麻餐巾、热气可见；
主体：刚出烤箱的可颂、手冲咖啡、橙汁、草莓

[PANEL 1]: 整体，早餐桌面俯拍
[PANEL 2]: 特写，可颂酥皮的层次与碎屑
[PANEL 3]: 近景，咖啡液面与杯壁水汽
[PANEL 4]: 特写，草莓表面的水珠"""),

    ("fantasy-wide", 4, "21:9", 496, """史诗奇幻概念艺术，单一宽幅画布分成 1 行 4 列的 4 格，细白线分隔；
冷蓝与金色对比、体积光、远景云雾山峦、巨大古城遗迹；
主角：披斗篷的旅人、手持长杖；电影概念设定图、厚涂

[PANEL 1]: 大全景，旅人站在峡谷边缘，远处遗迹
[PANEL 2]: 中景，斗篷被风吹起，长杖发光
[PANEL 3]: 特写，杖顶的水晶与纹路
[PANEL 4]: 远景，旅人走向城门，光从门缝射出"""),
]


def run_case(name, panels, aspect, edge, prompt, wf, infos, dry=False):
    api, _, _ = U.convert(copy.deepcopy(wf), infos)
    lay, smp = "4", "5"
    api[lay]["inputs"]["panels"] = panels
    api[lay]["inputs"]["panel_aspect"] = aspect
    api[lay]["inputs"]["panel_edge"] = edge
    api[smp]["inputs"]["positive"] = prompt
    api[smp]["inputs"]["seed"] = int(time.time() * 1000) % 2147483647
    for nid, kind in (("7", "grid"), ("8", "panel")):
        if nid in api:
            api[nid]["inputs"]["filename_prefix"] = "moss/gallery/%s_%s" % (name, kind)
    if dry:
        print("  [dry] %-18s %d 格 %s edge=%d  prompt=%d 字" % (name, panels, aspect, edge, len(prompt)))
        return True
    res = U.post_json(API + "/prompt", {"prompt": api, "client_id": "mosssb-gallery"})
    pid = res.get("prompt_id")
    if not pid:
        print("  ✗ %s 被拒收：%s" % (name, str(res.get("node_errors"))[:160]))
        return False
    t0 = time.time()
    while time.time() - t0 < 600:
        time.sleep(2)
        h = U.get_json("%s/history/%s" % (API, pid))
        if pid in h:
            st = (h[pid].get("status") or {})
            ok = st.get("status_str") == "success"
            print("  %s %-18s %.0fs" % ("✓" if ok else "✗", name, time.time() - t0))
            return ok
    print("  ✗ %s 超时" % name)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    wf = json.load(open(WF, encoding="utf-8"))
    infos = U.get_json(API + "/object_info")
    cases = [c for c in CASES if not a.only or a.only in c[0]]
    print("宿主 %s ｜ 用例 %d 个 ｜ 产物目录 %s" % (
        host_root(), len(cases), os.path.join(host_root(), "output", "moss", "gallery")))
    ok = 0
    for c in cases:
        if run_case(*c, wf=wf, infos=infos, dry=a.dry):
            ok += 1
    print("\n结果：成功 %d / %d" % (ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
