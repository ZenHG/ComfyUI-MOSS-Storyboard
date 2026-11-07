// SPDX-License-Identifier: MIT
// Copyright (c) 2026 ComfyUI-MOSS-Storyboard contributors
//
// LLM 提示词节点的多图参考槽：默认 2 个，接满当前所有槽才长出下一个，上限 9。
//
// 为什么这样做：H3 最多认 9 张参考图，但一上来摆 9 个空槽会把节点撑得很高，
// 而绝大多数人只用 1 张。判据是「连接数」而不是一个数量旋钮 —— 用户接第 2 张图
// 时第 3 个槽自己出现，不用先想「我要填几」。
//
// 纪律：
//   · 只回收**尾部未连接**的槽，绝不拆已连的（拆了会丢线）。
//   · 只增删输入槽，不碰 widgets ⇒ `widgets_values` 的格数与位置一个字节都不变。
//   · 用微任务重排，避免在 onConnectionsChange 里同步改 inputs 造成重入。

import { app } from "../../scripts/app.js";

const TYPE = "MossPromptSmith";
const MIN_SLOTS = 2;
const MAX_SLOTS = 9;

const slotName = (i) => (i === 1 ? "image" : `image${i}`);

function slotIndex(name) {
    if (name === "image") return 1;
    const m = /^image(\d+)$/.exec(String(name || ""));
    return m ? Number(m[1]) : 0;
}

function imageSlots(node) {
    return (node.inputs || []).filter((x) => slotIndex(x.name) > 0);
}

function rebuild(node) {
    if (!node || node.__mossImgBusy) return;
    node.__mossImgBusy = true;
    try {
        const cur = imageSlots(node);
        const connected = cur.filter((s) => s.link != null).length;
        const target = Math.min(MAX_SLOTS, Math.max(MIN_SLOTS, connected + 1));
        if (target > cur.length) {
            for (let i = cur.length + 1; i <= target; i += 1) {
                node.addInput(slotName(i), "IMAGE");
            }
        } else if (target < cur.length) {
            for (let i = cur.length; i > target; i -= 1) {
                const s = (node.inputs || []).find((x) => x.name === slotName(i));
                if (s && s.link == null) node.removeInput(node.inputs.indexOf(s));
            }
        }
    } finally {
        node.__mossImgBusy = false;
    }
}

const defer = (node) => setTimeout(() => rebuild(node), 0);

app.registerExtension({
    name: "MossSB.LLMImageSlots",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeData || nodeData.name !== TYPE) return;

        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = created ? created.apply(this, arguments) : undefined;
            defer(this);
            return r;
        };

        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = configured ? configured.apply(this, arguments) : undefined;
            defer(this);
            return r;
        };

        const changed = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const r = changed ? changed.apply(this, arguments) : undefined;
            defer(this);
            return r;
        };
    },
});
