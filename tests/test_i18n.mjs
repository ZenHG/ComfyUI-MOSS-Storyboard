// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// MOSS Storyboard · 中英切换的**纯函数离线单测**（零依赖、零浏览器）：
//     node tests/test_i18n.mjs
//
// 测的是**风险点**，不是覆盖率：
//   ① 只改 label / title / tooltip，**绝不改 name / value**（改了 = 改图）
//   ② 用户自定义标题不许被覆盖；与其它模块共存时只换前缀、后缀保留
//   ③ 枚举值白名单外不翻（翻了 = 存档值语义漂移）
//   ④ 悬浮提示：**中文一律不写**（唯一源在后端 nodes.py），只写 en
//   ⑤ 幂等：同一语言连应用两次，第二次零改动
//   ⑥ 词表完整性（新增参数忘了翻译）由 `tests/test_contract.py` 在 Python 侧核对

import {
    NODES, LABELS, COMBOS, TIPS, LANG_KEY, LANG_EVENT,
    normLang, defaultLang, defaultTitles, isDefaultTitle, swapTitle,
    comboLabel, applyCombo, pickTip, tipFor, applyLang, ownNodes,
} from "../web/moss_sb_i18n.js";

let pass = 0;
const fails = [];

function check(name, cond, detail = "") {
    if (cond) { pass += 1; console.log("  [OK]   " + name + (detail ? "  " + detail : "")); }
    else { fails.push(name); console.log("  [FAIL] " + name + (detail ? "  " + detail : "")); }
}

/** 造一个"像 litegraph 节点"的纯对象（**不碰 DOM**）。 */
function mkNode(type, widgets = [], extra = {}) {
    return Object.assign({
        type,
        title: (NODES[type] || {}).en,
        widgets: widgets.map((w) => Object.assign({ options: { values: w.values || [] } }, w)),
    }, extra);
}

console.log("MOSS Storyboard · i18n 纯函数单测");
console.log("=".repeat(64));

// ---- 1. 语言归一 / 默认语言 ----
check("1.1 normLang 只认 zh/en",
    normLang("zh-CN") === "zh" && normLang("zh_TW") === "zh" && normLang("ZH") === "zh"
    && normLang("en-US") === "en" && normLang("ja") === "en" && normLang(null) === "en"
    && normLang(undefined) === "en" && normLang("") === "en");
check("1.2 defaultLang 跟随宿主", defaultLang(() => "zh-CN") === "zh" && defaultLang(() => "en") === "en");
// 读不到宿主语言时会退到 navigator.language —— 那是**跑测试的机器**的 locale（本机 zh-CN）。
// 断言"等于某个具体语言"会在别人机器上翻红，所以只断言它是合法值 + 非法语言落 en。
check("1.3 defaultLang 读不到时回落为合法语言（不锁死具体值）",
    ["zh", "en"].includes(defaultLang(() => "")) && ["zh", "en"].includes(defaultLang(() => null)),
    "本机 navigator=" + (globalThis.navigator && globalThis.navigator.language));
check("1.4 defaultLang：非法语言码 ⇒ en（宿主常见 fr/ja）",
    defaultLang(() => "fr") === "en" && defaultLang(() => "ja-JP") === "en");

// ---- 2. 词表完整性 ----
const types = Object.keys(NODES);
check("2.1 六个节点都有 zh/en", types.length === 6 && types.every((t) => NODES[t].zh && NODES[t].en),
    types.join(","));
check("2.2 标题 zh 与 en 不相同", types.every((t) => NODES[t].zh !== NODES[t].en));
const tipKeys = Object.keys(TIPS);
check("2.3 提示条数 > 30", tipKeys.length > 30, String(tipKeys.length));
check("2.4 每条提示都有 en、且**没有 zh**（中文唯一源在后端）",
    tipKeys.every((k) => TIPS[k].en && TIPS[k].zh === undefined && TIPS[k].cn === undefined));
check("2.5 提示键都是 `类型.参数` 或 `*.参数` 形式",
    tipKeys.every((k) => /^(\*|Moss\w+)\.\w+$/.test(k)));
check("2.6 提示键里的节点类型都真实存在",
    tipKeys.every((k) => k.startsWith("*.") || NODES[k.split(".")[0]]));
check("2.7 LABELS 都是非空中文串",
    Object.keys(LABELS).length > 20 && Object.values(LABELS).every((v) => typeof v === "string" && v.length > 0));
check("2.8 COMBOS 的值都是字符串（不会误翻数字枚举）",
    Object.values(COMBOS).every((m) => Object.values(m).every((v) => typeof v === "string")));

// ---- 3. 标题 ----
const T = "MossStoryboardSampler";
check("3.1 默认英文名 ⇒ 中文", swapTitle(NODES[T].en, T, "zh") === NODES[T].zh);
check("3.2 默认中文名 ⇒ 英文", swapTitle(NODES[T].zh, T, "en") === NODES[T].en);
check("3.3 空标题 ⇒ 直接填译文", swapTitle("", T, "zh") === NODES[T].zh);
check("3.4 自定义标题 ⇒ 一个字都不动",
    swapTitle("我的采样器", T, "en") === "我的采样器" && swapTitle("my node", T, "zh") === "my node");
check("3.5 默认名 + 后缀 ⇒ 只换前缀、后缀保留",
    swapTitle(NODES[T].en + " · 3 段", T, "zh") === NODES[T].zh + " · 3 段");
check("3.6 未知类型 ⇒ 原样返回", swapTitle("x", "NotMyNode", "zh") === "x");
check("3.7 isDefaultTitle 判定正确",
    isDefaultTitle(NODES[T].en, T) && isDefaultTitle(NODES[T].zh, T)
    && isDefaultTitle("", T) && !isDefaultTitle("自定义", T));
check("3.8 defaultTitles 含 中/英/后端显示名 三种", defaultTitles(T).length === 3,
    JSON.stringify(defaultTitles(T)));

// ---- 4. 枚举显示 ----
check("4.1 白名单内 ⇒ 中文", comboLabel("init", "layout", "zh") === COMBOS.init.layout);
check("4.2 白名单内、英文态 ⇒ 原值", comboLabel("init", "layout", "en") === "layout");
check("4.3 白名单外 ⇒ null（不翻）", comboLabel("init", "unknown-value", "zh") === null
    && comboLabel("sampler_name", "euler", "zh") === null);

// ---- 5. 悬停提示 ----
check("5.1 en 命中", tipFor(T, "steps", "en") === TIPS[T + ".steps"].en);
check("5.2 zh ⇒ null（回落后端中文）", tipFor(T, "steps", "zh") === null);
check("5.3 未收录 ⇒ null", tipFor(T, "not_a_param", "en") === null);
check("5.4 pickTip 精确键优先于通配",
    (() => {
        const before = TIPS["*.steps"];
        TIPS["*.steps"] = { en: "wildcard" };
        const got = pickTip(T, "steps");
        if (before === undefined) delete TIPS["*.steps"]; else TIPS["*.steps"] = before;
        return got && got.en === TIPS[T + ".steps"].en;
    })());

// ---- 6. applyLang 的铁律 ----
const node = mkNode(T, [
    { name: "positive", value: "hello", label: "positive", tooltip: undefined },
    { name: "steps", value: 4, label: "steps", tooltip: undefined },
    { name: "init", value: "layout", label: "init", values: ["auto", "layout", "plain"] },
    { name: "panels", value: 4, label: "panels" },
]);
const namesBefore = node.widgets.map((w) => w.name);
const valuesBefore = node.widgets.map((w) => w.value);

const rEn = applyLang(node, "en");
check("6.1 en：标题变英文", node.title === NODES[T].en && rEn.title === false);
check("6.2 en：label 回落原名", node.widgets[1].label === "steps");
check("6.3 en：tooltip 写入英文", node.widgets[1].tooltip === TIPS[T + ".steps"].en, String(rEn.tips));
check("6.4 en：name 一个都没变", JSON.stringify(node.widgets.map((w) => w.name)) === JSON.stringify(namesBefore));
check("6.5 en：value 一个都没变", JSON.stringify(node.widgets.map((w) => w.value)) === JSON.stringify(valuesBefore));
check("6.6 en：options.values 一个都没变",
    node.widgets[2].options.values.join(",") === "auto,layout,plain");
check("6.7 en：没有残留中文显示钩子", node.widgets[2].options.getOptionLabel === undefined);

const rEn2 = applyLang(node, "en");
check("6.8 幂等：同语言再应用一次零改动",
    rEn2.title === false && rEn2.labels === 0 && rEn2.tips === 0);

const rZh = applyLang(node, "zh");
check("6.9 zh：标题变中文", node.title === NODES[T].zh);
check("6.10 zh：label 变中文", node.widgets[1].label === LABELS.steps);
check("6.11 zh：tooltip 覆盖被撤掉（回落中文）", node.widgets[1].tooltip === undefined);
check("6.12 zh：枚举显示钩子装上", typeof node.widgets[2].options.getOptionLabel === "function");
check("6.13 zh：钩子给出的中文正确", node.widgets[2].options.getOptionLabel("layout") === COMBOS.init.layout);
check("6.14 zh：存档值仍是英文原值", node.widgets[2].value === "layout");
check("6.15 zh 后 name/value 依旧未变",
    JSON.stringify(node.widgets.map((w) => w.name)) === JSON.stringify(namesBefore)
    && JSON.stringify(node.widgets.map((w) => w.value)) === JSON.stringify(valuesBefore));

// ---- 6.5 外来节点：一个字节都不许动（官方 / 别人的节点）----
const foreign = {
    type: "KSampler",
    title: "KSampler",
    widgets: [
        { name: "steps", value: 20, label: "Steps（别人家的标签）", options: { values: [] }, tooltip: "own tip" },
        { name: "cfg", value: 8, label: "CFG", options: { values: [] } },
        { name: "seed", value: 1, label: "Seed", options: { values: [] } },
    ],
};
const snapshot = JSON.stringify(foreign);
const rF = applyLang(foreign, "zh");
check("6.16 外来节点：applyLang 零改动",
    rF.title === false && rF.labels === 0 && rF.combos === 0 && rF.tips === 0, JSON.stringify(rF));
check("6.17 外来节点：标题/label/tooltip 全部原样", JSON.stringify(foreign) === snapshot);
applyLang(foreign, "en");
check("6.18 外来节点：切英文也零改动", JSON.stringify(foreign) === snapshot);
check("6.19 外来节点：不装枚举显示钩子", foreign.widgets[0].options.getOptionLabel === undefined);

// ---- 6.5 多行文本框：标题是 placeholder，不是 label ----
// 真机实证（前端 1.57.1）：`widget.label` 改成了「正面提示词」，界面上仍显示
// `placeholder="positive"` —— DOM `<textarea>` 撑满整个控件，前端根本不画 label。
// 只改 label 的写法对这两个框**永远无效**，所以这里锁住 placeholder。
function fakeTa(init) {
    let ph = init;
    return {
        tagName: "TEXTAREA",
        getAttribute: (k) => (k === "placeholder" ? ph : null),
        setAttribute: (k, v) => { if (k === "placeholder") ph = v; },
    };
}
const mkText = (type, names) => ({
    type,
    title: type,
    widgets: names.map((n) => ({ name: n, value: "", label: n, options: {}, element: fakeTa(n) })),
});
const phOf = (node, n) => node.widgets.find((w) => w.name === n).element.getAttribute("placeholder");

const tn = mkText("MossStoryboardSampler", ["positive", "negative"]);
applyLang(tn, "zh");
check("6.20 中文：positive 的 placeholder 写成「正面提示词」",
    phOf(tn, "positive") === "正面提示词", phOf(tn, "positive"));
check("6.21 中文：negative 的 placeholder 写成「负面提示词」",
    phOf(tn, "negative") === "负面提示词", phOf(tn, "negative"));
applyLang(tn, "en");
check("6.22 英文：placeholder 回滚成原名（不残留中文）",
    phOf(tn, "positive") === "positive" && phOf(tn, "negative") === "negative",
    phOf(tn, "positive") + "/" + phOf(tn, "negative"));

const fta = mkText("KSampler", ["positive"]);
applyLang(fta, "zh");
check("6.23 外来节点的 placeholder 一个字节都不动", phOf(fta, "positive") === "positive");

// ---- 7. 自定义标题在切换中存活 ----
const custom = mkNode("MossStoryboardCrop", [], { title: "我自己的裁格" });
applyLang(custom, "zh");
check("7.1 自定义标题：切中文不动", custom.title === "我自己的裁格");
applyLang(custom, "en");
check("7.2 自定义标题：切英文不动", custom.title === "我自己的裁格");

// ---- 8. ownNodes 过滤 ----
const all = [mkNode(T), mkNode("KSampler"), { type: "MossStoryboardLayout" }, null, { id: 3 }];
check("8.1 只挑本包节点且忽略 null", ownNodes(all).length === 2);

// ---- 9. 常量 ----
check("9.1 LANG_KEY / LANG_EVENT 是本包私有命名",
    LANG_KEY === "mosssb_lang" && LANG_EVENT === "mosssb:lang-changed");

console.log("\n结果：通过 " + pass + " / 失败 " + fails.length);
if (fails.length) { for (const f of fails) console.log("  - " + f); process.exit(1); }
