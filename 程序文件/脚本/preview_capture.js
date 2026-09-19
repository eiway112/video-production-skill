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

async function main() {
    const args = process.argv.slice(2);
    if (args.length < 3) {
        console.error('Usage: node preview_capture.js <html_path> <config_path> <output_dir>');
        process.exit(1);
    }

    const htmlPath = path.resolve(args[0]);
    const configPath = path.resolve(args[1]);
    const outputDir = path.resolve(args[2]);

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

main().catch(err => {
    console.error(err);
    process.exit(1);
});
