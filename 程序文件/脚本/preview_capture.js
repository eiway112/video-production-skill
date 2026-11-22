/**
 * Instant Preview - Scene Screenshot Capture
 * Uses puppeteer-core (bundled with HyperFrames) to capture scene thumbnails.
 *
 * Usage:
 *   node preview_capture.js <html_path> <config_path> <output_dir>
 *
 * Output:
 *   - scene_N_start.png, scene_N_end.png for each scene
 *   - capture_manifest.json with metadata
 */

const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const http = require('http');

// Chrome path (same as HyperFrames uses)
const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const FALLBACK_SIZE = { width: 1920, height: 1080 };

function readCompositionSize(htmlPath) {
    let html;
    try {
        html = fs.readFileSync(htmlPath, 'utf-8');
    } catch (e) {
        return FALLBACK_SIZE;
    }
    let m = html.match(/data-width="(\d+)"[^>]*data-height="(\d+)"/);
    if (!m) {
        m = html.match(/body\s*\{[^}]*width\s*:\s*(\d+)px[^}]*height\s*:\s*(\d+)px/);
    }
    if (!m) return FALLBACK_SIZE;
    const width = parseInt(m[1], 10);
    const height = parseInt(m[2], 10);
    if (!(width > 0 && height > 0)) return FALLBACK_SIZE;
    return { width, height };
}

/**
 * 稳态采样时刻计算（text_fit 与 bar_overflow 共用）。
 *
 * 两个判据的采样点语义相同（各场稳态：S-block 优先，无则 T-block，再无则 config 场景窗口；
 * 每场 start+2.5 与 end-1.5），住两处必漂移——一份实现单点导出。t=0 取数会把入场
 * 动画中途的元素当结论（text_fit 探针实测踩到过假阳），故所有几何判据共用本函数。
 */
async function computeSampleState(page, fallbackWindows) {
    return await page.evaluate((fb) => {
        const out = [];
        const T = (typeof window.T !== 'undefined' && window.T) ? window.T : null;
        const S = (typeof window.S !== 'undefined' && window.S) ? window.S : null;
        let timeSrc = 'none';
        if (S && S.length) {
            timeSrc = 'S-block';
            for (const s of S) {
                if (s.cover) {
                    // 封面纳入稳态取数面（2026-11-06 竖屏复盘）。旧实现整场 `continue`，
                    // 后果不是"少一张截图"而是 text_fit／bar_overflow 对封面**零对象**：
                    // 两支竖屏交付的封面缺陷（文字塌成每行 2 字、图区偏小）都在预览判据
                    // PASS 之下发生——判据在位、取数面没有对象（A06 同族）。
                    // 只取一个中点而非 start+2.5／end-1.5：封面窗常 ≤3s 且末段带淡出，
                    // 那两个点会落在退场中间，读到的半透明中间态反造假阳。
                    const dur = s.end - s.start;
                    if (dur > 0) {
                        const mid = Math.min(Math.max(s.start + dur / 2, s.start + 1.0),
                                            Math.max(s.start + 1.0, s.end - 0.3));
                        out.push(+mid.toFixed(2));
                    }
                    continue;
                }
                out.push(s.start + 2.5, Math.max(s.start + 2.5, s.end - 1.5));
            }
        } else if (T) {
            timeSrc = 'T-block';
            const vs = Object.values(T).sort((a, b) => a - b);
            vs.forEach((v, i) => {
                out.push(v + 2.5);
                if (i + 1 < vs.length) out.push(vs[i + 1] - 1.5);
            });
        } else if (fb && fb.length) {
            timeSrc = 'config-scenes';
            for (const s of fb) {
                out.push(s.start + 2.5, Math.max(s.start + 2.5, s.end - 1.5));
            }
        }
        const tl = (typeof window.tl !== 'undefined') ? window.tl : null;
        const dur = tl ? tl.duration() : (out.length ? Math.max(...out) + 3 : 10);
        const times = [...new Set(out.map(x => +x.toFixed(2)))]
            .filter(x => x >= 0 && x <= dur).sort((a, b) => a - b);
        return { times, timeSrc, hasTimeline: !!tl, timelineDuration: +dur.toFixed(1) };
    }, fallbackWindows);
}

/**
 * 版面塌陷（每行字数）取数面（2026-10-12 接入；判据住在 instant_preview.py，本函数只取数）。
 *
 * 为什么在预览里加这一条：既有三条判据（字幕安全区、视觉边界、画面内容终检）管"越界"
 * 与"空场"，没有一条管"字框过窄"。消费者副本一份已交付竖屏片的正文塌成每行 3 字的
 * 竖排列，全程绿到交付（读数见 操作日志.md [2026-10-12] 同名条）。
 *
 * 取数口径（两条必修，均为该轮实测踩到后写入）：
 *  ① 行盒用 Range.getClientRects() 数浏览器**实际渲染出的行**，不用"容器宽／字号"几何
 *    推算——中文实际字宽、letter-spacing、flex 挤压都不在几何式里；
 *  ② 时刻取各场**稳态**（S-block 优先，无则 T-block，再无则调用方传入的场景窗口），
 *    每场 start+2.5 与 end-1.5 两点，经页面全局 tl.seek() 落位（与本项目既有预览
 *    同一 seek 通道，不另造第二套时间语义）。在 t=0 量会把宽度动画中的元素误判成
 *    塌陷（假阳，实测 .fr-bar）、并漏掉晚揭示的长句（假阴）。
 *  ③ 聚合＝**逐元素跨采样取最优（最大）读数**：一个文本框在它最舒展那一刻仍不达标才算
 *    真塌陷；取最坏读数等于把入场过程当结论。
 *
 * 本函数只返回原始读数（候选节点列表 + 取数面元数据），不做四态裁定，也不写阈值——
 * 阈值由调用方（instant_preview 读 video_quality_rules.json → text_fit）传入。
 */
async function measureTextFit(page, thresholds, fallbackWindows) {
    const minLines = thresholds.minLines;
    const minChars = thresholds.minChars;

    const sampling = await computeSampleState(page, fallbackWindows);

    if (sampling.timeSrc === 'none') {
        // 三个时刻源都没有＝稳态采样无从落位，属取数断链，不得由"没发现问题"短路成通过
        return { status_hint: 'no_time_source', samples: 0, measured_nodes: 0, nodes: [] };
    }

    const best = {};
    for (const t of sampling.times) {
        const rows = await page.evaluate(({ time, minLines, minChars }) => {
            if (typeof tl !== 'undefined' && tl && tl.seek) {
                tl.seek(time, false);
                if (typeof gsap !== 'undefined') gsap.ticker.tick();
            }
            const out = [];
            const chainOpacity = (el) => {
                let o = 1, n = el;
                while (n && n !== document.body) {
                    const cs = getComputedStyle(n);
                    o *= parseFloat(cs.opacity || '1');
                    if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
                    n = n.parentElement;
                }
                return o;
            };
            for (const el of document.querySelectorAll('body *')) {
                // 只认节点"自己持有"的文本，嵌套子节点的文本由那个子节点自己作答（否则同一段
                // 文字被父/子各报一次，且父级行盒数是整块布局的行数、不是这句话的行数）
                const own = Array.from(el.childNodes)
                    .filter(n => n.nodeType === 3 && n.textContent.trim());
                if (!own.length) continue;
                const text = own.map(n => n.textContent.trim()).join('').replace(/\s+/g, '');
                if (text.length < minChars) continue;
                if (chainOpacity(el) < 0.5) continue;          // 未在场即不裁定
                const cs = getComputedStyle(el);
                const fsz = parseFloat(cs.fontSize) || 0;
                if (!fsz) continue;
                const range = document.createRange();
                own.forEach(n => { try { range.selectNodeContents(n); } catch (e) {} });
                const rects = Array.from(range.getClientRects()).filter(x => x.height > 2);
                if (rects.length < minLines) continue;          // 未折行即无"每行字数"可言
                const box = el.getBoundingClientRect();
                out.push({
                    key: (el.id ? '#' + el.id
                          : (String(el.className).split(' ')[0] || el.tagName)) + ':' + text.slice(0, 12),
                    cpl: +(text.length / rects.length).toFixed(2),
                    lines: rects.length, chars: text.length,
                    font_px: +fsz.toFixed(1), box_w: Math.round(box.width),
                    capacity: +(box.width / fsz).toFixed(2), at: time,
                });
            }
            return out;
        }, { time: t, minLines, minChars });
        for (const row of rows) {
            if (!best[row.key] || row.cpl > best[row.key].cpl) best[row.key] = row;
        }
    }

    const nodes = Object.values(best).sort((a, b) => a.cpl - b.cpl);
    return {
        time_src: sampling.timeSrc,
        has_timeline: sampling.hasTimeline,
        timeline_duration: sampling.timelineDuration,
        samples: sampling.times.length,
        sample_times: sampling.times.slice(0, 40),
        min_lines: minLines,
        min_chars: minChars,
        measured_nodes: nodes.length,
        nodes: nodes.slice(0, 200),
    };
}

/**
 * 底部对齐容器纵向溢出取数面（2026-10-15 接入；判据住在 instant_preview.py，本函数只取数）。
 *
 * 为什么在预览里加这一条：柱状图是"底座容器 + 竖向柱"的结构，柱高改大后柱顶会探出
 * 底座上沿——三处已交付件人工标定过该形态（容器高与柱高各调一档即修复），而既有判据
 * 没有一条管"容器内容纵向探出容器顶"：字幕安全区量内容底边越界、视觉边界量画布四边、
 * text_fit 量每行字数，三者都看不见"柱顶穿出底座"。
 *
 * 取数口径：
 *  ① 结构识别＝computed display 为 flex/inline-flex 且 align-items 为 flex-end/end 的
 *    容器（"底部对齐的 flex 容器"正是柱状图底座形态），不按类名猜；
 *  ② 溢出量＝内容盒顶边（rect.top + border-top + padding-top）− 可见后代 union 顶边；
 *    排除 position:absolute/fixed 后代（角标装饰不参与该容器的高度分配）与零面积
 *    后代（transform 缩成 0 的入场态），避免把装饰与入场过程读成溢出；
 *  ③ 时刻复用 computeSampleState（各场稳态），逐容器**跨采样取最坏（最大）读数**——
 *    与 text_fit 取最优方向相反：溢出是布局几何事实，晚场动画让柱顶探出同样算数，
 *    取最优会漏判；容器在该采样时刻不可见（链式 opacity < 0.5）则该时刻整体跳过；
 *  ④ 序号在可见性检查之前按 DOM 顺序分配，跨采样同一容器得同一 key，否则某时刻的
 *    不可见会让后续序号前移、最坏值聚合错位。
 *
 * 本函数只返回原始读数（候选容器全量回报，含未溢出者），不做四态裁定，也不写阈值——
 * 阈值由调用方（instant_preview 读 video_quality_rules.json → bar_overflow）裁定；
 * "候选 0 个"与"全体达标"是两回事，故未溢出者也必须回报。
 */
async function measureBarOverflow(page, fallbackWindows) {
    const sampling = await computeSampleState(page, fallbackWindows);
    if (sampling.timeSrc === 'none') {
        return { status_hint: 'no_time_source', samples: 0, measured_nodes: 0, nodes: [] };
    }

    const worst = {};
    for (const t of sampling.times) {
        const rows = await page.evaluate(({ time }) => {
            if (typeof tl !== 'undefined' && tl && tl.seek) {
                tl.seek(time, false);
                if (typeof gsap !== 'undefined') gsap.ticker.tick();
            }
            const out = [];
            const chainOpacity = (el) => {
                let o = 1, n = el;
                while (n && n !== document.body) {
                    const cs = getComputedStyle(n);
                    o *= parseFloat(cs.opacity || '1');
                    if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
                    n = n.parentElement;
                }
                return o;
            };
            const sceneOf = (el) => {
                let n = el;
                while (n && n !== document.body) {
                    if (n.dataset && n.dataset.sceneId) return n.dataset.sceneId;
                    n = n.parentElement;
                }
                return '?';
            };
            let idx = 0;
            for (const el of document.querySelectorAll('body *')) {
                const cs = getComputedStyle(el);
                const disp = cs.display;
                if (disp !== 'flex' && disp !== 'inline-flex') continue;
                const al = cs.alignItems;
                if (al !== 'flex-end' && al !== 'end') continue;
                // 序号先于可见性检查分配（见 doc ④），保证 key 跨采样稳定
                const label = (el.id ? '#' + el.id
                      : (String(el.className).split(' ')[0] || el.tagName)) + '#' + (idx++);
                if (chainOpacity(el) < 0.5) continue;
                const box = el.getBoundingClientRect();
                const contentTop = box.top + (parseFloat(cs.borderTopWidth) || 0)
                    + (parseFloat(cs.paddingTop) || 0);
                let unionTop = Infinity, visible = 0;
                for (const d of el.querySelectorAll('*')) {
                    const dcs = getComputedStyle(d);
                    if (dcs.position === 'absolute' || dcs.position === 'fixed') continue;
                    if (chainOpacity(d) < 0.5) continue;
                    const r = d.getBoundingClientRect();
                    if (r.width < 1 || r.height < 1) continue;
                    if (r.top < unionTop) unionTop = r.top;
                    visible++;
                }
                if (!visible) continue;                 // 无可见后代即无"探出"可言
                out.push({
                    key: sceneOf(el) + ':' + label,
                    overflow_px: +(contentTop - unionTop).toFixed(1),
                    content_top: +contentTop.toFixed(1),
                    union_top: +unionTop.toFixed(1),
                    box_h: Math.round(box.height), at: time,
                });
            }
            return out;
        }, { time: t });
        for (const row of rows) {
            if (!worst[row.key] || row.overflow_px > worst[row.key].overflow_px) worst[row.key] = row;
        }
    }

    const nodes = Object.values(worst).sort((a, b) => b.overflow_px - a.overflow_px);
    return {
        time_src: sampling.timeSrc,
        has_timeline: sampling.hasTimeline,
        timeline_duration: sampling.timelineDuration,
        samples: sampling.times.length,
        sample_times: sampling.times.slice(0, 40),
        measured_nodes: nodes.length,
        nodes: nodes.slice(0, 200),
    };
}

/**
 * 主体区填充率取数面（2026-11-11 复盘批；**描述面，不裁定**）。
 *
 * 为什么加这一条：既有几何判据（字幕安全区、text_fit、bar_overflow）全是"越界/塌陷"
 * 方向——内容越多越贴线越易判红，内容少则永远安全，压力是单向的。于是"少放、居中"
 * 成为过门最省力策略，留白在门禁全绿下出厂、以交付后投诉的形式到达。本腿把"够不够满"
 * 在渲染之前量出来，供 instant_preview.layout_fullness_lines 出读数（不设阈值）。
 *
 * 取数口径（三线 A/B 实测定稿，读数见 操作日志.md [2026-11-11] 复盘批）：
 *  ① 内容＝**可见墨迹**：有字的叶节点、img/canvas/video、svg 的可见子元素墨迹。
 *     刻意不用"卡片类名闭表"——同批 A/B 实测闭表选法与纯结构选法在三线上读数逐场相同
 *     （中位 0.640/0.495/0.411），闭表只是把项目专属类名钉进通用工具，无收益。
 *     也刻意不量"底沿空占比"：该口径在三线上是 18%/17%/21%，**无区分力**。
 *  ② svg 取可见子元素屏幕盒的并集，不取 svg 元素盒（viewBox letterbox 的空边会被
 *     算成内容），也不取整幅 getBBox（尚未揭示的子元素会被算进来）。
 *  ③ 场景根须**在画布内**（交集 ≥50%）：用画布外位移做转场的项目里 parked 场景
 *     opacity 恒 1，只按 opacity 判在场会把停在场外的整版内容算进本场。
 *  ④ 时刻复用 computeSampleState（各场稳态，含封面中点），逐场回报**全部采样**，
 *     由 Python 侧取最大值——聚合方向属口径，不属取数。
 *
 * 本函数只回几何盒与画布尺寸：主体区分母（字幕安全线）由 Python 侧单源给出，
 * 此处不写 860/1529 之类数值，也不做四态裁定。
 */
async function measureLayoutFill(page, fallbackWindows) {
    const sampling = await computeSampleState(page, fallbackWindows);
    if (sampling.timeSrc === 'none') {
        return { status_hint: 'no_time_source', samples: 0, measured_nodes: 0, nodes: [] };
    }

    const acc = {};
    for (const t of sampling.times) {
        const rows = await page.evaluate(({ time }) => {
            if (typeof tl !== 'undefined' && tl && tl.seek) {
                tl.seek(time, false);
                if (typeof gsap !== 'undefined') gsap.ticker.tick();
            }
            const CW = window.innerWidth, CH = window.innerHeight;
            const chainOpacity = (el) => {
                let o = 1, n = el;
                while (n && n !== document.body) {
                    const cs = getComputedStyle(n);
                    o *= parseFloat(cs.opacity || '1');
                    if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
                    n = n.parentElement;
                }
                return o;
            };
            const box = (el) => {
                const r = el.getBoundingClientRect();
                return { x: r.left, y: r.top, w: r.width, h: r.height };
            };
            const clip = (b, x2, y2) => {
                const x1 = Math.max(0, b.x), y1 = Math.max(0, b.y);
                const xx = Math.min(x2, b.x + b.w), yy = Math.min(y2, b.y + b.h);
                return (xx - x1 > 1 && yy - y1 > 1)
                    ? { x: x1, y: y1, w: xx - x1, h: yy - y1 } : null;
            };
            const union = (bs) => {
                if (!bs.length) return null;
                let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
                for (const b of bs) {
                    x1 = Math.min(x1, b.x); y1 = Math.min(y1, b.y);
                    x2 = Math.max(x2, b.x + b.w); y2 = Math.max(y2, b.y + b.h);
                }
                return { x: +x1.toFixed(1), y: +y1.toFixed(1),
                         w: +(x2 - x1).toFixed(1), h: +(y2 - y1).toFixed(1) };
            };
            const out = [];
            for (const root of document.querySelectorAll('[data-scene-id]')) {
                const cs = getComputedStyle(root);
                if (parseFloat(cs.opacity || '1') < 0.05 || cs.visibility === 'hidden') continue;
                const rb = box(root);
                const on = clip(rb, CW, CH);
                const frac = on ? (on.w * on.h) / (rb.w * rb.h || 1) : 0;
                if (frac < 0.5) continue;
                const bs = [];
                for (const el of root.querySelectorAll('*')) {
                    const tag = el.tagName.toLowerCase();
                    if (chainOpacity(el) < 0.05) continue;
                    if (tag === 'svg') {
                        const parts = [];
                        for (const c of el.querySelectorAll('rect,line,path,text,circle,polygon,polyline')) {
                            if (chainOpacity(c) < 0.05) continue;
                            const b = clip(box(c), CW, CH);
                            if (b) parts.push(b);
                        }
                        const u = union(parts);
                        if (u && u.w > 1) bs.push(u);
                        continue;
                    }
                    if (el.ownerSVGElement) continue;
                    const leaf = !el.querySelector('*:not(br)');
                    const txt = (el.textContent || '').trim().length > 0;
                    const media = tag === 'img' || tag === 'canvas' || tag === 'video';
                    if (!((leaf && txt) || media)) continue;
                    const b = clip(box(el), CW, CH);
                    if (b) bs.push(b);
                }
                out.push({ key: root.getAttribute('data-scene-id'),
                           canvas: { w: CW, h: CH }, ink: union(bs), at: time });
            }
            return out;
        }, { time: t });
        for (const row of rows) {
            if (!acc[row.key]) acc[row.key] = { key: row.key, canvas: row.canvas, readings: [] };
            acc[row.key].readings.push({ at: row.at, ink: row.ink });
        }
    }

    const nodes = Object.values(acc)
        .sort((a, b) => String(a.key).localeCompare(String(b.key)));
    return {
        time_src: sampling.timeSrc,
        has_timeline: sampling.hasTimeline,
        timeline_duration: sampling.timelineDuration,
        samples: sampling.times.length,
        sample_times: sampling.times.slice(0, 40),
        measured_nodes: nodes.length,
        nodes: nodes.slice(0, 200),
    };
}

/**
 * 绘制盒压在他人文字上的取数面（2026-11-22；判据住在 instant_preview.py，本函数只取数）。
 *
 * 为什么在预览里加这一条：使用者看片点名的缺陷 #3「关键词高亮后与相邻文字重叠」与
 * #4「进度线穿过正文」都是**运行时几何**问题，静态面（7j）只能问"盒的 CSS 声明是否外扩"，
 * 问不出"这个盒此刻到底盖住了谁的字"。既有三条预览判据量的是元素**自身**盒
 * （安全区／文字塌陷／柱溢出），一条都不量**两两之间**的遮挡。
 *
 * 三条窄化（都由本仓实测换来；②③的初版写法被实测推翻，写法与后果一并留在括号里）：
 *  ① **有效可见性走祖先链**：只看元素自身 opacity 会把藏在未揭示容器里的子元素算成在场
 *     （首版即因此凭空造出 3 条假相交）。
 *  ② **同 run 豁免**：绘制盒的父亲恰好是该文字所属的 run ⇒ 它是给**自己这个词**做的高亮底，
 *     属设计意图。（初版写的是"互不为祖先"，那条把真缺陷整类摘掉了：`.kw-chip` 因
 *     left/right 负外扩压到的是同一段落里**相邻词**的字，那个字的 run 是 `<p>`、芯片的
 *     父亲是 `.kw`，二者互为后代即被豁免 ⇒ 修前副本 covering 恒 0、与修后逐字相同＝空跑。）
 *  ③ **层叠由浏览器绘制栈序裁定**（`elementsFromPoint` 自上而下比索引）：底衬／卡片背景
 *     在字之下 ⇒ 栈里 run 先于绘制盒 ⇒ 不计；纯几何相交在本仓不可用（某支已交付项目
 *     248 组候选中合法底衬占绝大多数）。
 *     （初版用 `elementFromPoint` 单元素＋祖先链判"字在上层"，后果是**同一 run 内的绘制盒
 *     永远被豁免**——文字自己没有盒，命中链里必然含它那个 `<p>`，芯片压字形态结构性看不见。）
 *
 * `pointer-events:none` 的覆盖层不进命中栈（glow 类装饰层常这么写）。本函数不猜它盖没盖、
 * 也不放弃这一腿：测量期间把该元素自身临时置 `auto`、测毕在 finally 里立即还原，
 * 并在行上标 `paint_pe_none`，由 Python 侧把"这条读数是强制取到的"如实声明——
 * 直接撤掉这一腿才是把"我没测到"写成"没有"（A06 同族）。
 *
 * @param {object} opts  minAreaPx：几何地板（px²），低于此属描边级贴边，不回报。
 *                       阈值裁定不在 JS：这里只做候选枚举与命中测试。
 */
async function measureBoxOverlap(page, opts, fallbackWindows) {
    const sampling = await computeSampleState(page, fallbackWindows);
    if (sampling.timeSrc === 'none') {
        return { status_hint: 'no_time_source', samples: 0, scenes_seen: 0, pairs: [] };
    }
    const acc = {};
    let scenesSeen = 0;
    for (const t of sampling.times) {
        const rows = await page.evaluate(({ time, minArea }) => {
            if (typeof tl !== 'undefined' && tl && tl.seek) {
                tl.seek(time, false);
                if (typeof gsap !== 'undefined') gsap.ticker.tick();
            }
            const chain = (el) => {           // 祖先链（含自身），命中测试证据比对用
                const out = [];
                for (let n = el; n && n !== document.documentElement; n = n.parentElement) out.push(n);
                return out;
            };
            const stackAt = (x, y) => {
                // elementsFromPoint 返回该点**按绘制序自上而下**的完整元素栈。
                // 用栈而不是 elementFromPoint 的单个顶元素：文字自己没有盒，
                // 它所在的那个 run（文本节点的父元素）与绘制盒常常有共同祖先，
                // 单元素命中时"链里含 run"会把"芯片在字之上"洗成"字在上层"——
                // 首版即此误：真缺陷副本 covering 恒 0，修前修后读数逐字相同＝空跑。
                const f = document.elementsFromPoint || document.webkitElementsFromPoint;
                return f ? f.call(document, x, y) : null;
            };
            const effOpacity = (el) => {
                let o = 1;
                for (const n of chain(el)) {
                    const cs = getComputedStyle(n);
                    if (cs.display === 'none' || cs.visibility === 'hidden') return 0;
                    o *= parseFloat(cs.opacity || '1');
                }
                return o;
            };
            const idOf = (el) => {
                if (el.id) return '#' + el.id;
                const cls = (typeof el.className === 'string' ? el.className : '').trim().split(/\s+/).filter(Boolean);
                return el.tagName.toLowerCase() + (cls.length ? '.' + cls.slice(0, 2).join('.') : '');
            };
            const roots = [];
            for (const r of document.querySelectorAll('[data-scene-id], [id^="scene"]')) {
                if (effOpacity(r) >= 0.5) roots.push(r);
            }
            const out = [];
            for (const root of roots) {
                const scene = root.getAttribute('data-scene-id')
                    || (root.id && root.id.startsWith('scene') ? 's' + root.id.slice(5) : '?');
                const inks = [], paints = [];
                for (const el of root.querySelectorAll('*')) {
                    if (el.tagName === 'BR' || el.tagName === 'STYLE' || el.tagName === 'SCRIPT') continue;
                    const op = effOpacity(el);
                    if (op < 0.5) continue;
                    const cs = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 2 || rect.height < 2) continue;
                    // 承载可读文字：只取**直接文本节点**的实测行盒（Range），不取元素盒——
                    // 元素盒含 padding/行距，会把"没压到字、只压到行空隙"也算成压字。
                    for (const c of el.childNodes) {
                        if (c.nodeType !== 3 || !c.data || !c.data.trim()) continue;
                        const rg = document.createRange();
                        rg.selectNodeContents(c);
                        for (const r of Array.from(rg.getClientRects())) {
                            if (r.width > 6 && r.height > 6) {
                                inks.push({ owner: el, node: c, r: r,
                                            text: c.data.trim().slice(0, 24), op: op });
                            }
                        }
                    }
                    const bg = cs.backgroundColor;
                    const bgAlpha = !bg || bg === 'transparent' ? 0
                        : (bg.startsWith('rgba') ? parseFloat(bg.split(',')[3]) : 1);
                    const hasBgImg = cs.backgroundImage && cs.backgroundImage !== 'none';
                    const border = ['Top', 'Right', 'Bottom', 'Left']
                        .reduce((s, k) => s + (parseFloat(cs['border' + k + 'Width']) || 0), 0);
                    const tag = el.tagName.toLowerCase();
                    const media = ['img', 'canvas', 'video', 'svg', 'rect', 'path', 'line',
                                   'polygon', 'polyline'].includes(tag);
                    if (bgAlpha > 0.02 || hasBgImg || border > 0 || media) {
                        paints.push({
                            el: el, r: rect, name: idOf(el), op: op,
                            pe: cs.pointerEvents, fillAlpha: bgAlpha,
                        });
                    }
                }
                for (const p of paints) {
                    for (const q of inks) {
                        const run = q.node ? q.node.parentElement : q.owner;
                        if (!run || p.el === run) continue;
                        // 同一个 run 内部的绘制盒＝该词自己的高亮底，是设计意图。
                        // 判据问的是"它盖住了**别人**的字"：`.kw-chip` 盖住自己「盖章」两字合法，
                        // 因 left/right 负外扩而盖到相邻句末那个字则是缺陷（使用者点名的图三）。
                        if (p.el.parentElement === run) continue;
                        const x1 = Math.max(p.r.left, q.r.left), y1 = Math.max(p.r.top, q.r.top);
                        const x2 = Math.min(p.r.right, q.r.right), y2 = Math.min(p.r.bottom, q.r.bottom);
                        const w = x2 - x1, h = y2 - y1;
                        if (!(w > 0 && h > 0) || w * h < minArea) continue;
                        // 廉价的"这一对里有没有字真的在绘制盒下面"预筛（用整行盒，不逐字）
                        let pairAbove = 0, pairTotal = 0;
                        const nx = Math.max(2, Math.min(4, Math.round(w / 24)));
                        const ny = Math.max(2, Math.min(4, Math.round(h / 24)));
                        let stackMissing = false;
                        const compare = (px, py) => {
                            const st = stackAt(px, py);
                            if (st === null) { stackMissing = true; return 'unknown'; }
                            const ip = st.indexOf(p.el);
                            const ir = st.indexOf(run);
                            if (ip < 0) return 'below';
                            if (ir < 0) return 'unknown';
                            return ip < ir ? 'above' : 'below';
                        };
                        for (let ix = 0; ix < nx; ix++) {
                            for (let iy = 0; iy < ny; iy++) {
                                const r0 = compare(x1 + (ix + 0.5) * w / nx, y1 + (iy + 0.5) * h / ny);
                                if (r0 === 'unknown') continue;
                                pairTotal += 1;
                                if (r0 === 'above') pairAbove += 1;
                            }
                        }
                        // 逐字细化（只对预筛已判"在上"的候选跑，成本受控）：
                        // **面积类判据在本仓不可用**——真缺陷 `.kw-chip` 压相邻词的实测相交
                        // 只有 6×31＝186px²，而合法底衬与字的相交动辄上万 px²；按面积排序会把
                        // 缺陷排到噪声后面，按面积设地板（首版 200px²）则当场把这枚缺陷筛掉。
                        // 观众看到的是"几个字被盖住"，所以取数面直接量它。
                        let glyphs = 0, coveredGlyphs = 0, worstGlyphPx = 0, unknownPts = 0;
                        const hitGlyphs = [];
                        if (pairAbove > 0 && q.node) {
                            // pointer-events:none 的元素**不进命中栈**（glow 类装饰层常这么写）。
                            // 不猜它盖没盖，也不放弃这一腿：临时把它自身置 auto、测完立即还原，
                            // 并把 pe_forced 记在行上——证据面如实说明这条读数是强制取到的。
                            let forced = false;
                            if (p.pe === 'none') { p.el.style.pointerEvents = 'auto'; forced = true; }
                            try {
                                for (let i = 0; i < q.node.data.length; i++) {
                                    const chr = q.node.data[i];
                                    if (!chr || !chr.trim()) continue;
                                    const rg = document.createRange();
                                    try { rg.setStart(q.node, i); rg.setEnd(q.node, i + 1); }
                                    catch (e) { continue; }
                                    for (const gr of Array.from(rg.getClientRects())) {
                                        if (gr.width < 2 || gr.height < 2) continue;
                                        glyphs += 1;
                                        const gx1 = Math.max(p.r.left, gr.left);
                                        const gy1 = Math.max(p.r.top, gr.top);
                                        const gx2 = Math.min(p.r.right, gr.right);
                                        const gy2 = Math.min(p.r.bottom, gr.bottom);
                                        const gw = gx2 - gx1, gh = gy2 - gy1;
                                        if (!(gw > 0 && gh > 0)) continue;
                                        const area = gw * gh;
                                        // 8% 字形盒——取数口径（"这枚字有没有被碰到"），不是裁定阈值；
                                        // 裁定（几枚字算缺陷）住 config，由 instant_preview.py 判
                                        if (area < 2 || area / (gr.width * gr.height) < 0.08) continue;
                                        let top = 0, tot = 0;
                                        for (let k = 0; k < 3; k++) {
                                            const c0 = compare(gx1 + (k + 0.5) * gw / 3, (gy1 + gy2) / 2);
                                            if (c0 === 'unknown') { unknownPts += 1; continue; }
                                            tot += 1;
                                            if (c0 === 'above') top += 1;
                                        }
                                        if (tot && top / tot >= 0.5) {
                                            coveredGlyphs += 1;
                                            worstGlyphPx = Math.max(worstGlyphPx, Math.round(area));
                                            if (hitGlyphs.length < 6) {
                                                hitGlyphs.push({ i: i, ch: chr, px: Math.round(area) });
                                            }
                                        }
                                    }
                                }
                            } finally {
                                if (forced) p.el.style.pointerEvents = '';
                            }
                        }
                        out.push({
                            scene: scene, at: time, paint: p.name, ink: idOf(run),
                            text: q.text, overlap_px: Math.round(w * h),
                            samples: pairTotal, covered: pairAbove,
                            stack_missing: stackMissing, unknown_pts: unknownPts,
                            glyphs: glyphs, covered_glyphs: coveredGlyphs,
                            worst_glyph_px: worstGlyphPx, hit_glyphs: hitGlyphs,
                            paint_pe_none: p.pe === 'none',
                            paint_alpha: +(p.op * (p.fillAlpha || 1)).toFixed(3),
                        });
                    }
                }
            }
            return { rows: out, scenes: roots.length };
        }, { time: t, minArea: opts.minAreaPx });
        scenesSeen = Math.max(scenesSeen, rows.scenes || 0);
        for (const r of rows.rows) {
            const key = [r.scene, r.paint, r.ink, r.text].join('|');
            if (!acc[key]) {
                acc[key] = { ...r, marks: 0, covered_marks: 0, worst_cover_ratio: 0,
                             overlap_px_max: r.overlap_px, at_first: r.at,
                             covered_glyphs_max: 0 };
            }
            const a = acc[key];
            a.marks += 1;
            const ratio = r.samples ? r.covered / r.samples : 0;
            a.worst_cover_ratio = Math.max(a.worst_cover_ratio, ratio);
            if (ratio >= 0.5) a.covered_marks += 1;
            a.overlap_px_max = Math.max(a.overlap_px_max, r.overlap_px);
            a.paint_pe_none = a.paint_pe_none || r.paint_pe_none;
            // 被盖字形数取各采样点的**峰值**（不是求和）：同一枚字在 6 个采样点都被盖，
            // 观众看到的是 1 个字被盖，求和会把它读成 6 个。
            if ((r.covered_glyphs || 0) > (a.covered_glyphs_max || 0)) {
                a.covered_glyphs_max = r.covered_glyphs;
                a.glyphs = r.glyphs;
                a.worst_glyph_px = r.worst_glyph_px;
                a.hit_glyphs = r.hit_glyphs;
                a.at_worst = r.at;
            }
        }
    }
    const pairs = Object.values(acc).sort((x, y) =>
        ((y.covered_glyphs_max || 0) - (x.covered_glyphs_max || 0))
        || (y.overlap_px_max - x.overlap_px_max)
        || String(x.scene).localeCompare(String(y.scene)));
    return {
        time_src: sampling.timeSrc,
        // 无页面全局 tl 时 seek 是空操作——各采样点量到的是**同一个** t=0 中间态，
        // 该形态不得进入裁定（Python 侧按未测点名），否则"每条都在"是伪证据。
        has_timeline: !!sampling.hasTimeline,
        samples: sampling.times.length,
        scenes_seen: scenesSeen,
        distinct_pairs: pairs.length,
        covering_pairs: pairs.filter(p => (p.covered_glyphs_max || 0) > 0).length,
        pe_none_pairs: pairs.filter(p => p.paint_pe_none).length,
        pairs: pairs.slice(0, 300),
    };
}

async function main() {
    const args = process.argv.slice(2);
    if (args.length < 3) {
        console.error('Usage: node preview_capture.js <html_path> <config_path> <output_dir> '
            + '[--fit-min-cpl=N] [--fit-min-lines=N] [--fit-min-chars=N]');
        process.exit(1);
    }

    const htmlPath = path.resolve(args[0]);
    const configPath = path.resolve(args[1]);
    const outputDir = path.resolve(args[2]);

    // 版面塌陷取数的门槛值由调用方传入（权威源 instant_preview 读 video_quality_rules.json）。
    // 刻意不给 JS 侧默认值：缺省即"调用方忘了接线"，那种形态必须显式暴露而非静默按某个数字跑。
    const flagNumber = (prefix) => {
        for (const a of args.slice(3)) {
            if (a.startsWith(prefix)) {
                const v = parseFloat(a.slice(prefix.length));
                return Number.isFinite(v) && v > 0 ? v : null;
            }
        }
        return null;
    };
    const fitThresholds = {
        minCpl: flagNumber('--fit-min-cpl='),
        minLines: flagNumber('--fit-min-lines='),
        minChars: flagNumber('--fit-min-chars='),
    };
    // 盒相交的几何地板同样**不给 JS 侧缺省值**：缺省即"调用方忘了接线"，
    // 那种形态必须显式暴露成未测，而不是静默按某个数字跑出一张看起来完整的候选表。
    const overlapMinAreaPx = flagNumber('--overlap-min-px=');

    // 额外时刻（--extra-times=62.5,88）：绝对秒，逐枚全幅截图。
    // 刻意不判"属于哪一场"——调用方问的是"这个时刻画面对不对"，
    // 场归属由人看；工具去猜场界只会把越界时刻包装成合法读数。
    const extraTimes = (() => {
        for (const a of args.slice(3)) {
            if (a.startsWith('--extra-times=')) {
                return a.slice('--extra-times='.length)
                    .split(',')
                    .map(s => parseFloat(s.trim()))
                    .filter(v => Number.isFinite(v));
            }
        }
        return [];
    })();

    if (!fs.existsSync(htmlPath)) {
        console.error(`HTML not found: ${htmlPath}`);
        process.exit(1);
    }
    if (!fs.existsSync(configPath)) {
        console.error(`Config not found: ${configPath}`);
        process.exit(1);
    }

    fs.mkdirSync(outputDir, { recursive: true });

    // 预览视口必须等于组合画布尺寸，否则竖版项目截到 16:9 画布，
    // 安全区预检与网格图全部失真。画布权威源是组合自己的声明。
    const compositionSize = readCompositionSize(htmlPath);

    const config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
    // 兼容两种配置格式：顶层 scenes 或 timeline.scene_metadata（scene → scene_id 映射）
    let scenes = config.scenes || [];
    if (scenes.length === 0 && config.timeline && Array.isArray(config.timeline.scene_metadata)) {
        const cov = config.cover_duration || 0;
        // scene_metadata 的 start/end 已包含封面偏移，扣除后与 config.scenes 语义对齐
        scenes = config.timeline.scene_metadata
            .filter(s => s.type !== 'cover')
            .map(s => ({ scene_id: s.scene, start: s.start - cov, end: s.end - cov }));
    }
    const coverDuration = config.cover_duration || 0;

    console.log(`Preview Capture: ${scenes.length} scenes, cover=${coverDuration}s`);

    // Start local HTTP server to serve HTML (avoids file:// CORS issues)
    const htmlDir = path.dirname(htmlPath);
    const htmlFile = path.basename(htmlPath);
    const server = http.createServer((req, res) => {
        const filePath = path.join(htmlDir, decodeURIComponent(req.url).replace(/^\//, ''));
        if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
            const ext = path.extname(filePath).toLowerCase();
            const mimeTypes = {
                '.html': 'text/html; charset=utf-8',
                '.js': 'application/javascript',
                '.css': 'text/css',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.svg': 'image/svg+xml',
            };
            res.writeHead(200, { 'Content-Type': mimeTypes[ext] || 'application/octet-stream' });
            fs.createReadStream(filePath).pipe(res);
        } else {
            res.writeHead(404);
            res.end('Not found');
        }
    });

    let port = 8767;
    // 端口占用回退：上次运行残留的僵尸进程可能占着默认端口，递增重试而非直接崩溃
    await new Promise((resolve, reject) => {
        const tryListen = (p) => {
            const onError = (err) => {
                if (err.code === 'EADDRINUSE' && p < 8780) {
                    server.removeListener('error', onError);
                    tryListen(p + 1);
                } else {
                    reject(err);
                }
            };
            server.once('error', onError);
            server.listen(p, '127.0.0.1', () => {
                server.removeListener('error', onError);
                port = p;
                resolve();
            });
        };
        tryListen(port);
    });
    console.log(`HTTP server: http://127.0.0.1:${port}`);

    let browser;
    const manifest = { scenes: [], errors: [] };
    if (extraTimes.length) manifest.custom_frames = [];

    // Use D: drive for Chrome profile (avoids C: drive accumulation)
    const userDataDir = process.env.CHROME_USER_DATA_DIR || undefined;

    try {
        browser = await puppeteer.launch({
            executablePath: CHROME_PATH,
            headless: 'new',
            userDataDir: userDataDir,
            args: [
                '--no-sandbox',
                '--disable-gpu',
                '--disable-web-security',
                `--window-size=${compositionSize.width},${compositionSize.height}`,
                '--disable-extensions',
                '--disable-background-networking',
            ],
        });

        const page = await browser.newPage();
        await page.setViewport({ width: compositionSize.width, height: compositionSize.height });
        console.log(`Viewport: ${compositionSize.width}x${compositionSize.height} (composition)`);

        const url = `http://127.0.0.1:${port}/${encodeURIComponent(htmlFile)}`;
        console.log(`Loading: ${url}`);
        await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });

        // Check if GSAP is available
        const gsapOk = await page.evaluate(() => typeof gsap !== 'undefined');
        console.log(`GSAP loaded: ${gsapOk}`);

        if (!gsapOk) {
            console.log('WARNING: GSAP not available, will capture initial frame only');
        }

        // Capture screenshots for each scene
        for (let i = 0; i < scenes.length; i++) {
            const scene = scenes[i];
            // scene_id 为 0 合法（cover_duration=0 时首场景即 0 起，2026-10-16 prefab-agent-intro
            // 实测：`0 || (i+1)` 把首场景压成 1，与次场景同 id，覆盖率裁定误报"缺: [0]"）。
            // 只兜底 undefined/null，不得用 || —— 0 会被 || 吞掉。
            const sceneId = (scene.scene_id === undefined || scene.scene_id === null)
                ? (i + 1)
                : scene.scene_id;
            const sceneStart = parseFloat(scene.start) + coverDuration;
            const sceneEnd = parseFloat(scene.end) + coverDuration;
            const sceneDur = sceneEnd - sceneStart;

            // Frame 1: shortly after scene start (entrance complete ~3s in)
            const t1 = Math.min(sceneStart + 3.0, sceneEnd - 1.0);
            // Frame 2: near scene end (before transition)
            const t2 = Math.max(sceneEnd - 2.0, t1 + 1.0);

            console.log(`  Scene ${sceneId}: @${t1.toFixed(1)}s and @${t2.toFixed(1)}s (dur=${sceneDur.toFixed(1)}s)`);

            try {
                // Seek GSAP timeline
                if (gsapOk) {
                    await page.evaluate((time) => {
                        if (typeof tl !== 'undefined' && tl.seek) {
                            tl.seek(time, false);
                            gsap.ticker.tick();
                        }
                    }, t1);
                    await new Promise(r => setTimeout(r, 200));
                }

                const path1 = path.join(outputDir, `scene_${sceneId}_start.png`);
                await page.screenshot({ path: path1, type: 'png' });
                manifest.scenes.push({ sceneId, frame: 'start', time: t1, file: `scene_${sceneId}_start.png` });

                // Frame 2 (only if scene is long enough)
                if (sceneDur > 8 && gsapOk) {
                    await page.evaluate((time) => {
                        if (typeof tl !== 'undefined' && tl.seek) {
                            tl.seek(time, false);
                            gsap.ticker.tick();
                        }
                    }, t2);
                    await new Promise(r => setTimeout(r, 200));

                    const path2 = path.join(outputDir, `scene_${sceneId}_end.png`);
                    await page.screenshot({ path: path2, type: 'png' });
                    manifest.scenes.push({ sceneId, frame: 'end', time: t2, file: `scene_${sceneId}_end.png` });
                }
            } catch (err) {
                console.error(`  Scene ${sceneId} error: ${err.message}`);
                manifest.errors.push({ sceneId, error: err.message });
            }
        }

        // ── 额外时刻全幅截图（--extra-times）：场首/场尾两点盖不到的版式核对 ──
        // 截图尺寸＝视口＝组合画布（见上方 setViewport），不另设缩放参数：
        // 缩略图判断不出"浮空箭头""批注压安全线"这类缺陷，全幅才有判断力。
        for (const t of extraTimes) {
            try {
                if (gsapOk) {
                    await page.evaluate((time) => {
                        if (typeof tl !== 'undefined' && tl.seek) {
                            tl.seek(time, false);
                            gsap.ticker.tick();
                        }
                    }, t);
                    await new Promise(r => setTimeout(r, 200));
                }
                const file = `frame_t${t}.png`;
                await page.screenshot({ path: path.join(outputDir, file), type: 'png' });
                manifest.custom_frames.push({
                    time: t, file,
                    width: compositionSize.width, height: compositionSize.height,
                });
                console.log(`  Extra frame @${t}s -> ${file} `
                    + `(${compositionSize.width}x${compositionSize.height})`);
            } catch (err) {
                console.error(`  Extra frame @${t}s error: ${err.message}`);
                manifest.errors.push({ sceneId: null, time: t, error: err.message });
            }
        }

        // ── 版面塌陷（每行字数）取数：只取数，裁定归 instant_preview.py ──
        // 放在截图循环之后：塌陷判据要的是"各场稳态"，与截图共用同一个已加载页面，
        // 不另开第二次 goto（新开一次即多一次 30s 网络等待，且两份页面状态会分叉）。
        const fitWindows = scenes.map(s => ({
            start: parseFloat(s.start) + coverDuration,
            end: parseFloat(s.end) + coverDuration,
        }));
        const fitMissing = [['minCpl', '--fit-min-cpl'], ['minLines', '--fit-min-lines'],
                            ['minChars', '--fit-min-chars']]
            .filter(([k]) => fitThresholds[k] === null).map(([k, f]) => `${k}(${f})`);
        if (fitMissing.length) {
            manifest.text_fit = { status_hint: 'thresholds_not_passed', missing: fitMissing,
                                  samples: 0, measured_nodes: 0, nodes: [] };
            console.log(`  [TEXT-FIT] 取数未跑：调用方未传阈值 ${fitMissing.join(', ')}`);
        } else {
            const fitStarted = Date.now();
            try {
                const fit = await measureTextFit(
                    page,
                    { minLines: fitThresholds.minLines, minChars: fitThresholds.minChars },
                    fitWindows);
                fit.min_chars_per_line = fitThresholds.minCpl;
                manifest.text_fit = fit;
                console.log(`  [TEXT-FIT] 时刻源=${fit.time_src || 'none'} `
                    + `采样 ${fit.samples} 次，候选节点 ${fit.measured_nodes} 个`
                    + `（${((Date.now() - fitStarted) / 1000).toFixed(1)}s）`);
            } catch (e) {
                manifest.text_fit = { status_hint: 'measure_failed',
                                      error: String((e && e.message) || e),
                                      samples: 0, measured_nodes: 0, nodes: [] };
                console.error(`  [TEXT-FIT] 取数异常: ${(e && e.message) || e}`);
            }
        }

        // ── 底部对齐容器纵向溢出取数：只取数，裁定归 instant_preview.py ──
        // 不传阈值：本判据的阈值只在 Python 裁定面消费；JS 全量回报候选容器（含未溢出者），
        // 与 text_fit 的"调用方必须传阈值"刻意不同级——那里的阈值决定取数对象的入选门槛，
        // 这里的结构识别（flex-end 容器）无阈值参数，候选表本身就是裁定面的证据面。
        const barStarted = Date.now();
        try {
            const bar = await measureBarOverflow(page, fitWindows);
            manifest.bar_overflow = bar;
            console.log(`  [BAR-OVERFLOW] 时刻源=${bar.time_src || 'none'} `
                + `采样 ${bar.samples} 次，候选容器 ${bar.measured_nodes} 个`
                + `（${((Date.now() - barStarted) / 1000).toFixed(1)}s）`);
        } catch (e) {
            manifest.bar_overflow = { status_hint: 'measure_failed',
                                      error: String((e && e.message) || e),
                                      samples: 0, measured_nodes: 0, nodes: [] };
            console.error(`  [BAR-OVERFLOW] 取数异常: ${(e && e.message) || e}`);
        }

        // ── 主体区填充率取数：只取数，分母与读数归 instant_preview.py ──
        // 不传阈值：本面是描述面（2026-10-10 常设裁定：不为观感新增门禁），
        // 全量回报逐场几何盒，安全线分母由 Python 侧单源推导。
        const fillStarted = Date.now();
        try {
            const fill = await measureLayoutFill(page, fitWindows);
            manifest.layout_fill = fill;
            console.log(`  [LAYOUT-FILL] 时刻源=${fill.time_src || 'none'} `
                + `采样 ${fill.samples} 次，在场场景 ${fill.measured_nodes} 个`
                + `（${((Date.now() - fillStarted) / 1000).toFixed(1)}s）`);
        } catch (e) {
            manifest.layout_fill = { status_hint: 'measure_failed',
                                     error: String((e && e.message) || e),
                                     samples: 0, measured_nodes: 0, nodes: [] };
            console.error(`  [LAYOUT-FILL] 取数异常: ${(e && e.message) || e}`);
        }

        // ── 绘制盒压他人文字的取数：只取数，裁定与阈值归 instant_preview.py ──
        // 传几何地板而不传裁定阈值：minAreaPx 决定"哪些相交根本不进候选表"（取数面入选门槛，
        // 与 text_fit 同形），而"相交给到多少算压字"由 Python 按 config 裁定（与 bar_overflow 同形）。
        const ovlStarted = Date.now();
        if (overlapMinAreaPx === null) {
            manifest.box_overlap = { status_hint: 'threshold_not_passed',
                                     missing: ['minAreaPx(--overlap-min-px)'],
                                     samples: 0, scenes_seen: 0, distinct_pairs: 0, pairs: [] };
            console.log('  [BOX-OVERLAP] 取数未跑：调用方未传几何地板阈值');
        } else {
            try {
                const ovl = await measureBoxOverlap(page, { minAreaPx: overlapMinAreaPx }, fitWindows);
                manifest.box_overlap = ovl;
                console.log(`  [BOX-OVERLAP] 时刻源=${ovl.time_src || 'none'} `
                    + `采样 ${ovl.samples} 次，在场场景 ${ovl.scenes_seen} 个，`
                    + `候选对 ${ovl.distinct_pairs} 组`
                    + `（${((Date.now() - ovlStarted) / 1000).toFixed(1)}s）`);
            } catch (e) {
                manifest.box_overlap = { status_hint: 'measure_failed',
                                         error: String((e && e.message) || e),
                                         samples: 0, scenes_seen: 0, distinct_pairs: 0, pairs: [] };
                console.error(`  [BOX-OVERLAP] 取数异常: ${(e && e.message) || e}`);
            }
        }

        // Write manifest
        const manifestPath = path.join(outputDir, 'capture_manifest.json');
        fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
        console.log(`\nManifest: ${manifestPath}`);
        console.log(`Captured: ${manifest.scenes.length} screenshots`);

        // 产物已落盘，直接强制退出——browser.close() 实测会永久挂起，不得 await
        // （历史症状：manifest 写完后进程挂死，上游 120s 超时把完整输出一并丢弃）
        process.exit(0);

    } catch (err) {
        console.error(`Fatal error: ${err.message}`);
        process.exit(1);
    } finally {
        if (browser) await browser.close();
        server.close();
    }
}

// 取数函数对外导出：普查/探针经 require 复用同一份实现，禁止拷贝第二份取数面。
// main 仅在直接执行时自启（被 require 时不自跑浏览器）。
if (require.main === module) {
    main().catch(err => {
        console.error(err);
        process.exit(1);
    });
}

module.exports = {
    measureTextFit, measureBarOverflow, measureLayoutFill, measureBoxOverlap,
    computeSampleState,
};

