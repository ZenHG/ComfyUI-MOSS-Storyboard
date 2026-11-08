// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
// 第三方出处与许可见 THIRD-PARTY-NOTICES.md
//
// LLM Prompt · 参数面板 —— 39 个参数的信息架构层
//
// 设计口径（美学 / 高频用户两条视角，2026-10-30）：
//   1. **默认只露常驻 5 项**（task/channel/dialect/model/text）。这个节点的日常动作是
//      「贴图/贴文本 → 跑」，其余 34 项是配置而不是操作。节点因此从 978 px 降到 306 px
//      （1500×1500 视口、80% 缩放实测；size 高 353，100% 缩放下盒高 383 px）。
//   0. **开关条钉在节点顶部**，点开任一组都在它**下方**展开 —— 控制件的坐标永远不变
//      （旧版在底部，每展开一组它就往下跑一截，2026-10-30 实测点名）。
//   2. **一次只看一组**。点「常用调优」只看调优，点「本地引擎」只看引擎 —— 不叠。
//      理由：参数顺序是存档契约，改不了；`mmproj`/`runtime` 夹在别的组中间，
//      多组同时展开时标题只能来回重画（上一版全展开里「本地引擎」出现了 3 次）。
//      单组视图让同组行在视觉上连成一片，标题只出现一次。
//   3. **「已改」小点**：折叠着的组里只要有一项与默认不同，chip 上点一个小蓝点
//      （悬停看条数）。高频用户最怕「我改过的东西被藏起来了」。
//   4. 视觉只用宿主 CSS 变量；置顶 order 与临时网格样式需 !important。
//      开关条透明底 + 上边一道发丝线，读起来是工具栏而不是第 40 个参数。
//   5. **说明行高度恒定**（`height:2.7em`）：悬停换 tooltip 时行数变化不再顶动下面的参数
//      （2026-10-30 实测：这是鼠标定位失败的直接原因）。「中/EN」按钮挂在说明行右端的
//      `langSlot`（核心 i18n 画），不另开 DOM 行；切语言时面板文案跟着一起换。
//
// 🔴 一条都不许碰存档。`widgets_values` 按**位置**存（CONTRIBUTING 硬规矩 1、docs/10 §三·4）。
//    这里只改 `widget.options.hidden`（宿主的 byExtension 抑制位）+ 给宿主的行元素加类名。
//    实测前端 1.57.6 / Vue 节点模式：隐藏的行不画、`serialize()` 照样按 `serialize !== false`
//    收值 ⇒ 隐藏不改变存档格数与位置；折叠状态只活在节点实例的 `__mossllm` 上，不序列化。
//    DOM 开关条那一格由 `moss_sb_widget_order.js::installSaveCleanup` 在存盘时剔掉（实测 39 格）。
//    **唯一会写进 JSON 的东西是节点 `size`**（`fitHeight` 按可见内容调高度）—— 那是画布尺寸，
//    不是参数值；改分组时会重算，用户手拖的高度在下次换组时被取代（可见、可撤销）。
//
// 为什么不用宿主自带的「显示高级参数」：它只有一个桶，切不出「本地引擎 / 外部 API」两组，
//    也承载不了「默认只露 5 项」这件事。（`advanced` 在各渲染层的实际落点随前端版本变，
//    这里不依赖它。）
//
// 为什么只挂**一个** DOM 行：组标题要插在两组参数之间，就得往 `node.widgets` 中间塞东西
//    —— 那是重排 widget = 改存档顺序。所以组标题改由 CSS 画在每组第一行上方
//    （`.mossllm-head::before`），行元素用宿主自己打的 `data-widget-name` 定位。
//
// 🔴 不静默兜底：拿不到隐藏能力、或挂不上这一行 ⇒ **什么都不藏**（只出声）——
//    「藏了却没有开关」等于把参数弄丢。接了线的参数永远不藏。

import { app } from "../../scripts/app.js";
import { installSaveCleanup } from "./moss_sb_widget_order.js";
import { LANG_EVENT, LANG_KEY, normLang } from "./moss_sb_i18n.js";

const TAG = "[MOSS SB] llm panel";
const TYPE = "MossPromptSmith";
const BAR_NAME = "mosssb_llm_panel";
const STYLE_ID = "mosssb-llm-panel-style";
const CH_LOCAL = "local";
const CH_API = "api";
/** 初版把中文写进了枚举值（2026-10-30 早场）。旧工作流里还是这两个 —— 两边都认。 */
const CH_LEGACY = { "本地 GGUF": CH_LOCAL, "外部 API": CH_API };
const chanOf = (v) => CH_LEGACY[v] || v;

/**
 * 信息架构：按**参数名**分组（名字是契约，位置不是）。表里没写的参数落进 `other` 且常显
 * —— 以后往参数表尾部追加新旋钮时，默认是「看得见」，不会因为这张表没同步就被藏掉。
 */
const GROUPS = [
    // 决策：每次都要动的三项 + 模型（本地/API 都可能要，PE 改写器也读它）。
    { key: "core", always: true, items: ["task", "channel", "model", "text"] },
    // 输出格式：只与任务有关，不与通道绑定。
    { key: "format", sec: "secFormat", items: ["dialect", "target", "profile"] },
    // 采样：控制模型怎么写。
    { key: "sampling", sec: "secSampling",
      items: ["length", "temperature", "max_tokens", "seed", "language", "thinking"] },
    // 内容约束：写什么、保留什么。
    { key: "content", sec: "secContent",
      items: ["panels", "style_lock", "must_keep", "wash", "two_pass"] },
    // 本地连接：权重之外的本地入口。
    { key: "local", channel: CH_LOCAL, sec: "secLocal",
      items: ["server_exe", "mmproj", "runtime"] },
    // 外部连接。
    { key: "api", channel: CH_API, sec: "secApi",
      items: ["api_base", "api_model", "api_key"] },
    // 引擎：llama-server 启动与显存，只在本地通道有用。
    { key: "engine", channel: CH_LOCAL, sec: "secEngine",
      items: ["ctx_size", "gpu_layers", "cache_type_k", "cache_type_v", "flash_attn",
              "image_max_tokens", "ubatch", "parallel", "fit_on", "fit_target_mib",
              "mtp", "mtp_draft_n", "port", "release_after_run"] },
];
const OTHER = { key: "other", always: true };
const BY_NAME = new Map();
for (const g of GROUPS) for (const n of g.items) BY_NAME.set(n, g);

/** seed 每次跑都会变、「生成后控制」是它的伴侣 —— 拿它们算「已改」只会永远亮着。 */
const DIRTY_SKIP = new Set(["seed", "control_after_generate"]);

// 全走宿主 CSS 变量；置顶 order 与临时网格样式例外。DOM 顺序 ≠ 视觉顺序，见 fitHeight。
// `flex:0 0 auto` 必须有：宿主给 DOM 组件的单元格套了 `*:flex-1`（flex-grow:1），
// 不覆盖它，开关条就会被撑到整个剩余高度，线条被 align-content 摊开成一列散字（实测 434px）。
const CSS = `
/* server_exe 在参数表尾部：宿主每行包了一层 wrapper，order 要打在 wrapper 上。
   把它的 wrapper 排到最前（但仍低于开关条），避免用户漏看；只改视觉顺序，不动存档。 */
[data-widgets-grid-node-id] > div:has([data-widget-name="mosssb_llm_panel"]){order:-2 !important}
.mossllm-bar{display:flex;flex-wrap:wrap;align-content:flex-start;flex:0 0 auto;
 align-items:center;gap:2px 4px;
 box-sizing:border-box;width:100%;padding:6px 2px 1px;
 border-top:1px solid var(--border-color,#4e4e4e);
 color:var(--descrip-text,#999);font:11px/1.5 system-ui,sans-serif;
 min-width:0;max-width:100%;overflow:hidden}
.mossllm-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border-radius:7px;
 cursor:pointer;border:1px solid transparent;background:transparent;
 color:var(--input-text,#ddd);font:inherit;white-space:nowrap}
.mossllm-chip[hidden]{display:none}
.mossllm-chip:hover{background:var(--comfy-input-bg,#222)}
.mossllm-chip:focus-visible{outline:1px solid var(--border-color,#4e4e4e);outline-offset:1px}
.mossllm-chip[data-state="open"]{border-color:var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222)}
.mossllm-chip[data-state="closed"]{color:var(--descrip-text,#999)}
.mossllm-chip[data-state="outside"]{color:var(--descrip-text,#999);opacity:.65}
/* 嵌套第 2 层：本次任务会用到的组，左边一道蓝线标出来。不展开、不改行数。 */
.mossllm-chip[data-need="1"]{border-left:2px solid #185FA5}
/* 「端点已填，但通道=本地」：这不是提示语，是当前状态 —— 用琥珀色块钉在开关条上，
   而不是缩成摘要里的一小段灰字（实测那样等于没看见）。点它即可切到外部 API。 */
.mossllm-warn{display:inline-flex;align-items:center;gap:5px;max-width:100%;padding:2px 8px;
 border-radius:7px;border:1px solid #b8860b;background:rgba(217,164,65,.16);color:#e8b64c;
 font:600 11px/1.5 system-ui,sans-serif;cursor:pointer;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis}
.mossllm-warn[hidden]{display:none}
.mossllm-warn:hover{background:rgba(217,164,65,.28)}
.mossllm-caret{width:9px;opacity:.55;font-size:11px;line-height:1}
/* 三个 chip 并排，但「常用调优」是参数分组、「本地引擎 / 外部 API」是通道 —— 不是同一维度。
   给通道两个加前缀小字，别让人以为调优属于本地引擎。 */
.mossllm-kind{font-size:9.5px;line-height:1;opacity:.45;margin-right:3px;letter-spacing:.04em}
.mossllm-kind:empty{display:none}
/* 一次性预设：只在「常用调优」展开时出现。按钮不是 widget ⇒ 不进 widgets_values。 */
.mossllm-presets{display:inline-flex;align-items:center;gap:3px;margin-left:2px}
.mossllm-presets[hidden]{display:none}
  .mossllm-rt{display:inline-flex;align-items:center;gap:3px;margin-left:2px}
  .mossllm-rt[hidden]{display:none}
.mossllm-preset{padding:0 7px;border-radius:6px;border:1px dashed var(--border-color,#4e4e4e);
 background:transparent;color:var(--input-text,#ddd);font:11px/1.7 system-ui,sans-serif;cursor:pointer}
.mossllm-preset:hover{background:var(--comfy-input-bg,#222)}
.mossllm-preset:focus-visible{outline:1px solid var(--border-color,#4e4e4e);outline-offset:1px}
.mossllm-count{opacity:.55;font-variant-numeric:tabular-nums}
.mossllm-dot{display:none;width:6px;height:6px;margin-left:2px;border-radius:50%;
 background:#5b9cf6;flex:0 0 auto}
.mossllm-chip[data-dirty="1"] .mossllm-dot{display:inline-block}
.mossllm-btn{flex:0 0 auto;margin-left:auto;padding:2px 6px;border:0;border-radius:6px;background:transparent;
 color:var(--descrip-text,#999);cursor:pointer;font:inherit;
 text-decoration:underline;text-underline-offset:2px}
.mossllm-btn:hover{color:var(--input-text,#ddd);background:var(--comfy-input-bg,#222)}
/* 「拉取模型」贴在 api_model 行里 —— 控件必须挨着它填的那一格，放顶部条上等于没放。
   行本身是 static 网格，设 relative 只为定位，不改宿主布局。 */
[data-widgets-grid-node-id] [data-widget-name="api_model"]{position:relative}
.mossllm-pick{position:absolute;right:4px;top:50%;transform:translateY(-50%);
 display:flex;align-items:center;gap:4px;max-width:60%;z-index:3}
.mossllm-pick button{flex:0 0 auto;width:20px;height:20px;padding:0;border-radius:5px;
 border:1px solid var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222);
 color:var(--input-text,#ddd);cursor:pointer;font:12px/1 system-ui,sans-serif}
.mossllm-pick button:hover{background:var(--comfy-menu-bg,#333)}
.mossllm-pick button[data-bad="1"]{border-color:#c0504d;color:#e07b78}
.mossllm-pick button[data-busy="1"]{color:#d9a441;border-color:#8a6d2f;
 animation:mossllm-spin 1s linear infinite}
/* 「常用端点」贴在 api_base 行右端 —— 与 ↻ 同一套路：控件必须挨着它填的那一格。
   行本身是 static 网格，设 relative 只为定位，不改宿主布局。 */
[data-widgets-grid-node-id] [data-widget-name="api_base"]{position:relative}
.mossllm-ep{position:absolute;right:4px;top:50%;transform:translateY(-50%);z-index:3;max-width:52%}
.mossllm-ep select{max-width:100%;height:20px;padding:0 3px;border-radius:5px;
 border:1px solid var(--border-color,#4e4e4e);background:var(--comfy-input-bg,#222);
 color:var(--input-text,#ddd);cursor:pointer;font:11.5px/1 system-ui,sans-serif}
.mossllm-ep select:hover{background:var(--comfy-menu-bg,#333)}
/* 输入框右侧留出下拉的宽度，否则长 URL 的尾巴被盖住（只作用于 api_base 这一格）。 */
[data-widgets-grid-node-id] [data-widget-name="api_base"] input{padding-right:116px}
/* 设计系统（impeccable craft-floor）：深色宿主原生变量 · 字号 12/15/19 · 4 单位节奏。
   无嵌套卡片、无彩色粗左边框、无亮色贴纸、无 9.5px 小字。 */
.mossllm-status{flex:0 0 100%;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
 padding:6px 10px 8px;border-bottom:1px solid var(--border-color,#4e4e4e);margin-bottom:8px}
.mossllm-status .mossllm-hint{flex:1 1 auto;min-width:0;font-size:19px;font-weight:500;
 opacity:1;height:1.5em;line-height:1.5;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis}
.mossllm-status .mossllm-warn{font-size:12px;font-weight:400;background:transparent;
 border:0;padding:0;color:#e8b64c;white-space:normal;text-overflow:clip}
.mossllm-status[data-level="danger"] .mossllm-warn{color:#f09595}
.mossllm-status[data-level="ok"] .mossllm-warn{color:var(--descrip-text,#999)}
.mossllm-status .mossllm-warn:hover{background:transparent;text-decoration:underline}
.mossllm-chipsrow{flex:0 0 100%;min-width:0;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;
 padding:0 2px 4px}
.mossllm-chip{display:inline-flex;align-items:center;gap:5px;padding:4px 9px;border-radius:6px;
 border:1px solid transparent;background:transparent;color:var(--descrip-text,#999);
 font:12px/1.4 system-ui,sans-serif;cursor:pointer;white-space:nowrap}
.mossllm-chip[hidden]{display:none}
.mossllm-chip:hover{background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd)}
.mossllm-chip:focus-visible{outline:1px solid var(--border-color,#4e4e4e);outline-offset:1px}
.mossllm-chip[data-state="open"]{color:var(--input-text,#ddd);font-weight:500;
 border-bottom:2px solid var(--descrip-text,#999)}
.mossllm-chip[data-state="outside"]{opacity:.55}
.mossllm-chip[data-need="1"] .mossllm-dot{display:inline-block}
.mossllm-caret{font-size:11px;opacity:.6}
.mossllm-kind{font-size:11px;opacity:.7}
.mossllm-kind:empty{display:none}
.mossllm-count{display:none}
.mossllm-dot{display:none;width:6px;height:6px;border-radius:50%;background:#5b9cf6}
.mossllm-btn{flex:0 0 auto;margin-left:auto;padding:4px 9px;border:1px solid var(--border-color,#4e4e4e);
 border-radius:6px;background:transparent;color:var(--input-text,#ddd);
 font:12px/1.4 system-ui,sans-serif;cursor:pointer}
.mossllm-btn:hover{background:var(--comfy-input-bg,#222)}
.mossllm-presets{flex:0 0 100%;display:flex;gap:6px;padding:2px 0 8px}
.mossllm-presets[hidden]{display:none}
.mossllm-pzbody{flex:0 0 100%;display:flex;flex-direction:column;gap:8px;padding:8px 2px 4px}
.mossllm-pzbody[hidden]{display:none}
.mossllm-pzgroups,.mossllm-pzsub{display:flex;flex-wrap:wrap;gap:6px}
.mossllm-pztoggle{padding:4px 9px;border:1px solid var(--border-color,#4e4e4e);
 border-radius:6px;background:transparent;color:var(--input-text,#ddd);
 font:12px/1.4 system-ui,sans-serif;cursor:pointer}
.mossllm-pzitem{padding:4px 9px;border-radius:6px;border:1px solid var(--border-color,#4e4e4e);
 background:transparent;color:var(--input-text,#ddd);font:12px/1.4 system-ui,sans-serif;cursor:pointer}
.mossllm-pzitem[data-on="1"]{background:var(--comfy-input-bg,#222);font-weight:500}
/* 分区标题：打在每组首个 widget 行上，不插 DOM；宿主重建行后由 apply 补属性。 */
[data-widgets-grid-node-id] [data-widget-name][data-moss-sec]::before{content:attr(data-moss-sec);
 display:block;font:500 15px/1.4 system-ui,sans-serif;color:var(--input-text,#ddd);
 padding:16px 2px 6px;border-bottom:0.5px solid var(--border-color,#4e4e4e);margin-bottom:8px}
@keyframes mossllm-spin{to{transform:rotate(360deg)}}
/* ==== 推倒重来：三层界面 ==== */
.mossllm-bar{display:flex;flex-direction:column;flex-wrap:nowrap;flex:0 0 auto;
 align-items:stretch;gap:0;box-sizing:border-box;width:100%;min-width:0;max-width:100%;
 overflow:hidden;padding:0;border-top:1px solid var(--border-color,#4e4e4e);
 color:var(--descrip-text,#999);font:12px/1.5 system-ui,sans-serif}
.mossllm-toprow{flex:0 0 auto;position:relative;display:flex;flex-direction:row;
 width:100%;min-width:0;align-items:stretch}
.mossllm-status{flex:1 1 auto;min-width:0;display:flex;align-items:center;gap:10px;
 height:38px;padding:0 92px 0 10px;overflow:hidden;border-bottom:1px solid var(--border-color,#4e4e4e)}
.mossllm-status .mossllm-hint{flex:1 1 auto;min-width:0;height:auto;line-height:1.4;
 font-size:15px;font-weight:500;color:var(--input-text,#ddd);opacity:1;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mossllm-status .mossllm-warn{flex:0 1 auto;min-width:0;font-size:12px;font-weight:400;
 background:transparent;border:0;padding:0;color:#e8b64c;white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.mossllm-status .mossllm-warn[hidden]{display:none}
.mossllm-status .mossllm-warn[data-level="danger"]{color:#f09595}
.mossllm-status .mossllm-warn[data-level="ok"]{color:var(--descrip-text,#999)}
.mossllm-settings{position:absolute;right:0;top:0;height:38px;padding:0 14px;border:0;
 border-left:1px solid var(--border-color,#4e4e4e);border-bottom:1px solid var(--border-color,#4e4e4e);
 background:transparent;color:var(--input-text,#ddd);font:12px/1.5 system-ui,sans-serif;cursor:pointer}
.mossllm-settings:hover{background:var(--comfy-input-bg,#222)}
.mossllm-settings[data-on="1"]{color:#5b9cf6}
[data-widget-name].mossllm-flash{outline:2px solid #5b9cf6;outline-offset:2px;border-radius:4px}
.mossllm-settings-note{width:100%;min-width:0;max-width:100%;box-sizing:border-box;
 overflow:hidden;padding:7px 10px;font-size:12px;
 color:var(--descrip-text,#999);border-bottom:1px solid var(--border-color,#4e4e4e)}
.mossllm-settings-note[hidden]{display:none}
.mossllm-presets-box{width:100%;min-width:0;max-width:100%;box-sizing:border-box;
 overflow:hidden;display:flex;flex-direction:column;gap:8px;
 padding:8px 10px;border-bottom:1px solid var(--border-color,#4e4e4e)}
.mossllm-presets-box[hidden]{display:none}
.mossllm-pzbody{display:flex;flex-direction:column;gap:8px}
.mossllm-pzbody[hidden]{display:none}
.mossllm-pzgroups,.mossllm-pzsub,.mossllm-presets{display:flex;flex-wrap:wrap;gap:6px}
.mossllm-pztoggle{align-self:flex-start;padding:4px 9px;border-radius:6px;
 border:1px solid var(--border-color,#4e4e4e);background:transparent;
 color:var(--input-text,#ddd);font:12px/1.4 system-ui,sans-serif;cursor:pointer}
.mossllm-pzitem,.mossllm-preset{padding:4px 9px;border-radius:6px;
 border:1px solid var(--border-color,#4e4e4e);background:transparent;
 color:var(--input-text,#ddd);font:12px/1.4 system-ui,sans-serif;cursor:pointer}
.mossllm-pzitem[data-on="1"]{background:var(--comfy-input-bg,#222);font-weight:500}
.mossllm-preset:hover,.mossllm-pzitem:hover{background:var(--comfy-input-bg,#222)}

.mossllm-status .mossllm-langslot{flex:0 0 auto;min-width:0;max-width:64px;overflow:hidden}
.mossllm-status .mossllm-warn{max-width:45%}

.mossllm-hintrow{flex:0 0 100%;display:flex;align-items:flex-start;gap:6px;min-width:0}
.mossllm-hint{flex:1 1 auto;min-width:0;overflow-wrap:anywhere;
 /* 🔴 高度必须恒定：悬停换 tooltip 时行数一变，下面每个参数都会跟着位移（实测：鼠标定位失败）。 */
 line-height:1.35;height:2.7em;overflow:hidden;opacity:.8}
.mossllm-langslot{flex:0 0 auto;display:flex;align-items:center;min-height:1.5em}
.mossllm-inert{opacity:.5}
.mossllm-head{margin-top:18px;position:relative}
.mossllm-head::before{content:attr(data-mossllm-title);position:absolute;left:2px;top:-16px;
 font:600 10.5px/16px system-ui,sans-serif;letter-spacing:.08em;
 color:var(--input-text,#ddd);opacity:.75}
`;

const I18N = {
    zh: { core: "常驻", tune: "常用调优", local: "本地引擎 · llama.cpp", api: "外部 API",
        other: "其它", offchannel: "本次不用", expand: "全部展开", back: "回到分组",
        count: "%d 个参数 · %d 个可见", dirty: "%d 项与默认不同",
        openTip: "只展开这一组（其余收起来）", closeTip: "收起，回到常驻",
        expandTip: "一次展开全部 %d 项，包括当前通道用不到的旋钮",
        hideTip: "隐藏只是不画出来：参数还在工作流里，值不变",
        backTip: "恢复分组：默认只露常驻，一次看一组",
        inert: "本次不生效（拼装任务不开模型）",
        inertHint: "拼装模式：%d 项本次不生效",
        selfCheck: "零模型自检：任务选「拼装」",
        fetchTip: "用当前 api_base 与 Key 拉模型清单，灌进这个下拉",
        fetched: "已拉到 %d 个模型，可直接下拉换",
        fetching: "拉模型清单中… %ds",
        fetchFail: "拉模型失败", needBase: "先填 api_base 再拉模型",
        // 常用端点下拉（api_base 行内的 select，不是 widget）
        ep_custom: "自定义（不动）",
        ep_deepseek: "DeepSeek", ep_opencode: "OpenCode", ep_odesign: "OpenDesign CoRouter",
        ep_ollama: "Ollama 本地", ep_lmstudio: "LM Studio 本地",
        epDone: "已填 %s 端点，点 ↻ 拉模型",
        epTip: "选常用端点会把地址写进 api_base 并切到「外部 API」；「自定义」不动当前值",
        switched: "通道已切到外部 API",
        apiIdle: "⚠ 端点已填，但通道=本地 —— 本次走本地模型",
        apiIdleFix: "点此改用 API",
        apiIdleTip: "api_base 已填，但「通道 / channel」还是本地 GGUF：本次运行会去找本机 llama-server，"
            + "端点不生效。点这一个提示，就把通道切到「外部 API」（端点与模型名都已填好）。"
            + "想保留本地就无视它 —— 通道=本地 是显式选择，节点不会替你改。",
        chanAuto: "已按已填的端点切到「外部 API」",
        // 模式摘要：首屏唯一一处解释「这个节点现在要干什么」的地方（不新增控件、不动存档）。
        chLocalShort: "本地 GGUF", chApiShort: "外部 API",
        mode_caption: "读图说话", mode_expand: "扩写提示词",
        mode_edit: "按指令改图", mode_assemble: "只拼装提示词",
        modeTip_caption: "读图说话：把输入图写成提示词。本地要 GGUF 模型 + 视觉塔（mmproj）；"
            + "切到外部 API 则用服务商的视觉模型",
        modeTip_expand: "扩写：纯文字 → 更详细的提示词。不需要图，也不需要视觉塔",
        modeTip_edit: "按指令改图：接输入图 + 指令，输出改写后的提示词。本地需要视觉塔",
        modeTip_assemble: "只拼装提示词：不开模型、不占显存，用来检查拼装结果",
        // 执行前就能看出来的缺项（Key 空不算缺 —— Ollama / LM Studio 本来就不要 Key）
        lackBase: "⚠ 还没填 api_base", lackName: "⚠ 还没选模型名",
        lackModel: "⚠ 本机没有 GGUF 模型",
        lackTipBase: "端点还没填，这次运行必然失败。点一下展开「外部 API」组。",
        lackTipName: "模型名还没选。点一下展开「外部 API」组，或先用 ↻ 拉一份清单。",
        lackTipModel: "本地通道要一个 GGUF 模型才能跑。点一下展开「本地引擎」组；"
            + "或把通道切到「外部 API」。",
        kindChannel: "连接", kindNeed: "本次", needTip: "这个任务会用到这组",
        decWhat: "做什么", decChan: "用哪个", presetTitle: "预设",
        presetConnect: "连接", presetTune: "采样", presetOpenTip: "先选一类，再选具体档",
        advTitle: "高级 · 全部参数",
        secFormat: "输出格式", secSampling: "采样", secContent: "内容约束",
        secLocal: "本地引擎", secApi: "外部 API", secEngine: "引擎 / 显存",
        format: "格式", sampling: "采样", content: "内容",
        engine: "引擎",
        segCaption: "反推", segExpand: "扩写", segEdit: "改图", segAssemble: "拼装",
        segLocal: "本地", segApi: "API",
        preset_draft: "草稿（快）", preset_fine: "精修（细）", preset_reset: "恢复默认",
        presetTip_draft: "一次写入：长度=简短 · 温度 0.9 · 上限 512 tokens。先看方向对不对时用，最省时间",
        presetTip_fine: "一次写入：长度=详细 · 温度 0.2 · 上限 2048 tokens。要出成品时用",
        presetTip_reset: "把「常用调优」这一组恢复成默认值（不动其它组、不动通道）",
        presetDone: "已套用「%s」，改了 %d 项",
    rtInstall: "安装/更新", rtTest: "测试",
    rtInstallTip: "从 GitHub 官方 release 下载并安装最新 llama.cpp（下载与解压都会显示进度）",
    rtTestTip: "只跑 --version：不加载模型、不占显存，用来确认这个 exe 能不能用",
    rtInstalling: "正在安装/更新 llama.cpp…", rtInstalled: "已就绪：%s",
    rtProbeOk: "可用：%s", rtProbeFail: "不可用：%s",
        presetResetDone: "「常用调优」已恢复默认值",
        },
    en: { core: "Basic", tune: "Common", local: "Local engine · llama.cpp", api: "External API",
        other: "Other", offchannel: "not used now", expand: "Show all", back: "Grouped",
        count: "%d params · %d visible", dirty: "%d differ from default",
        openTip: "Show only this group", closeTip: "Fold back to Basic",
        expandTip: "Show all %d params, including the other channel's",
        hideTip: "Hiding only affects the canvas — values stay in the workflow",
        backTip: "Back to grouped view (Basic only, one group at a time)",
        inert: "No effect in this run (assemble does not load a model)",
        inertHint: "Assemble: %d controls have no effect",
        selfCheck: "No-weight check: set task=assemble",
        fetchTip: "Fetch models for this api_base + key into this dropdown",
        fetched: "%d models loaded — pick from the dropdown",
        fetching: "Fetching model list… %ds",
        fetchFail: "Model list failed", needBase: "Fill api_base first",
        // Preset endpoint select inside the api_base row (not a widget)
        ep_custom: "Custom (leave as is)",
        ep_deepseek: "DeepSeek", ep_opencode: "OpenCode", ep_odesign: "OpenDesign CoRouter",
        ep_ollama: "Ollama (local)", ep_lmstudio: "LM Studio (local)",
        epDone: "Filled %s endpoint; press the arrow to fetch models",
        epTip: "Picking a known endpoint writes api_base and switches to External API;"
            + " Custom leaves the current value alone",
        switched: "channel switched to external API",
        chLocalShort: "Local GGUF", chApiShort: "External API",
        mode_caption: "Caption image", mode_expand: "Expand prompt",
        mode_edit: "Edit by instruction", mode_assemble: "Assemble only",
        modeTip_caption: "Caption: turn the input image into a prompt. Local needs a GGUF model "
            + "plus a vision tower (mmproj); the API channel uses the provider's vision model",
        modeTip_expand: "Expand: plain text to a longer prompt. No image, no vision tower",
        modeTip_edit: "Edit: input image plus instruction, output the rewritten prompt. "
            + "Local needs a vision tower",
        modeTip_assemble: "Assemble only: no model, no VRAM, just check the assembled prompt",
        lackBase: "Missing api_base", lackName: "No model name yet",
        lackModel: "No local GGUF found",
        lackTipBase: "api_base is empty, so this run cannot work. Click to open External API.",
        lackTipName: "Pick a model name. Click to open External API, or press the arrow to fetch a list.",
        lackTipModel: "The local channel needs a GGUF model. Click to open Local engine, "
            + "or switch the channel to External API.",
        kindChannel: "ch", kindNeed: "now", needTip: "used by this task",
        decWhat: "What", decChan: "Backend", presetTitle: "Presets",
        presetConnect: "Connect", presetTune: "Sampling", presetOpenTip: "Pick a group, then a preset",
        advTitle: "Advanced · all parameters",
        secFormat: "Output format", secSampling: "Sampling", secContent: "Content",
        secLocal: "Local engine", secApi: "External API", secEngine: "Engine / VRAM",
        format: "Format", sampling: "Sampling", content: "Content",
        engine: "Engine",
        segCaption: "Caption", segExpand: "Expand", segEdit: "Edit", segAssemble: "Assemble",
        segLocal: "Local", segApi: "API",
        preset_draft: "Draft (fast)", preset_fine: "Fine (slow)", preset_reset: "Reset",
        presetTip_draft: "Writes: length=brief, temp 0.9, max 512 tokens. Cheapest way to check direction",
        presetTip_fine: "Writes: length=detailed, temp 0.2, max 2048 tokens. For final output",
        presetTip_reset: "Restore this group to defaults (other groups and the channel are untouched)",
        presetDone: "Applied %s, %d values changed",
    rtInstall: "Install/update", rtTest: "Test",
    rtInstallTip: "Download and install the latest llama.cpp release (progress is shown)",
    rtTestTip: "Runs --version only: no model load, no VRAM",
    rtInstalling: "Installing/updating llama.cpp…", rtInstalled: "Ready: %s",
    rtProbeOk: "Usable: %s", rtProbeFail: "Not usable: %s",
        presetResetDone: "Common settings restored to defaults",
        apiIdle: "Endpoint filled but channel=local — this run uses local",
        apiIdleFix: "Click to use the API",
        apiIdleTip: "api_base is filled but the channel is still Local GGUF: this run will look for a local"
            + " llama-server and the endpoint will be ignored. Click this badge to switch the channel to"
            + " External API. Keep local on purpose? Ignore it — the channel is respected, never overridden.",
        chanAuto: "Channel switched to External API (endpoint filled)",
        },
};
let T = null;
/** 宿主界面语言（没点过「中/EN」时跟随它）。顺序与 `moss_sb_i18n_ui.js` 完全一致。 */
function hostLang() {
    let s = "";
    // 先 `<html lang>` 再 `Comfy.Locale`：与核心 i18n 的 readHostLang 同步，
    // 否则同一次界面里会出现「节点标题英文、面板芯片中文」的撕裂（实测）。
    try { s = String(document.documentElement.getAttribute("lang") || ""); } catch (e) { /* 无 DOM */ }
    if (!s) { try { s = String(app?.ui?.settings?.getSettingValue?.("Comfy.Locale") || ""); } catch (e) { /* 老前端 */ } }
    if (!s) s = String(navigator?.language || "");
    return normLang(s);
}
function t() {
    if (T) return T;
    let raw = "";
    try { raw = globalThis.localStorage?.getItem(LANG_KEY) || ""; } catch (e) { /* 隐私模式 */ }
    T = I18N[raw ? normLang(raw) : hostLang()];
    return T;
}

/** 占位替换按**出现顺序**吃参数：模板里 %s 与 %d 混用（「已套用「%s」，改了 %d 项」）也照填。 */
const fmt = (s, ...a) => String(s).replace(/%[sd]/g, () => String(a.shift()));

const widgetOf = (node, name) => (node.widgets || []).find((w) => w && w.name === name);
const hideable = (w) => !!w && typeof w.syncVisibilityFromOptions === "function";
const realWidgets = (node) => (node.widgets || []).filter((w) => w && w.name !== BAR_NAME);
const groupOf = (w) => BY_NAME.get(w.name) || OTHER;
/** 旧稿把中文写进 task 枚举；归一口径与后端 _LEGACY_VALUES 一致（`compile` 是改名前的英文值）。 */
const TASK_LEGACY = { "反推": "caption", "扩写": "expand", "编辑指令": "edit",
    "编译": "assemble", "compile": "assemble" };
const taskOf = (node) => {
    const w = widgetOf(node, "task");
    const v = w ? String(w.value) : "";
    return TASK_LEGACY[v] || v;
};
/** 可静态判定的模式知识；tasks/channels 由 check_frontend.py 的 F11 与后端常量对账。
 *  PE 命中取决于模型目录里的文件，属运行时探测，不进这张表（docs/21 A2'）。 */
const MODE_SPEC = {
    tasks: ["caption", "expand", "edit", "assemble"],
    channels: ["local", "api"],
    inertOnCompile: ["channel", "dialect", "model"],
};
/**
 * 常用端点：值与顺序都取自 `docs/20` 的「已实测的 api_base」表（2026-11-05 实测）。
 * 只放测过能直接跑通的那几个 —— 没实测过的地址不进来，免得「选中即失败」。
 * 用户自己的网关不在表里：下拉末项「自定义」不改值，手填照样能跑（api_base 仍是 STRING）。
 */
const ENDPOINTS = [
    { id: "deepseek", url: "https://api.deepseek.com/v1" },
    { id: "opencode", url: "https://opencode.ai/inference/go/openai/v1" },
    { id: "odesign", url: "https://amr-link.open-design.ai/v1" },
    { id: "ollama", url: "http://127.0.0.1:11434/v1" },
    { id: "lmstudio", url: "http://127.0.0.1:1234/v1" },
];
const ENDPOINTS_BY_NAME = Object.assign(
    { "本机（自动）": null },
    ...ENDPOINTS.map((e) => ({ [({ deepseek: "DeepSeek", opencode: "OpenCode",
        odesign: "OpenRouter", ollama: "Ollama 本机", lmstudio: "LM Studio 本机" })[e.id] || e.id]: e }))
);

// ---------------------------------------------------------------------------
// 遥控器：宿主原生 combo，一个控件写多个原始 widget。serialize=false ⇒ 不进存档。
// 目的：用户要理解的概念从 39 个降到 8 个，且全部是宿主控件（存档 / 多实例 / 主题 /
// /prompt API 全部自动成立）。唯一自定义 DOM 只剩一行只读状态。
// ---------------------------------------------------------------------------
const STYLE_CLASS = {
    "连贯描述": ["英文详述", "英文通用", "中文"],
    "风格": ["写实摄影", "电影剧照", "胶片复古", "暗调氛围", "商业广告",
             "二次元插画", "水彩", "赛博朋克", "黑白纪实"],
    "自定义": ["自定义"],
    "标签串": ["英文标签"],
    "分段结构": ["宫格稿", "时间线六段式"],
};
const STYLE_WRITE = {
    "英文详述": { dialect: "moss_image" },
    "英文通用": { dialect: "generic_natural" },
    "电影感": { dialect: "cinematic" },
    "中文": { dialect: "cn_natural" },
    "英文标签": { dialect: "generic_tags" },
    "宫格稿": { dialect: "moss_storyboard", panels: 4 },
    "六段式": { target: "minimax-h3", profile: "six-section" },
};
const STYLE_OF = {
    moss_image: "英文详述", generic_natural: "英文通用", cinematic: "电影感",
    cn_natural: "中文", generic_tags: "英文标签", moss_storyboard: "宫格稿",
};
const LEN_WRITE = { "短": { length: "brief", max_tokens: 512 },
    "中": { length: "standard", max_tokens: 1024 },
    "长": { length: "detailed", max_tokens: 2048 } };
const TEMP_WRITE = { "低": 0.2, "中": 0.7, "高": 0.9 };
const SPEED_WRITE = {
    "省显存": { ctx_size: 4096, ubatch: 256, fit_target_mib: 768, image_max_tokens: 512 },
    "均衡": { ctx_size: 8192, ubatch: 512, fit_target_mib: 1024, image_max_tokens: 1024 },
    "尽量快": { ctx_size: 16384, ubatch: 1024, fit_on: "off", image_max_tokens: 1536 },
};
function rowOf(node, name) {
    try {
        return document.querySelector(
            '[data-widgets-grid-node-id="' + node.id + '"] [data-widget-name="' + name + '"]');
    } catch (e) { return null; }
}

function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
}

// ---------------------------------------------------------------------------
// 参数说明 + 默认值：读宿主缓存里的**同一份** nodeDef，前端不复制第二份中文
// ---------------------------------------------------------------------------
let TIPS = null;
let DEFAULTS = null;
let LOADING = null;
function loadDefs() {
    if (LOADING) return LOADING;
    LOADING = (async () => {
        const tips = {};
        const def = {};
        try {
            const defs = await app.getNodeDefs();
            const inp = (defs && defs[TYPE] && defs[TYPE].input) || {};
            for (const sec of ["required", "optional"]) {
                for (const [name, spec] of Object.entries(inp[sec] || {})) {
                    const arr = Array.isArray(spec) ? spec : [spec];
                    const opts = arr[1] || {};
                    if (opts.tooltip) tips[name] = String(opts.tooltip);
                    if ("default" in opts) def[name] = opts.default;
                    else if (Array.isArray(arr[0])) def[name] = arr[0][0];
                }
            }
        } catch (e) {
            console.warn("%s: 取不到参数说明/默认值（说明行会空着，且不画「已改」点）", TAG, e);
        }
        TIPS = tips;
        DEFAULTS = def;
        return { tips, def };
    })();
    return LOADING;
}
const tipOf = (name) => (TIPS && TIPS[name]) || "";

let hintOn = false;
function installHintListener() {
    if (hintOn) return;
    hintOn = true;
    document.addEventListener("pointerover", async (e) => {
        const row = e.target && e.target.closest ? e.target.closest("[data-widget-name]") : null;
        const grid = row && row.closest("[data-widgets-grid-node-id]");
        const hovered = grid ? (app?.graph?.nodes || [])
            .find((n) => String(n.id) === grid.getAttribute("data-widgets-grid-node-id")) : null;
        const owner = hovered && hovered.type === TYPE ? hovered : null;
        const st = owner && owner.__mossllm;
        if (!st || !st.hint || !row) return;
        await loadDefs();
        const name = row.getAttribute("data-widget-name");
        const w = owner && widgetOf(owner, name);
        const text = (w && w.tooltip) || tipOf(name) || st.summary || "";
        // 只给浏览器原生 tooltip。状态行文字与所有元素位置永不因 hover 改变。
        st.hint.title = text;
    }, true);
}

// ---------------------------------------------------------------------------
// 状态（只在内存，绝不进存档）
// ---------------------------------------------------------------------------
/** 分段按钮写回 widget：值与 callback 都走宿主那一套，不另存状态。 */
function setWidgetValue(node, name, value) {
    const w = widgetOf(node, name);
    if (!w || String(w.value) === String(value)) return false;
    w.value = value;
    try { w.callback?.(value); } catch (e) { /* 老前端没有 callback */ }
    return true;
}

function stateOf(node) {
    let st = node.__mossllm;
    if (!st) {
        st = node.__mossllm = {
            focus: null,           // null = 只看常驻；否则是某组 key
            force: false,          // 会话内记住「高级」展开：st 挂在 node.__mossllm 上，
                                   // 实例在则状态在、重开工作流则新建 st 回到收起。不另存第二份状态。
            warnedChannel: false,
            presetOpen: true, presetGroup: "connect",
        };
    }
    return st;
}

/**
 * 嵌套第 2 层：本次任务会用到哪几组。只用于 **chip 标记**，不自动展开、不改行数。
 * 判据全部来自「任务 / 通道」这两个与模型无关的维度；不含 dialect / target / profile 知识。
 */
function needsOf(task, ch) {
    const s = new Set();
    if (ch === CH_API) s.add("api");
    else if (ch === CH_LOCAL) { s.add("local"); s.add("engine"); }
    if (task === "expand" || task === "assemble") s.add("format");
    if (task === "expand") s.add("sampling");
    return s;
}

/** 16 项一屏放得下：全部显示，不做任何隐藏。 */
function plan(node, st) {
    const ch = String(apiVal(node, "api_base") || "").trim() ? CH_API : CH_LOCAL;
    const shown = new Set();
    for (const w of realWidgets(node)) shown.add(w.name);
    const task = String(apiVal(node, "task") || "反推");
    return { shown, off: new Set(), inert: new Set(),
        assemble: task === "拼装", task, ch, needs: new Set() };
}

function syncWidgets(node, shown) {
    return false;                       // 显隐由宿主 advanced 负责
    let changed = false;
    for (const w of realWidgets(node)) {
        if (w.name === BAR_NAME || REMOTE_NAMES.has(w.name)) continue;
        if (!hideable(w)) continue;
        const wantHidden = !shown.has(w.name);
        if (!!w.options.hidden !== wantHidden) {
            w.options.hidden = wantHidden;
            w.syncVisibilityFromOptions();
            changed = true;
        }
    }
    return changed;
}

/**
 * 按可见内容调节点高度（**只动 `size`，不动 `widgets_values`**）。
 *
 * 为什么必须调：宿主不会随隐藏行自动收缩；DOM 组件那一格又被 `*:flex-1` 拉满 ⇒ 折叠后
 * 原尺寸会留一大片空白。这里用宿主自己渲染的行元素实测内容高度。
 * 量不到（画布模式没有 DOM 行）就**不猜** —— 宁可高，不可裁。
 * 只调高度、不调宽度；用户手拖的高度会在下一次「切换分组」时被内容高度取代（这是可预期的）。
 */
function fitHeight(node, shown) {
    const grid = document.querySelector('[data-widgets-grid-node-id="' + node.id + '"]');
    const box = grid && grid.closest(".lg-node");
    if (!grid || !box || typeof node.setSize !== "function") return false;
    // 宿主的网格会把多余高度**平摊进每一行**（自动行被 stretch）⇒ 直接量到的是"摊平后"
    // 的高度，拿它当内容高度会越调越高。临时把行压成 min-content 再量，量完立刻还原：
    // 同一个任务里改样式再读回，浏览器不会 paint，看不到跳动。
    grid.style.setProperty("grid-auto-rows", "min-content", "important");
    grid.style.setProperty("align-content", "start", "important");
    let rects;
    try {
        // 只量**这一轮要显示**的行：宿主删 DOM 行是异步的，刚藏起来的行这一刻还在页面上，
        // 按 DOM 高度数会把 39 行全算进去（实测因此把节点从 319 调到 1286）。
        rects = [...grid.querySelectorAll("[data-widget-name]")]
            .filter((e) => e.dataset.widgetName === BAR_NAME || shown.has(e.dataset.widgetName))
            .map((e) => e.getBoundingClientRect());
        // order 只改视觉顺序；DOM 首行不是视觉首行，顶部留白会被算成置顶行高度。
        rects.sort((a, b) => a.top - b.top);
    } finally {
        grid.style.removeProperty("grid-auto-rows");
        grid.style.removeProperty("align-content");
    }
    // 还没排完版（有 0 高）⇒ 这一轮不猜，等下一次 applySoon
    if (!rects.length || rects.some((r) => r.height <= 0)) return false;
    const gr = grid.getBoundingClientRect();
    const br = box.getBoundingClientRect();
    const zoom = (app?.canvas?.ds?.scale) || 1;
    let gaps = 0;
    for (let i = 1; i < rects.length; i += 1) gaps += Math.max(0, rects[i].top - rects[i - 1].bottom);
    const content = rects.reduce((a, r) => a + r.height, 0) + gaps
        + Math.max(0, rects[0].top - gr.top) + 8 * zoom;
    // 8px 余量：状态卡是开关条内独立卡片，有自己的 padding/border；
    // 行数多时累积误差会顶出 4px（实测 21 行时 scrollHeight 775 / clientHeight 771）。
    // 实测宿主：节点盒高 =（size 高 + 30）× zoom ⇒ size = 盒高 / zoom − 30。
    const want = Math.round((br.height - gr.height + content) / zoom - 30);
    if (!Number.isFinite(want) || want < 120) return false;
    if (Math.abs(node.size[1] - want) < 6) return false;
    node.setSize([node.size[0], want]);
    return true;
}

function paintInert(node, inert) {
    const L = t();
    for (const w of realWidgets(node)) {
        const row = rowOf(node, w.name);
        if (!row) continue;
        const on = inert.has(w.name);
        row.classList.toggle("mossllm-inert", on);
        if (on) {
            row.title = L.inert;
            row.dataset.mossllmInert = "1";
        } else if (row.dataset.mossllmInert) {
            delete row.dataset.mossllmInert;
            row.title = "";
        }
    }
}

/**
 * 组标题：每次「可见行的组」换人时标一条。
 * 单组视图下每组只出现一次；`全部展开` 时按段画（顺序是存档契约，不能为了标题去重排）。
 */
function paintHeaders(node, shown) {
    // 标题改由 applyOrder 在每组首行打 data-moss-sec（CSS ::before 渲染）。
    // 这里只保留「参数行在不在」的探测，供旧版画布兜底告警用。
    let found = 0;
    for (const w of realWidgets(node)) {
        if (!shown.has(w.name)) continue;
        const row = rowOf(node, w.name);
        if (!row) continue;
        found += 1;
        row.classList.remove("mossllm-head");
        delete row.dataset.mossllmTitle;
    }
    return found;
}

/** 这一组里有几项与默认不同（拿宿主 nodeDef 的 default 比）。 */
function countDirty(node, g) {
    if (!DEFAULTS) return 0;
    let n = 0;
    for (const name of g.items) {
        if (DIRTY_SKIP.has(name)) continue;
        const w = widgetOf(node, name);
        if (!w || !(name in DEFAULTS)) continue;
        const dv = DEFAULTS[name];
        if (dv === undefined) continue;
        const a = w.value;
        const same = (typeof dv === "number" && typeof a === "number")
            ? Math.abs(a - dv) < 1e-9 : a === dv;
        if (!same) n += 1;
    }
    return n;
}

// ---------------------------------------------------------------------------
// 开关条：分组 chip + 全部展开 + 说明行
// ---------------------------------------------------------------------------
function makeChip(node, st, g) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mossllm-chip";
    b.dataset.mossllmChip = g.key;
    const caret = document.createElement("span");
    caret.className = "mossllm-caret";
    const label = document.createElement("span");
    const count = document.createElement("span");
    count.className = "mossllm-count";
    const dot = document.createElement("span");
    dot.className = "mossllm-dot";
    // 通道组带一个前缀小字：三个 chip 并排时，「常用调优」是参数分组、
    // 「本地引擎 / 外部 API」是通道，不是同一维度，不标出来就会被当成同级三选一。
    const kind = document.createElement("span");
    kind.className = "mossllm-kind";
    b.append(kind);
    b.append(caret, label, count, dot);
    b.onclick = (e) => {
        e.stopPropagation();
        // 连接方式互斥：点哪个 chip 就把 channel 拨到那个通道（幂等；第二下仍收起视图）。
        // 先算视图目标：切 channel 会触发宿主 callback 自动聚焦该组，先切再算会把「收起」抵消。
        const wasForce = st.force;
        const next = wasForce ? g.key : (st.focus === g.key ? null : g.key);
        if (g.channel) setChannel(node, g.key === "local" ? CH_LOCAL : CH_API);
        st.force = false;
        st.focus = next;
        applySoon(node);
    };
    return { b, caret, label, count, dot, kind };
}

function apiVal(node, name) {
    const w = widgetOf(node, name);
    return w ? String(w.value == null ? "" : w.value) : "";
}

function setModel(node, id) {
    const w = widgetOf(node, "api_model");
    if (!w || !id) return;
    w.value = id;
    try { w.callback?.(id); } catch (e) { /* 老前端没有 callback */ }
    app?.graph?.setDirtyCanvas?.(true, true);
}

/**
 * 把「通道」拨到 API。点「↻ 拉模型」本身就是「我要用这个 API」的明确动作：
 * 拉完仍停在「本地 GGUF」，下次运行就会去找 llama-server（实测踩过）。
 */
function setChannel(node, v) {
    const w = widgetOf(node, "channel");
    if (!w || chanOf(String(w.value)) === v) return false;
    w.value = v;
    try { w.callback?.(v); } catch (e) { /* 老前端没有 callback */ }
    app?.graph?.setDirtyCanvas?.(true, true);
    return true;
}

/**
 * 写 `api_base` 并走它自己的 callback：挂载时装过钩子（换端点清旧模型 + 切通道），
 * 绕开 callback 就等于绕过那两件事 —— 用户会看到「填了端点，跑起来还去找本地 LLM」。
 */
function setApiBase(node, url) {
    const w = widgetOf(node, "api_base");
    if (!w || apiVal(node, "api_base") === url) return false;
    w.value = url;
    try { w.callback?.(url); } catch (e) { /* 老前端没有 callback */ }
    app?.graph?.setDirtyCanvas?.(true, true);
    return true;
}

/**
 * 把拉到的模型灌进 `api_model` 这个 combo 的选项。
 * 直接改 `options.values` 宿主不重画（实测下拉仍是旧列表），必须把同一个 widget
 * 在同索引 splice 一次触发响应式；位置不变 ⇒ 存档格数与顺序不受影响。
 */
function setModelChoices(node, models) {
    const w = widgetOf(node, "api_model");
    if (!w) return;
    const vals = [""].concat(models);
    // 存过的自定义值不在清单里时要留在选项中，否则 combo 会显示空白、存盘把值弄丢。
    const cur = String(w.value == null ? "" : w.value);
    if (cur && !vals.includes(cur)) vals.unshift(cur);
    w.options.values = vals;
    const i = node.widgets.indexOf(w);
    if (i >= 0) {
        node.widgets.splice(i, 1);
        node.widgets.splice(i, 0, w);
    }
    app?.graph?.setDirtyCanvas?.(true, true);
}

/** 拉模型清单：Key 只在这一次 POST 里过一趟，服务端不回显、不落盘。 */
async function fetchModels(node, st) {
    const base = apiVal(node, "api_base");
    const pick = st.pick;
    if (!pick) return;
    if (!base) {
        // 空端点就点「拉取」是静默空转 —— 必须说清缺哪一项。
        pick.err = t().needBase;
        pick.note = "";
        pick.btn.dataset.bad = "1";
        apply(node);
        return;
    }
    syncModelBase(node, st);
    pick.btn.disabled = true;
    pick.btn.dataset.busy = "1";
    pick.err = null;
    pick.note = "";
    const t0 = Date.now();
    // 兜底链最长要几秒，没有可见进度就像点了没反应。
    pick.busy = fmt(t().fetching, 0);
    pick.timer = setInterval(() => {
        if (!node.graph) {           // 节点已被删除：定时器必须自己停
            clearInterval(pick.timer);
            return;
        }
        pick.busy = fmt(t().fetching, Math.round((Date.now() - t0) / 1000));
        apply(node);
    }, 1000);
    apply(node);
    try {
        const r = await fetch("/moss_llm/models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_base: base, api_key: apiVal(node, "api_key") }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || ("HTTP " + r.status));
        setModelChoices(node, d.models);
        pick.err = null;
        delete pick.btn.dataset.bad;
        // 拉成功 = 用这个 API。通道还停在本地就顺手拨过去，否则下次运行又去找 llama-server。
        const switched = setChannel(node, CH_API);
        // 拉完必须看得见：空着就选中第一个（否则屏幕上除了下拉内部什么都没变）。
        if (!apiVal(node, "api_model") && d.models.length) setModel(node, d.models[0]);
        pick.note = fmt(t().fetched, d.models.length) + (switched ? "｜" + t().switched : "");
        pick.autoOpen = true;
        pick.btn.title = t().fetchTip + (d.url ? "\n" + d.url : "");
    } catch (e) {
        // 失败必须留下痕迹：按钮变红、说明行常驻原因（不靠会过期的一闪而过）。
        pick.err = t().fetchFail + "：" + String((e && e.message) || e).split("\n")[0];
        pick.note = "";
        pick.btn.dataset.bad = "1";
        console.warn("%s: 拉取模型失败", TAG, e);
    } finally {
        clearInterval(pick.timer);
        pick.timer = 0;
        pick.busy = "";
        pick.btn.disabled = false;
        delete pick.btn.dataset.busy;
        pick.btn.textContent = "↻";
        apply(node);
        if (pick.autoOpen) {
            pick.autoOpen = false;
            setTimeout(() => openCombo(node), 60);
        }
    }
}

/** 拉开原生 combo（宿主用 button[role=combobox] 渲染）：拉完直接让用户看见全部可选项。 */
function openCombo(node) {
    const row = document.querySelector('[data-widgets-grid-node-id="' + node.id
        + '"] [data-widget-name="api_model"]');
    const ctl = row && row.querySelector('[role="combobox"]');
    if (ctl) ctl.click();
}

/**
 * 换了 api_base，上一批模型名就对不上了。不清理会拿旧列表冒充这次的结果
 * （实测：新端点 404 时下拉里还是上一个服务的模型，看起来像"拉到了"）。
 */
function syncModelBase(node, st) {
    if (!st.pick) return;
    const base = apiVal(node, "api_base");
    if (st.pick.base === undefined) {
        st.pick.base = base;
        return;
    }
    if (st.pick.base === base) return;
    st.pick.base = base;
    st.pick.autoDone = false;
    st.pick.err = null;
    st.pick.note = "";
    setModelChoices(node, []);   // 只留当前值与空串
}

/**
 * 旧工作流存过的模型名可能不在这次拉到的清单里：不补进选项，combo 就显示空白，
 * 用户以为没选、存盘还可能丢值。补进去即可（只会补一次）。
 */
function ensureModelValue(node) {
    const w = widgetOf(node, "api_model");
    const vals = w && w.options && w.options.values;
    if (!Array.isArray(vals)) return;
    const cur = String(w.value == null ? "" : w.value);
    if (!cur || vals.includes(cur)) return;
    w.options.values = [cur].concat(vals);
    const i = node.widgets.indexOf(w);
    if (i >= 0) {
        node.widgets.splice(i, 1);
        node.widgets.splice(i, 0, w);
    }
}

/**
 * 载入存档后的通道对齐：旧存档里「端点填着、通道还写本地」是踩坑态（运行时会去找本地 LLM）。
 * 对齐只做一次 —— 之后用户手动切回本地就是显式选择，不再抢。
 *
 * 为什么放在 apply 而不是 onConfigure：宿主把 `widgets_values` 灌进 widgets 的时机晚于
 * onConfigure（实测：在那里读 api_base 还是空串），只能等值真的出现。
 */
function adoptApiIntent(node, st) {
    if (st.chanAligned || !apiVal(node, "api_base").trim()) return;
    st.chanAligned = true;
    // L2 契约：UI 只显示、不擅自改值。存档里「端点已填 + 通道=本地」按存档跑，
    // 由开关条上的状态块说明并给一键修复，不替用户拨通道。
}

/** 一次性说明：把状态的改变说出来，但不能一直占着摘要行（8s 后自己收）。 */
function noteOnce(node, st, text) {
    st.note = text;
    clearTimeout(st.noteTimer);
    st.noteTimer = setTimeout(() => {
        if (st.note === text) { st.note = ""; apply(node); }
    }, 8000);
}

/** 把「↻ 拉取」贴在 api_model 行右端；宿主重建行元素后自愈。 */
function pinPicker(node, st) {
    const row = rowOf(node, "api_model");
    if (!row) return false;
    if (!st.pick) {
        const box = document.createElement("span");
        box.className = "mossllm-pick";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "↻";
        btn.onclick = (e) => {
            e.stopPropagation();
            fetchModels(node, st);
        };
        box.append(btn);
        st.pick = { box, btn, err: null, note: "", busy: "", timer: 0, autoDone: false };
    }
    if (st.pick.box.parentElement !== row) row.appendChild(st.pick.box);
    st.pick.btn.title = t().fetchTip;
    // 端点与 Key 都填了、模型还空着 ⇒ 自动拉一次（每节点一次，失败不反复打服务端）
    if (!st.pick.autoDone && !apiVal(node, "api_model") && apiVal(node, "api_base")
        && apiVal(node, "api_key")) {
        st.pick.autoDone = true;
        fetchModels(node, st);
    }
    return true;
}

/** 下拉只是 `api_base` 的镜像：值以存档为准，每次 apply 对一次，所以不会漂。 */
function syncEndpointSelect(node, st) {
    const ep = st.ep;
    if (!ep) return;
    const L = t();
    // 换语言 ⇒ 只改选项文字，不重建 select（别打断可能正开着的那次下拉）。
    if (ep.L !== L) {
        ep.L = L;
        const items = [{ url: "", text: L.ep_custom }]
            .concat(ENDPOINTS.map((p) => ({ url: p.url, text: L["ep_" + p.id] })));
        ep.sel.textContent = "";
        for (const it of items) {
            const o = document.createElement("option");
            o.value = it.url;
            o.textContent = it.text;
            ep.sel.append(o);
        }
        ep.sel.title = L.epTip;
    }
    const cur = apiVal(node, "api_base");
    ep.sel.value = ENDPOINTS.some((p) => p.url === cur) ? cur : "";
}

/**
 * 把「常用端点」贴在 api_base 行右端；宿主重建行元素后自愈（与 pinPicker 同一手法，
 * 两处各管一格：↻ 在 api_model 行，这个在 api_base 行）。
 * 它**不是 widget**：不进 `widgets_values`，存档格数一格都不动。
 */
function pinEndpoints(node, st) {
    const row = rowOf(node, "api_base");
    if (!row) return false;
    if (!st.ep) {
        const box = document.createElement("span");
        box.className = "mossllm-ep";
        const sel = document.createElement("select");
        sel.className = "mossllm-epsel";
        sel.dataset.mossllmEndpoint = "1";
        // 行内控件同样不该把点击变成拖节点（与开关条同一套事件）。
        for (const ev of ["pointerdown", "mousedown", "dblclick", "click"]) {
            sel.addEventListener(ev, (e) => e.stopPropagation());
        }
        sel.onchange = (e) => {
            e.stopPropagation();
            const url = String(sel.value || "");
            if (!url) { syncEndpointSelect(node, st); return; }   // 「自定义」：一个字节都不改
            if (setApiBase(node, url)) {
                const hit = ENDPOINTS.find((p) => p.url === url) || {};
                noteOnce(node, st, t().epDone.replace("%s", t()["ep_" + hit.id] || url));
            }
            applySoon(node);
        };
        box.append(sel);
        st.ep = { box, sel, L: null };
    }
    if (st.ep.box.parentElement !== row) row.appendChild(st.ep.box);
    syncEndpointSelect(node, st);
    return true;
}


/**
 * 把「安装/更新」与「测试」贴在 runtime 行右端（与 ↻、常用端点同一手法）。
 * 两个按钮都**不是 widget**：不进 `widgets_values`，存档格数一格不动。
 */

/**
 * 运行时动作：安装/更新 与 测试。
 *
 * 为什么不贴到 runtime 行右端：那一行由 widget 占满，塞文本按钮会溢出/错位（实测）。
 * 这里与预设按钮同一处——放在开关条里、只在该组展开时显示，行布局一个像素都不动，
 * 也不新增 widget（存档仍 39 格）。
 */
function buildRuntimeTools(node, st) {
    const L = t();
    const box = document.createElement("span");
    box.className = "mossllm-rt";
    box.hidden = true;
    const post = async (url, body) => {
        const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}) });
        try { return await r.json(); } catch (e) { return { ok: false, error: "HTTP " + r.status }; }
    };
    const mk = (key, run) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "mossllm-preset";
        b.dataset.mossllmRuntime = key;
        b.onclick = async (e) => {
            e.stopPropagation();
            if (b.disabled) return;
            b.disabled = true;
            try { await run(); } finally { b.disabled = false; applySoon(node); }
        };
        return b;
    };
    const install = mk("install", async () => {
        noteOnce(node, st, t().rtInstalling);
        const j = await post("/moss_llm/runtime/install");
        noteOnce(node, st, j.ok ? fmt(t().rtInstalled, j.tag || j.exe || "") : fmt(t().rtProbeFail, j.error || ""));
    });
    const test = mk("test", async () => {
        const j = await post("/moss_llm/runtime/probe",
            { source: apiVal(node, "runtime"), server_exe: apiVal(node, "server_exe") });
        noteOnce(node, st, j.ok ? fmt(t().rtProbeOk, j.version || j.exe || "") : fmt(t().rtProbeFail, j.error || ""));
    });
    box.append(install, test);
    st.rt = { box, install, test, L };
    return box;
}

function buildBar(node, st) {
    const bar = document.createElement("div");
    bar.className = "mossllm-bar";
    bar.style.flex = "0 0 auto";
    bar.style.alignContent = "flex-start";
    for (const ev of ["pointerdown", "mousedown", "dblclick", "keydown", "click"]) {
        bar.addEventListener(ev, (e) => e.stopPropagation());
    }
    st.statusCard = document.createElement("div");
    st.statusCard.className = "mossllm-status";
    st.hint = document.createElement("span");
    st.hint.className = "mossllm-hint";
    st.warn = document.createElement("span");
    st.warn.className = "mossllm-warn";
    st.warn.hidden = true;
    st.warn.onclick = (e) => {
        e.stopPropagation();
        const act = st.warn.dataset.act || "";
        if (act === "chan") { setChannel(node, CH_API); applySoon(node); return; }
        if (act === "local") { setChannel(node, CH_LOCAL); applySoon(node); return; }
        if (act === "raw") { st.raw = true; applySoon(node); }
    };
    st.langSlot = document.createElement("span");
    st.langSlot.className = "mossllm-langslot";
    st.statusCard.append(st.hint, st.warn, st.langSlot);
    bar.append(st.statusCard);
    return bar;
}

function applyPreset(node, st, key) {
    return;   // 预设已由 style/style_class 承担
    const L = t();
    const spec = key === "reset" ? (DEFAULTS || {}) : PRESETS[key];
    if (!spec) return;
    // reset 只恢复「采样 + 内容」两组，不碰引擎/连接/输出格式。
    const names = key === "reset"
        ? GROUPS.filter((g) => g.key === "sampling" || g.key === "content")
            .flatMap((g) => g.items) : Object.keys(spec);
    let n = 0;
    for (const name of names) {
        const w = widgetOf(node, name);
        if (!w || !(name in spec)) continue;
        if (String(w.value) === String(spec[name])) continue;
        w.value = spec[name];
        try { w.callback?.(spec[name]); } catch (e) { /* 老前端没有 callback */ }
        n += 1;
    }
    // 值改了但参数可能是收起的看不见 ⇒ 必须说一句，否则又成「点了没反应」。
    noteOnce(node, st, key === "reset" ? L.presetResetDone
        : fmt(L.presetDone, L["preset_" + key], n));
    applySoon(node);
    app?.graph?.setDirtyCanvas?.(true, true);
}

/**
 * 执行前就能判断的「还缺什么」——只列**前端看得见、且缺了必然跑不起来**的项。
 *
 * Key 空不算缺：Ollama / LM Studio / vLLM 本来就不要 Key，报「缺 Key」会把人往错方向带。
 * llama-server 有没有也不在这里判断（那要碰文件系统），交给执行期那条定向报错。
 */
function lacksOf(node, meta) {
    if (!meta || meta.assemble) return null;         // 拼装任务不开模型
    if (meta.ch === CH_API) {
        if (!apiVal(node, "api_base").trim()) return "base";
        if (!apiVal(node, "api_model").trim()) return "name";
        return null;
    }
    // 后端在没有 GGUF 时把占位符塞进选项（"(未找到 GGUF 模型)"）；真实文件名不会以括号开头。
    const m = apiVal(node, "model");
    return (!m || m.startsWith("(")) ? "model" : null;
}

/** V2/V5/V6：三个区每帧从 widget / st 读回，不另存与真实值可能漂移的状态。 */
function paintBar(node, st, shown, off, meta) {
    if (!st.hint) return;
    const task = String(apiVal(node, "task") || "反推");
    const style = String(apiVal(node, "style") || "");
    const hasImg = (node.inputs || []).some((i) => i && i.name
        && i.name.startsWith("image") && i.link != null);
    const txt = String(apiVal(node, "text") || "").trim();
    const online = !!String(apiVal(node, "api_base") || "").trim();
    st.hint.textContent = task + " · " + (online ? "在线服务" : "本机")
        + (style ? " ｜ 写法：" + style : "");
    let need = "";
    if ((task === "反推" || task === "洗图") && !hasImg) need = "还接一张图";
    else if (task === "扩写" && !txt) need = "还填一句想写什么";
    else if (online && !String(apiVal(node, "api_model") || "").trim()) need = "还填在线模型名";
    st.warn.hidden = !need;
    st.warn.textContent = need;
    st.hint.title = need || "可以直接运行";
    st.summary = st.hint.textContent;
}

/** style_class → style 子档级联。 */
function syncStyle(node) {
    const cls = widgetOf(node, "style_class");
    const sty = widgetOf(node, "style");
    if (!cls || !sty) return;
    const subs = STYLE_CLASS[String(cls.value)] || [];
    if (!subs.length) return;
    // 宿主不同版本读不同字段：options.values 与 w.values 都写。
    const cur = (sty.options && Array.isArray(sty.options.values)) ? sty.options.values
        : (Array.isArray(sty.values) ? sty.values : null);
    const same = cur && cur.length === subs.length && cur.every((v, i) => v === subs[i]);
    if (!same) {
        if (!sty.options) sty.options = {};
        sty.options.values = subs.slice();
        sty.values = subs.slice();
    }
    if (!subs.includes(String(sty.value))) sty.value = subs[0];
}

function applySoon(node) {
    apply(node);
    try { requestAnimationFrame(() => apply(node)); } catch (e) { /* 无 rAF */ }
    setTimeout(() => {
        apply(node);
        // 行重建/CSS order 晚于首帧；补算一次，尺寸已对时 fitHeight 自带早退。
        // 必须与 fitHeight 的排序同做，否则会把虚高 want 写得更稳。
        try { fitHeight(node, plan(node, stateOf(node)).shown); } catch (e) { /* noop */ }
    }, 300);
}

/**
 * 开关条**置顶**：`addDOMWidget` 只会追加到末尾，而末尾意味着每展开一组它就被顶下一截
 * （2026-10-30 实测反馈：点开一组，开关就跑到最低下）。这里把那个 widget 挪到数组首位 ——
 * 只动「不参与存档的 DOM 组件」的位置，39 个参数之间的相对顺序一个都没变；
 * 存盘时 `installSaveCleanup` 照样把它那一格剔掉（实测仍 39 格、逐位一致）。
 * 宿主重建 widget 数组后位置会退回末尾，所以 `apply()` 每次都自愈。
 */
function ensureBarFirst(node, st) {
    const w = st.barWidget;
    if (!w || !node.widgets || node.widgets[0] === w) return false;
    const i = node.widgets.indexOf(w);
    if (i < 0) return false;
    node.widgets.splice(i, 1);
    node.widgets.unshift(w);
    return true;
}

function protectFocus(node, st, shown) {
    const act = document.activeElement;
    const row = act && act.closest ? act.closest("[data-widget-name]") : null;
    if (!row) return;
    const grid = row.closest('[data-widgets-grid-node-id="' + node.id + '"]');
    if (!grid || shown.has(row.dataset.widgetName)) return;
    const c = st.chips && (st.chips[st.focus] || st.chips.tune);
    if (c) { try { c.b.focus(); } catch (e) { /* noop */ } }
}

function apply(node) {
    const st = stateOf(node);
    ensureBarFirst(node, st);
    syncStyle(node);
    const meta = plan(node, st);
    const { shown } = meta;
    paintBar(node, st, shown, meta.off, meta);
    try { pinPicker(node, st); } catch (e) { /* 行内挂件失败不影响状态行 */ }
    try { pinEndpoints(node, st); } catch (e) { /* 同上 */ }
    return shown.size;
}

// ---------------------------------------------------------------------------
// 挂载
// ---------------------------------------------------------------------------
function focusForChannel(node, st, load) {
    if (st.force) return;
    const cw = widgetOf(node, "channel");
    if (!cw) return;
    const ch = chanOf(String(cw.value));
    const cur = GROUPS.find((g) => g.key === st.focus);
    if (load) {
        // 冷载入只对 api 兜底：local 保持默认 5 行，别把首屏撑开。
        if (ch === CH_API && (!cur || cur.channel !== CH_API)) st.focus = "api";
        return;
    }
    const want = GROUPS.find((g) => g.channel && g.channel === ch);
    if (want && (!cur || cur.channel !== want.channel)) st.focus = want.key;
}

function mount(node) {
    if (!node || node.type !== TYPE || node.__mossllmBar) return false;
    const ws = realWidgets(node);
    if (!ws.some(hideable)) {
        node.__mossllmBar = "unsupported";
        console.warn("%s: 本前端没有 widget 显隐 API ⇒ 不折叠，%d 个参数全部照常显示", TAG, ws.length);
        return false;
    }
    const st = stateOf(node);
    let bar;
    try {
        ensureStyle();
        bar = buildBar(node, st);
        // `serialize:false` = 告诉宿主别存；存盘时由 `moss_sb_widget_order.js` 真把那一格剔掉。
        st.barWidget = node.addDOMWidget(BAR_NAME, "custom", bar,
            { serialize: false, hideOnZoom: false, getMinHeight: () => 40 });
    } catch (e) {
        // 挂不上开关就不藏任何东西 —— 没有开关的隐藏等于把参数弄丢
        node.__mossllmBar = "noDom";
        console.warn("%s: 该前端不支持节点 DOM 组件 ⇒ 面板不启用，参数全部照常显示", TAG, e);
        return false;
    }
    node.__mossllmBar = bar;
    ensureBarFirst(node, st);
    installSaveCleanup(node);
    // 存盘时清空 Key（依赖倒置：通用模块只认字段名集合，不认识业务字段）。
    node.__mosssbOmitOnSave = new Set(["api_key"]);
    installHintListener();
    focusForChannel(node, st, true);
    applySoon(node);
    loadDefs();

    const cw = widgetOf(node, "channel");
    if (cw) {
        const orig = cw.callback;
        cw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            // 换通道 ⇒ 把参数面板切到那个通道的组：不切的话用户看不到 api_* / mmproj，
            // 填不了必填项还以为节点坏了。
            focusForChannel(node, st, false);
            applySoon(node);
            return r;
        };
    }
    // 换端点 ⇒ 上一批模型名立刻作废。等 1.5s 兜底轮询太慢，更像"没反应"。
    // 同一处顺手把通道拨过去：填了端点 = 要用这个 API（踩过的坑：填完直接跑，去本地找 llama-server）。
    const bw = widgetOf(node, "api_base");
    if (bw) {
        const orig = bw.callback;
        bw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            // L2：填端点不再自动拨通道；只有用户显式点 chip 或状态块才改连接方式。
            applySoon(node);
            return r;
        };
    }
    // 摘要行与缺项都在读 task / model：值一变就必须重画。不装这个回调，只能等 1.5s 兜底轮询，
    // 用户看到的就是「切了模式，上面那行字没动」（实测）。
    for (const name of ["task", "model"]) {
        const w = widgetOf(node, name);
        if (!w || w.__mossRepaintHooked) continue;
        const orig = w.callback;
        w.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            applySoon(node);
            return r;
        };
        w.__mossRepaintHooked = true;
    }
    // 手输模型名（不走 ↻ 拉取）同样是"我要用这个 API"。
    const mw = widgetOf(node, "api_model");
    if (mw && !mw.__mossChanHooked) {
        const orig = mw.callback;
        mw.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            // L2：手填模型名同样不自动拨通道（只重画，让状态块去说明）。
            applySoon(node);
            return r;
        };
        mw.__mossChanHooked = true;
    }
    hookAllRepaint(node);
    const oc = node.onConfigure;
    node.onConfigure = function (...args) {
        const r = oc ? oc.apply(this, args) : undefined;
        focusForChannel(node, st, true);       // 存过的 api 工作流重开后要能看见 api_model
        applySoon(node);                       // 载入时 widget 是重建的，隐藏位要重打
        return r;
    };
    return true;
}

const smiths = () => (app?.graph?.nodes || []).filter((n) => n && n.type === TYPE);

let booted = false;
let tries = 0;

/** 已有专用回调的 widget（它们自己会调 applySoon）不重复包装。 */
const REPAINT_SKIP = new Set(["channel", "api_base", "api_model", "task", "model"]);
/** 其余 widget 一改值立刻重画；宿主重建 widget 后由 guard 补挂。 */
function hookAllRepaint(node) {
    for (const w of realWidgets(node)) {
        if (!w || w.__mossRepaintHooked || REPAINT_SKIP.has(w.name)) continue;
        const orig = w.callback;
        w.callback = function (...args) {
            const r = orig ? orig.apply(this, args) : undefined;
            applySoon(node);
            return r;
        };
        w.__mossRepaintHooked = true;
    }
}

function guard() {
    for (const node of smiths()) {
        try {
            if (!node.__mossllmBar) mount(node);
            else if (node.__mossllmBar.nodeType) {
                hookAllRepaint(node);
                apply(node);                                   // 抗覆盖：别的扩展可能改回 hidden
            }
        } catch (e) {
            // 静默会让「状态行一直空」这种问题无从定位。出声，但每节点只报一次。
            if (!node.__mossErrOnce) {
                node.__mossErrOnce = true;
                console.error(TAG, "apply 失败（后续每轮仍会重试）", e);
            }
        }
    }
}

function boot(why) {
    if (booted) return;
    try {
        if (!document.body || !app || !app.graph) {
            if (tries++ < 60) setTimeout(() => boot("retry"), 500);     // 最多 30s
            return;
        }
        booted = true;
        // 语言按钮是核心 i18n 画的（挂在面板的 langSlot 里）—— 它发广播，这里跟着换
        // 自己的分组标题 / 摘要 / 说明行；39 个参数的标签与提示由 `applyLang` 负责。
        try {
            window.addEventListener(LANG_EVENT, (e) => {
                const lang = String((e && e.detail && e.detail.lang) || "");
                T = lang ? I18N[lang === "zh" ? "zh" : "en"] : null;
                for (const n of smiths()) {
                    try { apply(n); } catch (err) { /* 单个节点失败不影响其它 */ }
                }
            });
        } catch (e) { /* 无 window */ }
        guard();
        // 载入工作流时 `nodeCreated` 不一定触发（i18n 实测同一坑），所以留一轮兜底轮询。
        setInterval(guard, 1500);
        console.info("%s: 就绪（入口 %s，本节点 %d 个）", TAG, why, smiths().length);
    } catch (e) {
        console.error("%s: 启动异常（面板不可用，参数全部照常显示）", TAG, e);
    }
}

app.registerExtension({
    name: "mosssb.llm.panel",
    async setup() { boot("setup"); },
    nodeCreated(node) { if (node && node.type === TYPE) mount(node); },
});

boot("module");

// 排障入口：控制台 `MOSS_SB_LLM_PANEL.where()` / `.focus("local")` / `.expand()` / `.values()`
try {
    globalThis.MOSS_SB_LLM_PANEL = {
        where: () => smiths().map((n) => ({
            id: n.id,
            mounted: !!(n.__mossllmBar && n.__mossllmBar.nodeType),
            widgets: realWidgets(n).length,
            hidden: realWidgets(n).filter((w) => w.options && w.options.hidden).length,
            focus: n.__mossllm ? n.__mossllm.focus : null,
            task: taskOf(n),
            force: !!(n.__mossllm && n.__mossllm.force),
            dirty: GROUPS.filter((g) => !g.always)
                .map((g) => g.key + ":" + countDirty(n, g)).join(","),
        })),
        focus: (key) => { for (const n of smiths()) { stateOf(n).force = false; stateOf(n).focus = key || null; apply(n); } },
        expand: () => { for (const n of smiths()) { stateOf(n).force = true; apply(n); } },
        fold: () => { for (const n of smiths()) { stateOf(n).force = false; stateOf(n).focus = null; apply(n); } },
        values: () => smiths().map((n) => (n.serialize?.().widgets_values || []).length),
    };
} catch (e) { /* 无 window */ }
