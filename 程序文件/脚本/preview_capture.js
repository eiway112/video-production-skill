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
                if (s.cover) continue;
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
            const sceneId = scene.scene_id || (i + 1);
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

module.exports = { measureTextFit, measureBarOverflow, computeSampleState };

