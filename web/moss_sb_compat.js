// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 旧工作流兼容：加载时把错位的 `widgets_values` 校正回来（纯逻辑在 moss_sb_widget_order.js）。
//
// 只在 `configure` 之前动手：那时 widget 上还是**默认值**，正好拿来补缺失的那几格。
// 判据与不动手的情形都写在纯函数里；这里只负责挂钩、提示、重绘。

import { app } from "../../scripts/app.js";
import { NODES } from "./moss_sb_i18n.js";
import { repairValues } from "./moss_sb_widget_order.js";

const TAG = "[MOSS SB] compat";
const OWN = new Set(Object.keys(NODES));

/**
 * 留痕只写一次/节点。用 WeakSet 存节点实例而不是拼 key ——
 * 拼 `${type}:${delta}:${at}` 会让同类型的第二个旧稿命中同一个 key，于是它修好了却没留痕。
 */
const HINTED = new WeakSet();

function notice(res, node) {
    const up = res.delta > 0;
    const what = up
        ? `补上 ${res.added.join(" / ")} 的默认值`
        : `删掉 ${res.removed.join(" / ")} 这个多余值`;
    // 补值 ⇒ 后段右移；删值 ⇒ 后段左移。说反了比不说更误导。
    // 用模板字符串而不是 `%s` 占位：占位符依赖控制台插值，日志被转发/收集时常常原样露出。
    console.warn(`${TAG}: 旧工作流的参数比当前版本${up ? "少" : "多"} ${Math.abs(res.delta)} 格，`
        + `已在第 ${res.at + 1} 项${what}，其余值向${up ? "右" : "左"}移。`
        + "界面上的红框会消失，但**请重新保存一次工作流**。");
    if (!node || HINTED.has(node)) return;
    HINTED.add(node);
    try {
        node.properties = node.properties || {};
        node.properties.msb_widgets_repaired = up
            ? `+${res.added.join(",")}`
            : `-${res.removed.join(",")}`;
    } catch (e) { /* properties 写不进去不影响值 */ }
}

app.registerExtension({
    name: "MossSB.WidgetCompat",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!OWN.has(nodeData?.name)) return;
        const proto = nodeType.prototype;
        if (proto.__msbCompat) return;
        proto.__msbCompat = true;

        const orig = proto.configure;
        proto.configure = function (info, ...rest) {
            let fixed = null;
            try {
                fixed = repairValues(info && info.widgets_values, this.widgets);
            } catch (e) { /* 判不出来就交给宿主原样加载 */ }
            const use = fixed ? Object.assign({}, info, { widgets_values: fixed.values }) : info;
            const out = typeof orig === "function" ? orig.call(this, use, ...rest) : undefined;
            if (fixed) {
                try {
                    this.widgets_values = fixed.values;
                    notice(fixed, this);
                    app.canvas?.setDirty(true, true);
                } catch (e) { /* 提示失败不影响已修好的值 */ }
            }
            return out;
        };
    },
});
