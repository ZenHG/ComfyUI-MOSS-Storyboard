# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""中文整句渲染路径（`--lang zh`）。

## 为什么单独一个模块，而不是在 `clauses.py` 里加分支

英文路径的唯一价值是「默认输出逐字节不变」。任何往 `build_clauses()` /
`render_prose()` 里插 `if lang == "zh"` 的改法，都会让英文路径多一条被顺手改坏的
机会——而它被 4 份真机 IR × 5 个后端的 sha256 基线钉着（`tools/freeze_smith_set.py`）。
所以中文路径**整条独立**：自己的子句计划、自己的句子拼装、自己的判据。
`clauses` 只被当作数据结构（`Clause` / `pack` / `DIM_ORDER`）复用。

## 和「词表替换」的区别

词表替换只把 `yellow raincoat` 换成「黄色雨衣」，句子骨架仍是英文的
`subject, with X, wearing Y, in the Z`——于是得到「一个28岁的女人，带着黑色长发，
穿着白色高领毛衣，在左前景」。中文不这么说话。本模块做三件事：

1. **整句重排**：主体+动作先在句首落住，再场景、镜头、光、材质/媒介、画面文字；
   方位必须由动词/介词引出（「坐在画面左前景」），不能像英文那样裸挂在句尾；
2. **中文语法落点**：量词（一个人 / 一杯柠檬水 / 一盏台灯）、「的」字结构、
   「穿着…（面料柔软）」这类定语与谓语的分工、全角标点；
3. **机检规则**（`:func:`zh_issues`）：英文功能词残留、未翻译英文词、ASCII 标点、
   「的」字堆叠、句首虚词、主语未起手——这些加起来就是「无英文语序」的可单测判据，
   不是靠人眼看。

## 判据边界（必须写在文档里，别夸大）

- 机检能证明的是「没有英文语法的表面特征」+「固定事实没丢」；
  **不能**证明母语审校通过。人读抽检结论单列在 `docs/28-中文整句.md`。
- 结构化字段（entity / camera / lighting / composition / style）是**模板化重排**，
  质量可复现；`notes` 的观察散文是**词表机翻**（长句没有模板可套），
  属「机翻痕迹」，不是「英文语序」——两者分开判。
- 公开仓纪律：本文件不写任何本机路径。

## 事实对账怎么跑

现有 `roundtrip.diff()` 的固定项命中器是**英文词表**（颜色词、方位词）。
中文输出直接丢进去会把「黄色」判成丢失。所以提供 :func:`fact_surface`：
把中文里的颜色/方位面**反向归一化**回英文规范词，再交给既有 `roundtrip.diff()`
核对。这不是额外事实，只是把同一条事实换到可比对的面上；口径写在
`docs/28-中文整句.md` §4。
"""

from __future__ import annotations

import re

from .clauses import DIM_ORDER, Clause, pack
from .ir import IR, Entity

#: 中文路径的本名字。`--lang` 只认这两个值。
LANG_EN = "en"
LANG_ZH = "zh"

_CJK = re.compile(r"[\u3400-\u9fff]")
_QUOTED_SPAN = re.compile(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」')
_DASH_SPLIT = re.compile(r"[,;]")


# ─────────────────────────────────────────────────────────────
# 1. 词表
#
# 分三层，优先级从高到低：
#   _EXACT  整串字段值 → 中文（结构化字段的确定值，覆盖 fixtures / examples）
#   _SEN    整句 → 中文（notes / style.notes 这类观察散文，逐句对齐）
#   _WORDS  单词 → 中文（词表外输入的兜底；质量未校，会在 warnings 里点名）
# ─────────────────────────────────────────────────────────────

#: 量词 / 名词 / 形容词 / 动作 / 场景的词级兜底表。只收「意译稳定」的词：
#: 颜色、材质、景别、光照、氛围这类。含专名的不收（保原词，由 allowed 名单放行）。
_WORDS = {
    # 人
    "woman": "女人", "man": "男人", "girl": "女孩", "boy": "男孩", "person": "人",
    "female": "女性", "male": "男性", "people": "人物", "figure": "人物",
    # 颜色（与 expand.COLORS 对齐的子集）
    "black": "黑色", "white": "白色", "dark": "深色", "light": "浅色", "gray": "灰色",
    "grey": "灰色", "brown": "棕色", "orange": "橙色", "yellow": "黄色", "red": "红色",
    "blue": "蓝色", "green": "绿色", "silver": "银色", "gold": "金色", "golden": "金色",
    "beige": "米色", "cream": "奶油色", "navy": "藏青色", "fair": "白皙",
    "dark green": "深绿色", "dark brown": "深棕色", "light gray": "浅灰色",
    # 形体
    "hair": "头发", "skin": "皮肤", "face": "脸", "eyes": "眼睛", "gaze": "目光",
    "shoulders": "肩膀", "head": "头部", "hand": "手", "hands": "双手",
    "long": "长", "short": "短", "straight": "直", "curly": "卷", "wavy": "波浪",
    "neat": "整洁", "messy": "凌乱", "damp": "微湿",
    "smooth": "光滑", "oval": "椭圆", "angular": "棱角分明", "defined": "清晰",
    "relaxed": "放松", "tilted": "倾斜", "forward": "前倾",
    # 服装 / 道具
    "sweater": "毛衣", "turtleneck": "高领", "coat": "外套", "jacket": "夹克",
    "pants": "长裤", "boots": "短靴", "raincoat": "雨衣", "umbrella": "雨伞",
    "uniform": "制服", "fabric": "面料", "knit": "针织", "textured": "有纹理",
    "fitted": "合身", "plain": "素色", "soft": "柔软", "cup": "杯子", "glass": "杯子",
    "lemonade": "柠檬水", "pen": "笔", "notebook": "笔记本", "lamp": "台灯",
    "paper": "纸", "pocket": "口袋",
    # 光 / 氛围
    "warm": "暖", "cool": "冷", "muted": "低饱和", "cinematic": "电影感",
    "intimate": "亲密", "contemplative": "沉静", "quiet": "安静", "evening": "傍晚",
    "neutral": "中性", "professional": "专业", "clean": "干净", "focused": "专注",
    "photograph": "摄影作品", "manga": "漫画", "illustration": "插画",
    "centered": "居中", "center": "居中", "centre": "居中",
    "background": "背景", "foreground": "前景", "frame": "画面",
    # 功能词：英文结构词在兜底翻译里直接丢（不丢会变成中英混排）
    "a": "", "an": "", "the": "", "of": "", "with": "", "and": "、", "or": "或",
    "in": "", "on": "", "at": "", "to": "", "for": "", "from": "", "by": "",
    "is": "", "are": "", "was": "", "were": "", "be": "", "this": "这个",
    "that": "那个", "its": "它的", "as": "", "into": "", "over": "", "under": "",
}

#: 整串字段值 → 中文。覆盖率 = fixtures + examples 里**所有**出现过的结构化值；
#: 词表外的值走 `_WORDS` 兜底，并在 warnings 里报「未翻译」。
_EXACT = {
    # ── hair ──
    "black, long, tied back": "黑色长发，扎在脑后",
    "black, short, neat": "黑色短发，发型整齐",
    "short, dark, straight, bangs": "深色短直发，齐刘海",
    "short, dark, neat": "深色短发，发型整齐",
    "dark brown, short, styled with volume on top, neat": "深棕色短发，顶部蓬松，发型整齐",
    "shoulder-length damp black hair": "微湿的黑色及肩发",
    "long straight black hair": "黑色长直发",
    "short messy brown hair": "棕色凌乱短发",
    # ── skin ──
    "fair, smooth": "皮肤白皙、光滑",
    "light tone, smooth surface": "肤色偏浅、表面光滑",
    "fair skin with visible pores": "皮肤白皙、毛孔可见",
    # ── face ──
    "oval, neutral expression": "脸型椭圆，表情中性",
    "angular, neutral expression": "脸型棱角分明，表情中性",
    "oval bone structure, neutral expression": "脸型椭圆、骨骼清秀，表情中性",
    "oval bone structure, glasses, neutral expression": "脸型椭圆、骨骼清秀，戴眼镜，表情中性",
    "oval bone structure, defined jawline, faint stubble":
        "脸型椭圆、骨骼清秀，下颌线清晰，有淡淡胡茬",
    # ── expression ──
    "thoughtful, slightly sad": "若有所思，略带忧郁",
    "focused, slightly tired": "神情专注，略显疲惫",
    "pensive, slightly weary": "若有所思，略显倦意",
    "neutral, direct gaze": "表情平静，目光直视",
    "looking up at the sky": "抬头望向天空",
    # ── 不带动词的姿态（带动词的见 _POSE）──
    "sitting": "坐着",
    "standing": "站着",
    # ── wardrobe ──
    "white turtleneck sweater": "白色高领毛衣",
    "dark coat": "深色外套",
    "soft fabric, fitted": "面料柔软、合身",
    "dark long-sleeved sweater": "深色长袖毛衣",
    "plain fabric": "素色面料",
    "turtleneck sweater": "高领毛衣",
    "dark pants": "深色长裤",
    "light gray crew neck t-shirt, textured knit, fitted":
        "浅灰色圆领T恤（针织纹理、合身）",
    "a yellow raincoat": "一件黄色雨衣",
    "black ankle boots": "黑色短靴",
    "a navy school uniform": "一套藏青色校服",
    "a red umbrella": "一把红伞",
    "a blue windbreaker": "一件蓝色防风外套",
    # ── props（第二项是「是否手持」）──
    "glass of lemonade": "一杯柠檬水",
    "white cup": "一个白色杯子",
    "pen": "一支笔",
    "notebook": "一个笔记本",
    "desk lamp": "一盏台灯",
    "umbrella": "一把雨伞",
    # ── 物体 / 材质 ──
    "handmade ceramic cup": "手工陶瓷杯",
    "fine speckled glaze": "细密斑点釉",
    # ── 镜头（景别 / 角度 / 景深分开查，避免 "medium" 歧义）──
    "the two people at the table": "桌边的两个人",
    "the man's face, especially the eyes": "男人的脸，尤其是眼睛",
    "her face": "她的脸",
    # ── 光照 ──
    "soft, warm light from the front and side": "柔和暖光从正面和侧面照来",
    "front-center, soft": "正面居中，柔光",
    "soft overcast daylight from the left": "来自左侧的柔和阴天日光",
    "cold fluorescent light from the storefront, warm rim from the street lamp":
        "店门口冷色调荧光灯，路灯带来暖色轮廓光",
    "soft side light from the right": "来自右侧的柔和侧光",
    "ambient light from the environment": "环境光补光",
    "soft frontal fill": "柔和的正面补光",
    "dusk": "黄昏时分", "blue hour": "蓝调时刻", "night": "夜晚", "daylight": "日光",
    "intimate, contemplative": "亲密而沉静", "calm, neutral": "平静、中性",
    "quiet": "安静", "calm": "平静",
    # ── 构图 ──
    "top and bottom margins of the frame": "画面上缘与下缘",
    "open sky in the upper right": "右上方",
    # ── 风格 ──
    "photograph": "摄影作品", "manga": "漫画", "oil painting": "油画",
    "3d render": "3D 渲染", "35mm film": "35mm 胶片", "cinematic still": "电影感定格",
    "studio product campaign": "棚拍产品广告", "anime screencap": "动画截帧",
    # ── 氛围 ──（"quiet" 已在「光照」段定义过，同值不重复写）
    "intimate": "亲密", "contemplative": "沉静", "warm": "温暖", "soft": "轻柔",
    "evening": "傍晚", "wet pavement": "湿漉漉的路面",
    "light mist": "薄雾", "rain": "雨", "steam from a vent": "通风口冒出的蒸汽",
    "neutral": "中性", "professional": "专业", "studio": "影棚感", "clean": "干净",
    "focused": "专注",
    # ── 画布背景 ──
    "a narrow kyoto backstreet after rain, wet asphalt mirroring the shop signs":
        "雨后京都一条狭窄的后街，湿漉漉的沥青路面倒映着店铺招牌",
    "a rain-soaked convenience store entrance at night": "夜晚被雨浸透的便利店门口",
    "an ivory studio backdrop with a soft falloff toward the edges":
        "象牙色摄影棚背景，向四周柔和过渡",
    # ── 排版 ──
    "bold": "加粗", "regular": "常规字重", "normal": "常规字重",
    "moderately heavy": "中等偏粗字重", "heavy": "粗体",
    "sans-serif": "无衬线字体", "serif": "衬线字体",
    "crisp lettering with generous spacing, no backing panel":
        "字迹利落、字距宽松、无底板",
    "title": "标题式大小写", "lower": "全小写", "upper": "全大写",
}

#: 观察散文（notes / style.notes）的整句表。逐句对齐，不做词级拼接。
_SEN = {
    "soft, cinematic lighting with shallow depth of field.":
        "柔和而富有电影感的光线，浅景深。",
    "warm, muted tones.": "色调温暖、低饱和。",
    "realistic skin texture and fabric rendering.": "皮肤质感与布料质感写实。",
    "the frame is tightly cropped around the subjects, with the background blurred.":
        "画面紧贴主体裁切，背景虚化。",
    "the perspective is even, with no strong tilt.": "视角端正，没有明显的倾斜。",
    "light falls softly on the subjects' faces and clothing, creating gentle shadows.":
        "光线柔和地落在人物面部与衣物上，形成柔和的阴影。",
    "the background is dimly lit with bokeh from distant lights.":
        "背景较暗，远处灯光散成光斑。",
    "the four panels are arranged in a 2x2 grid.": "四个画格按二乘二网格排布。",
    "the eye moves from the wide shot to the close-ups, creating a narrative flow.":
        "视线由远景移向特写，形成叙事流动感。",
    "high-contrast ink lines, clean hatching for shading, minimal cross-hatching, "
    "strong outlines defining forms and clothing folds.":
        "高对比墨线，用干净的排线表现阴影，交叉排线很少，轮廓线强烈，"
        "明确形体与衣褶。",
    "smooth, even lighting with minimal shadowing.": "光线平滑均匀，阴影很少。",
    "clean, digital rendering with soft skin texture and subtle grain.":
        "干净的数字渲染，皮肤质感柔和，带细微颗粒。",
    "tight crop on the head and shoulders, background is softly blurred.":
        "紧贴头部与肩部裁切，背景柔和虚化。",
    "the frame is centered on the face with no negative space.":
        "画面以面部为中心，没有留白。",
    "even illumination with no harsh shadows.": "照明均匀，没有生硬阴影。",
    "highlights are soft on the forehead and cheekbones.":
        "额头与颧骨的高光柔和。",
    "skin has a matte finish.": "皮肤呈哑光质感。",
    "the subject is centered in the frame, occupying the majority of the space.":
        "主体位于画面中央，占据大部分空间。",
    "the eye is drawn directly to the face due to the shallow depth of field.":
        "浅景深使视线直接落在面部。",
    "flat ui with crisp edges, dark theme, subtle anti-aliasing on text and borders.":
        "扁平化界面，边缘清晰，深色主题，文字与边框有细微抗锯齿。",
}

#: 景别。`medium` 在 shot 与 dof 里意思完全不同，所以分表，不塞 _EXACT。
_SHOT = {"medium shot": "中景", "medium": "中景", "close up": "特写",
         "close-up": "特写", "closeup": "特写", "full shot": "全景",
         "wide": "远景", "wide shot": "远景", "extreme close-up": "大特写",
         "extreme close up": "大特写", "long shot": "远景", "establishing shot": "交代镜头"}
_ANGLE = {"eye level": "平视", "eye_level": "平视", "low": "低角度", "low angle": "低角度",
          "high": "高角度", "high angle": "高角度", "bird's eye": "俯视",
          "worm's eye": "仰视", "dutch": "荷兰角"}
_DOF = {"shallow": "浅", "deep": "深", "medium": "中等", "large": "大", "small": "小"}
_ALIGN = {"center": "居中排列", "centre": "居中排列", "middle": "居中排列",
          "left": "左对齐", "right": "右对齐", "justify": "两端对齐"}

#: 构图规则：只翻「规则名」，前缀「构图采用」由拼装层加。
_RULE = {"centered": "居中", "center": "居中", "centre": "居中",
         "grid layout": "网格布局", "rule of thirds": "三分法",
         "symmetrical": "对称", "diagonal": "对角", "golden ratio": "黄金比例"}

#: 关系谓词 → 中文句子模板（{a} / {b} 由实体短指代填）。
_PRED = {
    "holding": "{a}拿着{b}",
    "touching": "{a}触碰{b}",
    "facing": "{a}面向{b}",
    "left_of": "{a}在{b}的左边",
    "right_of": "{a}在{b}的右边",
    "above": "{a}在{b}的上方",
    "below": "{a}在{b}的下方",
    "behind": "{a}在{b}的后面",
    "in_front_of": "{a}在{b}的前面",
    "occluding": "{a}部分遮挡{b}",
}

#: 方位短语。fixtures 里出现过的整串优先；没出现过的走 `_anchor_zh` 的组合规则。
_ANCHOR = {
    "left foreground": "画面左前景",
    "right foreground": "画面右前景",
    "center": "画面中央",
    "left-center": "画面左侧居中",
    "top-center": "画面上方居中",
    "bottom-right": "画面右下方",
    "middle-left": "画面中部偏左",
    "middle-right": "画面中部偏右",
    "upper right": "画面右上方",
    "lower right": "画面右下方",
    "left half": "画面左半边",
    "right half": "画面右半边",
    "left third, midground": "画面左侧三分之一处的中景",
    "left third, foreground": "画面左侧三分之一处的前景",
    "left panel, center": "左侧画格中央",
    "right bottom panel, center-right": "右下方画格中央偏右",
    "bottom right panel, center-right": "右下方画格中央偏右",
    "top right panel, center-right": "右上方画格中央偏右",
    "bottom left panel, center-left": "左下方画格中央偏左",
}

#: 长句里出现频率最高的人称代词的贴近写法（兜底翻译用）。
_PRONOUN = {"her": "她的", "his": "他的", "her face": "她的脸"}


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def _drop_article(text: str) -> str:
    """英文冠词只对英文原文有意义；翻译成中文前先去掉。"""
    return re.sub(r"\b(a|an|the)\b\s*", "", (text or "").strip(), flags=re.I)


# ─────────────────────────────────────────────────────────────
# 2. 词表翻译（兜底）：词级拼接 + 未译词点名
# ─────────────────────────────────────────────────────────────

#: 词级兜底用的切词。数字带单位（50mm / 2x2）整块保留。
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d+(?:\.\d+)?(?:\s*[A-Za-z%]{1,4})?|[^\sA-Za-z0-9]+")
_UNIT = re.compile(r"\d+(?:\.\d+)?(?:\s*[A-Za-z%]{1,4})?$")


def translate(text: str) -> tuple[str, list[str]]:
    """字段值的兜底翻译。返回 (中文, 未译英文词列表)。确定性，无模型。

    只做「词/短语级」替身：英文语序在兜底路径上不做重排（那需要真解析），
    所以**结构化字段必须走模板**，这里只服务 notes / 背景这类自由散文。
    未译词原样保留并点名——静默丢词会让事实核对失效。
    """
    raw = (text or "").strip()
    if not raw:
        return "", []
    if _has_cjk(raw):                       # 已经是中文：不二次翻译
        return raw, []
    key = _norm(raw)
    if key in _EXACT:
        return _EXACT[key], []
    # 整句表
    if key in _SEN:
        return _SEN[key], []

    unknown: list[str] = []
    out: list[str] = []
    i = 0
    words = _TOKEN.findall(raw)
    while i < len(words):
        matched = None
        for n in (4, 3, 2):
            if i + n > len(words):
                continue
            cand = _norm(" ".join(words[i:i + n]))
            if cand in _EXACT:
                matched = (_EXACT[cand], n)
                break
            if cand in _WORDS:
                matched = (_WORDS[cand], n)
                break
        if matched:
            out.append(matched[0])
            i += matched[1]
            continue
        w = words[i]
        low = w.lower()
        if low in _WORDS:
            out.append(_WORDS[low])
        elif _UNIT.match(w) or re.match(r"^\d", w) or w in ("BREAK", "mm", "%"):
            out.append(w)
        elif re.match(r"^[^\sA-Za-z0-9]+$", w):     # 标点
            out.append("，" if w in ",;." else ("" if w == "'" else w))
        else:
            unknown.append(w)
            out.append(w)
        i += 1
    # 中文之间不加空格；拉丁词之间保留一个空格
    result = ""
    for piece in out:
        if not piece:
            continue
        if (re.match(r"[A-Za-z0-9]", piece) and result
                and re.search(r"[A-Za-z0-9]$", result)):
            result += " "
        result += piece
    return result.strip(), unknown


def _tr(text: str) -> str:
    """翻译一段文本，丢掉「未译」信息（模板层不需要逐词追账）。"""
    return translate(text)[0]


# ─────────────────────────────────────────────────────────────
# 3. 字段模板（结构化字段的整句重排）
# ─────────────────────────────────────────────────────────────

_GENDER_NOUN = {
    "woman": "女人", "man": "男人", "girl": "女孩", "boy": "男孩",
    "female": "女人", "male": "男人", "女性": "女人", "男性": "男人",
    "女孩": "女孩", "男孩": "男孩", "女": "女人", "男": "男人",
    "女子": "女人", "男子": "男人", "女人": "女人", "男人": "男人",
}

#: 动作模板：整串 → (谓语, 附加小句, 是否已自带方位)。
_POSE = {
    "sitting": ("坐着", [], False),
    "standing": ("站着", [], False),
    "sitting, hands holding a glass": ("坐着", [], False),
    "sitting, hands holding a cup": ("坐着", [], False),
    "sitting, writing with right hand, left hand resting on paper":
        ("坐着", ["右手写字", "左手搭在纸上"], False),
    "standing, holding umbrella with right hand, left hand in pocket":
        ("站着", ["左手插在口袋里"], False),
    "head slightly tilted forward, shoulders relaxed":
        ("", ["头部微微前倾", "双肩放松"], False),
    "standing with her weight on one leg": ("单腿站着", [], False),
    "standing still under the awning": ("在雨棚下静静站着", [], True),
    "walking toward her": ("朝她走去", [], False),
}

#: 常见服装名词（用于区分「衣物」与「面料描述」）。
_CLOTHING = re.compile(r"(衣|衫|裤|裙|鞋|靴|外套|夹克|制服|校服|风衣|袄|帽|袜|手套|恤|袍)")

#: 手持道具。不在表里的按「旁边放着」处理（宁可少写「拿着」也别写错）。
_HELD_PROPS = {"glass of lemonade", "white cup", "pen", "notebook", "umbrella", "cup",
               "glass"}


def _noun_zh(e: Entity) -> str:
    """人物名词。与英文 `_age_phrase` 同口径：gender 优先，label 只在短名词时兜底。"""
    gender = _norm(e.descriptors.get("gender") or "")
    if gender in _GENDER_NOUN:
        return _GENDER_NOUN[gender]
    label = _norm(e.label or "")
    if label and len(label.split()) <= 3:
        tr = _tr(label)
        if _has_cjk(tr):
            return tr
    return "人物"


def _subject_head_zh(e: Entity) -> str:
    noun = _noun_zh(e)
    if e.age_years is None:
        return "一个" + noun
    return "一个%d岁的%s" % (int(e.age_years), noun)


def _pose_zh(text: str) -> tuple[str, list, bool]:
    key = _norm(text)
    if not key:
        return "", [], False
    if key in _POSE:
        return _POSE[key]
    # 泛化：「sitting, X」「standing, X」按逗号拆，首段当谓语，其余当小句
    segs = [s.strip() for s in _DASH_SPLIT.split(text) if s.strip()]
    verb = _tr(segs[0]) if segs else ""
    tails = [_tr(s) for s in segs[1:]]
    return verb, tails, False


def _anchor_zh(anchor: str) -> str:
    """方位短语 → 中文。整串表优先，否则按「水平/垂直/景深/画格」组合。"""
    key = _norm(anchor)
    if not key:
        return ""
    if key in _ANCHOR:
        return _ANCHOR[key]
    low = key
    panel = "panel" in low
    horiz = "right" if re.search(r"\bright\b|right-", low) else (
        "left" if re.search(r"\bleft\b|left-", low) else "")
    vert = ""
    if re.search(r"\bbottom\b|\blower\b", low):
        vert = "下方"
    elif re.search(r"\btop\b|\bupper\b", low):
        vert = "上方"
    center = bool(re.search(r"\bcenter\b|\bcentre\b|\bmiddle\b|center-|-center", low))
    depth = "前景" if "foreground" in low else ("背景" if "background" in low else "")
    third = "三分之一处" if "third" in low else ""
    half = "半边" if "half" in low else ""
    if panel:
        pos = ""
        if horiz:
            pos += "左" if horiz == "left" else "右"
        if vert:
            pos += vert[0] + "方"
        text = (pos + "画格") if pos else "画格"
        offset = ""
        if center:
            offset = "中央"
            if horiz == "right":
                offset += "偏右"
            elif horiz == "left":
                offset += "偏左"
        return text + offset
    bits = ["画面"]
    if horiz:
        bits.append("左" if horiz == "left" else "右")
    if vert:
        bits.append(vert)               # 右上方 / 左下方 / 画面上方
    if center:
        bits.append("居中")
    if third:
        bits.append("三分之一处")
    if half:
        bits.append("半边")
    if depth:
        bits.append(("的" if (third or half) else "") + depth)
    return "".join(bits)


def _wardrobe_zh(items) -> str:
    """服装清单 → 中文。衣物进「穿着」；面料/版型进定语括号；伞/包这类持物进「拿着」。"""
    garments, notes, hold = [], [], []
    for raw in items or []:
        item = _tr(str(raw))
        if not item:
            continue
        if "（" in item or "）" in item:
            garments.append(item)          # 译名里已经带了定语括号，别套两层
            continue
        if _CLOTHING.search(item):
            garments.append(item)
        elif re.match(r"^一[把杯盏支本个张只条]", item):
            hold.append(item)              # 挂在 wardrobe 里的持物，写成「手里拿着」更中文
        else:
            notes.append(item)
    pieces = []
    if garments:
        pieces.append("穿着" + "、".join(garments))
    if hold:
        pieces.append("手里拿着" + "、".join(hold))
    text = "，".join(pieces)
    if notes:
        tail = "（" + "，".join(notes) + "）"
        text = text + tail if text else tail
    return text


def _props_zh(items) -> tuple[list, list]:
    held, placed = [], []
    for raw in items or []:
        key = _norm(raw)
        item = _tr(str(raw))
        if not item:
            continue
        if key in _HELD_PROPS or any(w in key for w in ("pen", "umbrella", "cup", "glass",
                                                        "notebook", "phone", "book")):
            held.append(item)
        else:
            placed.append(item)
    return held, placed


def _color_zh(color: str) -> str:
    return _tr(color)


def describe_entity_zh(e: Entity, with_anchor: bool = True, extras: bool = True) -> str:
    """一个实体 → 中文整句（主体 + 动作 + 方位在前，属性/穿着/手持在后）。"""
    d = e.descriptors
    anchor = (e.spatial or {}).get("anchor") if with_anchor else ""
    if e.kind == "character":
        verb, tails, located = _pose_zh(d.get("pose") or "")
        held, placed = _props_zh(d.get("props") or [])
        text = _subject_head_zh(e)
        if verb:
            text += verb
        if anchor:
            if verb and verb.endswith("着") and "在" not in verb:
                text = text[:-1] + "在" + _anchor_zh(anchor)
            elif verb and not located:
                tails.insert(0, "位于" + _anchor_zh(anchor))
            elif not verb:
                text += "位于" + _anchor_zh(anchor)
            else:
                tails.insert(0, "位于" + _anchor_zh(anchor))
        elif located:
            pass
        parts = [text] + list(tails)
        for field in ("hair", "skin", "face", "expression"):
            v = d.get(field)
            if v:
                tr = _tr(str(v))
                if tr:
                    parts.append(tr)
        wear = _wardrobe_zh(d.get("wardrobe") or [])
        if wear:
            parts.append(wear)
        if held:
            parts.append("手里拿着" + "、".join(held))
        if placed:
            parts.append("旁边放着" + "、".join(placed))
        if extras:
            for field in ("build", "detail"):
                v = d.get(field)
                if v:
                    tr = _tr(str(v))
                    if tr:
                        parts.append(tr)
        return "，".join(p for p in parts if p)
    # 非人物
    label = _tr(e.label or e.kind or "物体")
    color = _color_zh(str(d.get("color") or ""))
    head = (color + label) if color and color not in label else label
    if not head:
        head = "物体"
    parts = ["一个" + head]
    if d.get("material"):
        parts.append("表面为" + _tr(str(d["material"])))
    if extras:
        for field in ("build", "detail"):
            v = d.get(field)
            if v:
                parts.append(_tr(str(v)))
    if anchor:
        parts.append("位于" + _anchor_zh(anchor))
    return "，".join(p for p in parts if p)


def _text_clause_zh(e: Entity) -> str:
    """图内文字：逐字内容用中文/英文引号包住，不复述、不改字。"""
    content = e.text_content or ""
    quote = "「%s」" if _has_cjk(content) else '"%s"'
    body = "画面上的文字是" + (quote % content)
    typ = e.typography or {}
    tails = []
    weight = _norm(typ.get("visual_weight") or "")
    if weight in ("large", "small"):
        tails.append("字号较大" if weight == "large" else "字号偏小")
    if typ.get("weight"):
        tr = _tr(str(typ["weight"]))
        if tr:
            tails.append(tr)
    if typ.get("color"):
        tr = _color_zh(str(typ["color"]))
        if tr:
            tails.append(tr + "字")
    if typ.get("case") and re.search(r"[A-Za-z]", content):
        tr = _tr(str(typ["case"]))
        if tr:
            tails.append(tr)
    if typ.get("align"):
        align = _norm(str(typ["align"]))
        tr = _ALIGN.get(align) or (_tr(align) + "对齐")
        if tr:
            tails.append(tr)
    if typ.get("style_hint"):
        tr = _tr(str(typ["style_hint"]))
        if tr:
            tails.append(tr)
    anchor = (e.spatial or {}).get("anchor")
    if anchor:
        tails.append("位置在" + _anchor_zh(anchor))
    return body + ("，" + "，".join(tails) if tails else "")


def _relation_clause_zh(ir: IR, rel: dict) -> str:
    subj, obj = ir.entity(rel.get("subject", "")), ir.entity(rel.get("object", ""))
    if not subj or not obj:
        return ""
    pred = _norm(rel.get("predicate", ""))
    aliases = {"拿着": "holding", "握着": "holding", "触碰": "touching", "面对": "facing",
               "朝向": "facing", "在左": "left_of", "在右": "right_of", "在上": "above",
               "在下": "below", "在后方": "behind", "在前方": "in_front_of",
               "遮挡": "occluding"}
    pred = aliases.get(pred, pred)
    template = _PRED.get(pred)
    if not template:
        return "%s与%s有位置关系" % (_short_ref_zh(ir, subj), _short_ref_zh(ir, obj))
    return template.format(a=_short_ref_zh(ir, subj), b=_short_ref_zh(ir, obj))


def _short_ref_zh(ir: IR, e: Entity) -> str:
    """关系句里的短指代：方位 + 名词。不复述外观（英文路径同一条纪律）。

    方位短语太长时只留名词：`画面左侧三分之一处的中景的女孩` 会堆两层「的」，
    读起来像机翻；主语句里已经交代过位置，关系句不需要再背一遍。
    """
    noun = _noun_zh(e) if e.kind == "character" else _tr(e.label or e.kind or "物体")
    anchor = (e.spatial or {}).get("anchor")
    if anchor:
        ref = _anchor_zh(anchor)
        if len(ref) <= 8:
            return ref + "的" + noun
    return noun


def _camera_clause_zh(cam: dict) -> str:
    bits = []
    if cam.get("shot"):
        shot = _norm(str(cam["shot"]).replace("_", " "))
        bits.append(_SHOT.get(shot) or _tr(shot))
    if cam.get("angle"):
        ang = _norm(str(cam["angle"]).replace("_", " "))
        bits.append(_ANGLE.get(ang) or _tr(ang))
    if cam.get("lens_mm"):
        bits.append("%dmm 镜头" % int(cam["lens_mm"]))
    if cam.get("dof"):
        dof = _norm(str(cam["dof"]))
        bits.append(_DOF.get(dof, _tr(dof)) + "景深")
    if cam.get("focus"):
        bits.append("对焦在" + _tr(str(cam["focus"])))
    if cam.get("motion"):
        tr = _tr(str(cam["motion"]))
        if tr:
            bits.append(tr)
    return "，".join(b for b in bits if b)


def _lighting_clause_zh(lit: dict) -> str:
    bits = []
    for key in ("key", "fill"):
        if lit.get(key):
            tr = _tr(str(lit[key]))
            if tr:
                bits.append(tr)
    if lit.get("time_of_day"):
        tr = _tr(str(lit["time_of_day"]))
        if tr:
            bits.append(tr)
    if lit.get("mood"):
        tr = _tr(str(lit["mood"]))
        if tr:
            bits.append("氛围" + tr)
    return "，".join(bits)


def _note_zh(text: str) -> str:
    """观察散文 → 中文：先逐句查表，再整段兜底；句间用「。」。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _has_cjk(raw):
        return raw
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", raw) if s.strip()]
    out = []
    for s in sentences:
        tr = _tr(s)
        if not tr:
            continue
        out.append(tr.rstrip("。；，") + "。")
    return "".join(out)


def _cjk_bigrams(text: str) -> set:
    chars = _CJK.findall(text or "")
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def _dedupe_note_zh(note: str, fact_text: str, threshold: float = 0.45) -> str:
    """丢掉与事实重合度高的**句子**（中文按字二元组算重合，不装分词器）。"""
    fact = _cjk_bigrams(fact_text)
    if not fact:
        return note
    kept = []
    for sent in re.split(r"(?<=[。！？])", note.strip()):
        want = _cjk_bigrams(sent)
        if not want:
            continue
        hit = want & fact
        if len(hit) >= 2 and len(hit) / float(len(want)) >= threshold:
            continue
        kept.append(sent.strip())
    return "".join(kept)


# ─────────────────────────────────────────────────────────────
# 4. 子句计划（中文版 build_clauses）
# ─────────────────────────────────────────────────────────────

_NOTE_DIM = {"subject": "subject", "setting": "setting", "composition": "setting",
             "camera": "camera", "lighting": "lighting", "material": "material",
             "medium": "medium", "text": "text"}
_NOTE_IMP = {"subject": 0.87, "setting": 0.82, "camera": 0.77,
             "lighting": 0.82, "material": 0.47, "medium": 0.57, "text": 0.94}


def build_clauses_zh(ir: IR) -> list[Clause]:
    """中文子句计划。维度、重要度、去重口径与英文版一致；句子换中文模板。"""
    out: list[Clause] = []

    # 1. 主体（同形实体归并，重复出现改成一句方位补充）
    groups: dict[str, list[Entity]] = {}
    order: list[str] = []
    for e in ir.entities:
        if e.kind == "text":
            continue
        key = describe_entity_zh(e, with_anchor=False)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)
    for key in order:
        group = groups[key]
        if len(group) == 1:
            out.append(Clause("subject", describe_entity_zh(group[0]),
                              group[0].importance, group[0].id))
            continue
        # 归并键不含方位（不然同一个人换个位子就成了两个主体）；但中文正文里
        # **第一个**仍带方位落地——「一个28岁的女人坐在画面左前景」比
        # 「一个28岁的女人坐着」先交代位置更像中文。英文路径的取舍保持原样。
        out.append(Clause("subject", describe_entity_zh(group[0]),
                          group[0].importance, group[0].id))
        rest = [(e.spatial or {}).get("anchor") for e in group[1:]]
        if all(rest):
            out.append(Clause("setting",
                              "同样的人物再次出现在" + "、".join(_anchor_zh(a) for a in rest),
                              group[0].importance * 0.8))
        else:
            out.append(Clause("setting", "同样的人物再次出现在画面另一处",
                              group[0].importance * 0.8))

    # 2. 关系（排序口径与英文一致：数组顺序不是语义）
    for rel in sorted(ir.relations, key=lambda r: (str(r.get("subject", "")),
                                                   str(r.get("predicate", "")),
                                                   str(r.get("object", "")))):
        text = _relation_clause_zh(ir, rel)
        if not text:
            continue
        subj = ir.entity(rel.get("subject", ""))
        obj = ir.entity(rel.get("object", ""))
        if subj is None or obj is None:
            continue
        imp = min(subj.importance, obj.importance)
        out.append(Clause("setting", text, imp, rel.get("subject")))

    # 3. 场景
    setting_parts = []
    if ir.canvas.get("background"):
        setting_parts.append(_tr(str(ir.canvas["background"])))
    for e in ir.entities:
        if e.kind == "background":
            setting_parts.append(_tr(e.label))
    comp = ir.composition
    rules = [_norm(str(r).replace("_", " ")) for r in (comp.get("rules") or [])]
    if rules:
        setting_parts.append("构图采用" + "、".join(_RULE.get(r) or _tr(r) for r in rules))
    if comp.get("horizon"):
        setting_parts.append(_tr(str(comp["horizon"])))
    if comp.get("negative_space"):
        setting_parts.append(_tr(str(comp["negative_space"])) + "留白")
    if any(setting_parts):
        out.append(Clause("setting", "，".join(p for p in setting_parts if p), 0.9))

    # 4. 镜头
    cam = ir.camera
    if cam:
        text = _camera_clause_zh(cam)
        if text:
            out.append(Clause("camera", text, 0.8))

    # 5. 光照
    lit = ir.lighting
    if lit:
        text = _lighting_clause_zh(lit)
        if text:
            out.append(Clause("lighting", text, 0.85))

    # 6. 材质（主体描述里已经写过的材质不再复述）
    mats = []
    for e in ir.entities:
        m = e.descriptors.get("material")
        if not m:
            continue
        tr = _tr(str(m))
        if tr and tr in describe_entity_zh(e):
            continue
        mats.append(tr)
    if mats:
        out.append(Clause("material", "材质为" + "、".join(mats), 0.5))

    # 7. 氛围
    if ir.atmosphere:
        tr = [_tr(str(a)) for a in ir.atmosphere if str(a).strip()]
        tr = [t for t in tr if t]
        if tr:
            out.append(Clause("lighting", "整体氛围为" + "、".join(tr), 0.4))

    # 8. 媒介 / 风格
    st = ir.style
    if st:
        bits = []
        if st.get("medium"):
            bits.append(_tr(str(st["medium"])))
        if st.get("substyle"):
            subs = [_tr(str(s)) for s in st["substyle"] if str(s).strip()]
            subs = [s for s in subs if s]
            if subs:
                bits.append("、".join(subs))
        refs = [r["name"] for r in (st.get("artist_refs") or [])
                if r.get("policy", "allowed") != "blocked"]
        if refs:
            bits.append("参考" + "、".join(refs) + "的画风")
        if bits:
            out.append(Clause("medium", "，".join(b for b in bits if b), 0.6))

    # 9. 图内文字（逐字，单独成句）
    for e in ir.text_entities:
        out.append(Clause("text", _text_clause_zh(e),
                          max(0.95, e.importance), e.id))

    # 10. 观察散文：与全部事实去重后追加
    fact_text = " ".join(c.text for c in out)
    for key in sorted(ir.notes or {}):
        text = ir.notes[key]
        dim = _NOTE_DIM.get(key)
        if not dim or not str(text).strip():
            continue
        trimmed = _dedupe_note_zh(_note_zh(str(text)), fact_text)
        trimmed = trimmed.strip("，。；")
        if trimmed:
            out.append(Clause(dim, trimmed, _NOTE_IMP.get(dim, 0.5), None, "note"))

    out.sort(key=lambda c: (-c.importance,
                            DIM_ORDER.index(c.dim) if c.dim in DIM_ORDER else 99,
                            0 if c.source == "fact" else 1))
    return out


# ─────────────────────────────────────────────────────────────
# 5. 句子拼装
# ─────────────────────────────────────────────────────────────

def render_prose_zh(clauses: list[Clause],
                    dim_order: tuple[str, ...] = DIM_ORDER) -> str:
    """中文散文：按维度顺序成句，维度内用「，」连事实，句间「。」。

    与英文 `render_prose()` 同一条纪律：note 是完整句子，不参与逗号连接
    （否则句中冒出第二个句子的主语，读起来像拼接故障）。
    """
    buckets: dict[str, list[Clause]] = {}
    for c in clauses:
        buckets.setdefault(c.dim, []).append(c)

    sentences: list[str] = []
    order = list(dim_order) + [d for d in buckets if d not in dim_order]
    for dim in order:
        items = buckets.get(dim)
        if not items:
            continue
        # 同维度多个主体用「；」分句（一个28岁的女人…；一个28岁的男人…），
        # 其余维度仍是逗号并列——这是中文并列成分的常规写法，不是英文逗号链。
        joiner = "；" if dim == "subject" else "，"
        buf: list[str] = []
        for c in items:
            text = (c.text or "").strip().rstrip("。")
            if not text:
                continue
            if c.source == "note":
                if buf:
                    sentences.append("，".join(buf))
                    buf = []
                sentences.append(text)
            else:
                buf.append(text)
        if buf:
            sentences.append(joiner.join(buf))
    if not sentences:
        return ""
    return "。".join(s for s in sentences if s) + "。"


def render_tags_zh(clauses: list[Clause],
                   dim_order: tuple[str, ...] = DIM_ORDER) -> str:
    """中文 tag 方言：全角逗号分隔，不收句号；不发明权重。"""
    buckets: dict[str, list[Clause]] = {}
    for c in clauses:
        buckets.setdefault(c.dim, []).append(c)
    parts: list[str] = []
    seen = ""
    for dim in list(dim_order) + [d for d in buckets if d not in dim_order]:
        for c in buckets.get(dim, []):
            text = (c.text or "").strip().rstrip("。；，").strip()
            if not text:
                continue
            if text.lower() in seen:
                continue
            parts.append(text)
            seen += " " + text.lower()
    return "，".join(parts)


# ─────────────────────────────────────────────────────────────
# 6. 适配器接线
# ─────────────────────────────────────────────────────────────

def resolve_language(lang: str | None, registry, name: str) -> tuple[bool, str]:
    """决定这次编译走不走中文路径。

    优先级：显式 `lang` > registry 的 `prompt.language`（**只读，不改结构**）。
    时间线体裁（H3）自带中文骨架，不走本模块——它 `prompt.language` 写的是
    `zh+en`（格式英文、内容中文），那是另一套骨架，不能当图像后端的 zh 用。
    """
    policy = registry.prompt_policy(name) or {}
    mode = _norm(str(policy.get("mode") or "prose"))
    target_lang = _norm(str(policy.get("language") or "en"))
    explicit = _norm(lang or "")
    if explicit in ("zh", "zh+en"):
        use = True
    elif explicit in ("en",):
        use = False
    else:
        use = target_lang in ("zh", "zh+en")
    if not use:
        return False, ""
    if mode == "timeline":
        return False, ("该后端是时间线体裁（自带中文骨架），--lang / registry language "
                       "不改变它的渲染")
    return True, ""


def realize_zh(adapter, ir: IR, profile: str | None = None):
    """中文路径的 realize：英文路径只用来取负向/采样/能力守卫，正文整条换成中文。

    `Realized` 的字段与英文路径一致（positive / negative / params / warnings /
    dropped / token_count / token_limit / mode），所以下游 API / 节点不用改。
    """
    from .adapters.base import Realized
    from .tokens import HeuristicCounter

    caps = adapter.caps()
    policy = caps.get("prompt") or {}
    form = _norm(str(policy.get("mode") or "prose"))
    encoder = caps.get("encoder") or {}
    chunking = _norm(str(encoder.get("chunking") or ""))
    counter = getattr(adapter, "counter", None) or HeuristicCounter()
    adapter.counter = counter

    base = adapter.realize(ir, profile=profile, mode="deterministic")
    # 布局 JSON / 编辑指令不是「一句话」，不适合整句重排：原样交出，并说明原因。
    if base.layout is not None or ir.task in ("edit", "inpaint", "outpaint"):
        base.warnings.append("中文整句路径不改变 Layout JSON / 编辑指令形态（原样输出）")
        base.zh_applied = False
        return base

    out = Realized(target=adapter.name, mode="deterministic")
    out.token_limit = base.token_limit
    out.negative = base.negative
    out.params = base.params
    out.zh_applied = True
    # base 的篇幅/分块警告是拿**英文**正文算的，中文正文换了字数与切块口径，
    # 留着会误读（同一条预算会被报两遍，且块号还按英文算）。
    out.warnings = [w for w in base.warnings
                    if "预算" not in w and "下界" not in w and "偏长" not in w
                    and "偏好短" not in w and "块" not in w]

    clauses = build_clauses_zh(ir)
    limit = adapter.token_limit(ir)
    kept, dropped = pack(clauses, limit, counter)
    if dropped:
        out.dropped = ["%s:%s" % (c.dim, c.text[:40]) for c in dropped]
        out.warnings.append("预算 %s token，按重要度砍掉 %d 条低权子句"
                            % (limit, len(dropped)))

    if chunking and "break" in chunking:
        # BREAK 类是**分块**方言：77 是每块上限，不是整段上限，不能装箱砍。
        budget = (int(encoder.get("max_tokens") or 77)) - 2
        text, notes = _chunk_tags_zh(kept, adapter.dim_order, counter, budget)
        out.warnings.extend(notes)
    elif form == "tag":
        text = render_tags_zh(kept, adapter.dim_order)
    else:
        text = render_prose_zh(kept, adapter.dim_order)

    out.positive = text
    out.token_count = counter.count(text)

    unknown = _untranslated_words(text)
    if unknown:
        out.warnings.append("中文路径：%d 处英文词不在词表内，已保留原文（%s）"
                            % (len(unknown), "、".join(sorted(unknown)[:8])))
    if ir.notes:
        out.warnings.append("中文路径：观察散文为词表机翻，未经母语审校")
    return out


def _chunk_tags_zh(clauses: list[Clause], dim_order, counter, budget: int):
    """按子句边界切块（SDXL 的 77/块）：切，不砍。"""
    chunks: list[list[Clause]] = [[]]
    used = 0
    for c in clauses:
        cost = counter.count(c.text) + 1
        if chunks[-1] and used + cost > budget:
            chunks.append([c])
            used = cost
            continue
        chunks[-1].append(c)
        used += cost
    if chunks and not chunks[0]:
        chunks = chunks[1:]
    notes, rendered = [], []
    for i, chunk in enumerate(chunks):
        text = render_tags_zh(chunk, dim_order)
        if counter.count(text) > budget:
            notes.append("第 %d 块 %d token 超过单块上限 %d：这条子句无法再切，"
                         "编码器会截断它的尾部" % (i + 1, counter.count(text), budget))
        rendered.append(text)
    if len(rendered) > 1:
        notes.append("已切成 %d 块（每块 ≤%d token，BREAK 分隔）"
                     % (len(rendered), budget))
    return " BREAK ".join(rendered), notes


def _untranslated_words(text: str) -> set:
    """中文正文里残留的拉丁词（引号内容不算）。用于 warnings 与机检。"""
    masked = _QUOTED_SPAN.sub(" ", text or "")
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", masked):
        if w.lower() in ("break", "mm", "cm", "px"):
            continue
        out.add(w)
    return out


# ─────────────────────────────────────────────────────────────
# 7. 机检：无英文语序（可单测，不靠人眼）
# ─────────────────────────────────────────────────────────────

#: 英文功能词：出现即说明句子骨架还是英文的。
_EN_FUNC = re.compile(
    r"\b(a|an|the|of|with|and|or|in|on|at|to|for|from|by|is|are|was|were|be|been|"
    r"this|that|these|those|it|its|as|into|over|under|near|behind|very|more|most|"
    r"some|any|all|not|no)\b", re.I)

#: 中文句子里不该出现的 ASCII 标点（数字小数点除外）。
_ASCII_PUNCT = re.compile(r"[,.;:!?()\[\]]")
_DE_STACK = re.compile(r"的[^，。；、！？]{0,4}的")
_BAD_HEAD = re.compile(r"^\s*(的|地|得|了|着|是)")


def _ir_allowed_latin(ir: IR | None) -> set:
    """IR 里「本来就该保留原样」的拉丁串：图内文字、画师署名、must_keep。"""
    allowed = set()
    if ir is None:
        return allowed
    for e in ir.entities:
        for line in (e.text_content or "").splitlines():
            for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", line):
                allowed.add(w.lower())
        for k in e.must_keep or []:
            for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(k)):
                allowed.add(w.lower())
    for r in (ir.style.get("artist_refs") or []):
        for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(r.get("name") or "")):
            allowed.add(w.lower())
    for c in (ir.constraints.get("fixed") or []) if isinstance(ir.constraints, dict) else []:
        for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(c)):
            allowed.add(w.lower())
    return allowed


def _mask_for_check(text: str, ir: IR | None) -> str:
    """把「允许的英文」挖空：引号内容、IR 原文、技术单位、BREAK 分隔符。"""
    masked = _QUOTED_SPAN.sub(" ", text or "")
    for w in sorted(_ir_allowed_latin(ir), key=len, reverse=True):
        masked = re.sub(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(w), " ", masked,
                        flags=re.I)
    masked = re.sub(r"\bBREAK\b", " ", masked)
    return re.sub(r"\d+(?:\.\d+)?\s*(?:mm|cm|px|%|k)\b", " ", masked, flags=re.I)


def zh_issues(text: str, ir: IR | None = None,
              dim_order: tuple[str, ...] | None = None) -> list[str]:
    """「无英文语序」的机检规则。返回问题列表（空 = 通过）。

    规则（与 docs/28 一一对应）：
      Z01 英文功能词残留（a / the / of / with / in / on …）
      Z02 未翻译的英文词残留
      Z03 中文里混入 ASCII 标点（引号内的逐字文字除外）
      Z04 「的」字堆叠（英文 `of the … of the` 的直译痕迹）
      Z05 句首虚词（的/地/得/了/着/是 起手 = 半截句）
      Z06 角色年龄/量词缺失（英文式 age 表达不认）
      Z07 主语起手：默认散文方言（subject 在第一维）要求**首句**出现角色名词；
          方言把别的维度排在主语前面（Krea 2 的 medium、SDXL 的 tag 分块）时，
          退化成「角色名词出现在前 60% 位置」，不拿一种语序当全局真理。

    `dim_order` 传方言的维度顺序：判据要认方言，不能拿一种语序当全局真理。
    传空元组 `()` 表示「这块方言不保证句子顺序」（tag 分块），走宽松判据。
    """
    issues: list[str] = []
    text = (text or "").strip()
    if not text:
        return ["Z05 空文本"]
    masked = _mask_for_check(text, ir)

    funcs = sorted({m.group(0).lower() for m in _EN_FUNC.finditer(masked)})
    if funcs:
        issues.append("Z01 英文功能词残留：%s" % "、".join(funcs))
    latin = sorted({w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", masked)
                    if w.lower() not in ("break", "mm", "cm", "px")})
    if latin:
        issues.append("Z02 未翻译英文词残留：%s" % "、".join(latin))
    punct = sorted({m.group(0) for m in _ASCII_PUNCT.finditer(masked)})
    if punct:
        issues.append("Z03 中文里混入 ASCII 标点：%s" % " ".join(punct))
    if _DE_STACK.search(masked):
        issues.append("Z04 「的」字堆叠：%r" % _DE_STACK.search(masked).group(0))
    if _BAD_HEAD.match(text):
        issues.append("Z05 句首虚词起手：%r" % text[:6])
    sentences = [s.strip() for s in text.split("。") if s.strip()]
    if not _CJK.match(text):
        issues.append("Z07 首句不以中文起手：%r" % text[:12])
    if ir is not None:
        chars = [e for e in ir.entities if e.kind == "character"]
        if chars:
            aged = [e for e in chars if e.age_years]
            if aged and not re.search(r"\d+\s*岁", text):
                issues.append("Z06 角色年龄未以中文写出（缺「N岁」）")
            if not re.search(r"[个位名群]", text):
                issues.append("Z06 缺中文量词（个/位/名/群）")
            nouns = [_noun_zh(e) for e in chars]
            strict = dim_order is None or (len(dim_order) > 0 and dim_order[0] == "subject")
            if strict:
                if not any(n in (sentences[0] if sentences else "") for n in nouns):
                    issues.append("Z07 首句不是主语起手（首句未见 %r）" % "/".join(nouns))
            else:
                pos = min([text.find(n) for n in nouns if text.find(n) >= 0] or [-1])
                if pos < 0:
                    issues.append("Z07 输出里没有角色名词：%s" % "/".join(nouns))
                elif pos > len(text) * 0.6:
                    issues.append("Z07 角色主语来得太晚（第 %d 字 / 共 %d 字）"
                                  % (pos, len(text)))
    return issues


# ─────────────────────────────────────────────────────────────
# 8. 事实对账：中文面 → 英文规范词面
# ─────────────────────────────────────────────────────────────

#: 颜色中文 → 英文规范词（expand.COLORS 的子集；渲染时会原样出现在正文里）。
_COLOR_BACK = {
    "黑色": "black", "白色": "white", "深色": "dark", "浅色": "light", "灰色": "gray",
    "棕色": "brown", "橙色": "orange", "黄色": "yellow", "红色": "red", "红": "red",
    "蓝色": "blue", "绿色": "green", "银色": "silver", "金色": "gold", "米色": "beige",
    "奶油色": "cream", "藏青色": "navy", "深绿色": "green",
}

#: 方位英文 → 中文出现形态。用于把中文方位面还原成 `expand.DIRECTIONS` 的词。
_DIRECTION_BACK = {
    "left": ("左侧", "左前景", "左半边", "左方", "左边", "偏左", "中左", "左三分之一",
             "画面左"),
    "right": ("右上方", "右下方", "右侧", "右前景", "右半边", "右方", "右边", "偏右",
              "中右", "右三分之一", "画面右"),
    "top": ("上方", "上缘", "顶部", "上部"),
    "upper": ("上方", "上缘"),
    "bottom": ("下方", "下缘", "底部", "下部"),
    "lower": ("下方", "下缘", "底部", "下部"),
    "center": ("中央", "居中", "中部", "中偏"),
    "centre": ("中央", "居中", "中部"),
    "middle": ("中部", "中央", "居中"),
    "foreground": ("前景",),
    "background": ("背景",),
    "corner": ("角",),
    "edge": ("边缘",),
    "third": ("三分之一",),
    "half": ("半边",),
}


def fact_surface(text: str, ir: IR | None = None) -> str:
    """把中文 prompt 里的颜色/方位面归一化回英文规范词，供 `roundtrip.diff()` 核对。

    为什么需要它：`roundtrip` 的固定项命中器只认英文词表（`expand.COLORS` /
    `expand.DIRECTIONS`）。中文渲染器的输出本身没丢事实，直接丢进英文判据会
    把「黄色」误报成「丢失黄色」。这个函数只做**同一条事实换面**，不添加事实。
    """
    flat = text or ""
    extra: list[str] = []
    for zh, en in _COLOR_BACK.items():
        if zh in flat:
            extra.append(en)
    for en, aliases in _DIRECTION_BACK.items():
        if any(a in flat for a in aliases):
            extra.append(en)
    if not extra:
        return flat
    return flat + " " + " ".join(sorted(set(extra)))
