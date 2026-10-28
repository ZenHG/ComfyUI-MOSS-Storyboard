// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// 旧工作流的 `widgets_values` 与当前参数表对不上时，把错位纠正回来。
//
// 为什么需要：`widgets_values` 是**按位置**存的（ComfyUI 的硬约定，不是我们的选择）。
//   ⇒ 只要参数个数变过一次，老工作流就会整体错位一位：格数拿到 "16:9"、下拉拿到 512，
//   界面上那几个下拉直接变红，而且**不会报错**——用户只会觉得"这节点坏了"。
//   （2026-10-28 真机复现：少一格的旧稿加载后 panels=16:9、panel_aspect=496、mode=1024。）
//
// 判据只有一条：**长度对不上才修，且修完必须每个值都合法**。
//   长度对得上时哪怕有单个非法值也**不猜**——那更可能是用户手改的，替人做主比报错更坏。
//
// 纯函数（不 import 宿主），所以 `tests/test_compat.mjs` 能直接跑。

/**
 * 参与序列化的 widget。
 * ⚠️ 只有「`serialize:false` **且** 是 DOM 组件（有 `element`）」才不占位 ——
 *   前端自己插的 `control_after_generate` 也标了 `serialize:false`，但它**是**存进
 *   `widgets_values` 的（2026-10-28 实测：照 `serialize` 一刀切会把它当不存在，全表错位）。
 */
export function serializableWidgets(widgets) {
    return (widgets || []).filter((w) => w && !(w.options && w.options.serialize === false && w.element));
}

/** 一个 widget 的"合法值"口径。 */
export function widgetSpec(w) {
    const vals = w && w.options && w.options.values;
    if (Array.isArray(vals)) return { kind: "combo", values: vals };
    const t = String((w && w.type) || "");
    if (t === "number" || t === "slider") return { kind: "number" };
    if (t === "toggle") return { kind: "boolean" };
    return { kind: "text" };
}

export function valueOk(spec, v) {
    if (!spec) return true;
    if (spec.kind === "combo") {
        return spec.values.some((x) => x === v || String(x) === String(v));
    }
    if (spec.kind === "number") return typeof v === "number" && Number.isFinite(v);
    if (spec.kind === "boolean") return typeof v === "boolean";
    return typeof v === "string";
}

/**
 * 尝试把 `values` 校正到 `widgets` 的当前顺序。
 * @returns {{values:any[], at:number, delta:number, added:string[], removed:string[]}|null}
 *   null = 不需要修 / 不敢修（长度一致、或一个候选都对不上）
 */
export function repairValues(values, widgets, opts = {}) {
    const list = serializableWidgets(widgets);
    if (!Array.isArray(values) || !list.length) return null;

    const delta = list.length - values.length;
    if (delta === 0) return null;                       // 长度一致 ⇒ 不动
    const maxShift = opts.maxShift == null ? 2 : opts.maxShift;
    if (Math.abs(delta) > maxShift) return null;        // 差太多，多半不是同一张图

    const specs = list.map(widgetSpec);
    const defaults = list.map((w) => w.value);
    const broken = values.filter((v, i) => i < specs.length && !valueOk(specs[i], v)).length;
    // 只错一个值时更可能是用户手改过的脏数据；整体错位一定会连带坏好几个。
    if (broken < 2) return null;

    const fits = (cand) => cand.length === list.length
        && cand.every((v, i) => valueOk(specs[i], v));

    if (delta > 0) {
        // 少了几格：从**最靠后**的插入点往前试（新增参数追加在末尾是最常见的情形）
        for (let p = values.length; p >= 0; p -= 1) {
            const cand = values.slice(0, p)
                .concat(defaults.slice(p, p + delta), values.slice(p));
            if (fits(cand)) {
                return { values: cand, at: p, delta,
                         added: list.slice(p, p + delta).map((w) => w.name), removed: [] };
            }
        }
        return null;
    }

    // 多了几格：反过来删。仍从最靠后的位置往前试。
    for (let p = values.length - 1; p >= 0; p -= 1) {
        const cand = values.slice(0, p).concat(values.slice(p - delta));
        if (fits(cand)) {
            return { values: cand, at: p, delta,
                     added: [], removed: values.slice(p, p - delta).map((v) => String(v).slice(0, 24)) };
        }
    }
    return null;
}
