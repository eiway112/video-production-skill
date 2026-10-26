# HyperFrames HTML 模板参考基准

> 从 workflow-promo（700行）、workflow-summary（522行）、file-governance（568行）三个成功项目中提取的完整模式规范。
> AI 导演模式下手写 HTML 时，本文档是唯一参照标准。

---

## 一、HTML 骨架结构

### 1.1 根元素
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>/* CSS */</style>
</head>
<body>
<div id="root" data-composition-id="main" data-width="1920" data-height="1080" 
     data-start="0" data-duration="{TOTAL}" data-cover-duration="3">
  <!-- 场景 -->
</div>
<script>/* GSAP */</script>
</body>
</html>
```

### 1.2 场景容器
```html
<!-- 封面场景 scene0：z-index:999，确保 t=0 时顶层可见 -->
<div id="scene0" class="scene" style="z-index:999;">
  <!-- 装饰层 -->
  <div class="sc" style="align-items:center;justify-content:center;text-align:center;padding-bottom:150px;">
    <!-- 封面内容 -->
  </div>
  <div class="subtitle-safe"></div>
</div>

<!-- 内容场景 sceneN：z-index 递增 -->
<div id="sceneN" class="scene">
  <!-- 装饰层（glow/ghost/gline） -->
  <div class="sc" style="gap:32px;">
    <!-- 场景内容 -->
  </div>
  <div class="subtitle-safe"></div>
</div>
```

**关键规则**：
- 每个场景必须包含 `<div class="subtitle-safe"></div>`
- scene0 的 z-index:999，后续场景从 1 开始递增
- `.sc` 是内容容器，所有场景内元素都在 `.sc` 内部
- `data-cover-duration="3"` 表示封面持续 3 秒

---

## 二、CSS 规范

### 2.1 基础样式（必须项）
```css
* { margin:0; padding:0; box-sizing:border-box; }
body { width:1920px; height:1080px; overflow:hidden; 
       background:#0f172a; font-family:"Noto Sans SC","Montserrat",sans-serif; color:#e2e8f0; }
.scene { position:absolute; top:0; left:0; width:1920px; height:1080px; overflow:hidden; }

/* scene0 直接可见，其余初始 opacity:0 */
#scene1 { z-index:1; background:#0f172a; opacity:0; }
#scene2 { z-index:2; background:#0f172a; opacity:0; }
/* ... 以此类推 */

/* 内容容器 */
.sc { display:flex; flex-direction:column; justify-content:flex-start;
      width:100%; height:100%; padding:50px 100px 160px; position:relative; }

/* 字幕安全区 */
.subtitle-safe { position:absolute; bottom:0; left:0; width:100%;
                 height:140px; z-index:100; pointer-events:none;
                 background:linear-gradient(to bottom,
                   rgba(15,23,42,0) 0%,
                   rgba(15,23,42,0.85) 40%,
                   rgba(15,23,42,0.95) 100%); }
```

### 2.2 参数基准值（从成功项目提取）

| 参数 | 推荐值 | 允许范围 | 说明 |
|------|:---:|:---:|------|
| subtitle-safe height | 130-140px | 120-150px | 深色主题；浅色主题 120-140px |
| .sc padding-bottom | 150-160px | 150-170px | ≥ safe-height + 20px |
| .sc padding-top | 50-80px | 50-80px | 封面 50px，内容 60-80px |
| .sc padding-x | 100-120px | 80-120px | 信息密集取大值 |
| .sc gap | 28-40px | 24-44px | 元素间距 |
| 内容 max-width | 1400px | 1200-1760px | 卡片网格可达 1760px |

### 2.3 组件样式库

**装饰层**：
```css
.glow { position:absolute; border-radius:50%; pointer-events:none; filter:blur(120px); }
.ghost { position:absolute; font-size:200px; font-weight:900; opacity:0.04; 
         pointer-events:none; letter-spacing:-0.05em; }
.gline { position:absolute; height:2px; pointer-events:none;
         background:linear-gradient(90deg,rgba(accent,0),accent,rgba(accent,0)); }
```

**信息标签**：
```css
.badge { display:inline-block; padding:8px 20px; border-radius:999px; 
         font-size:20px; font-weight:700; }
.badge-blue { background:rgba(96,165,250,0.2); color:#60a5fa; }
.badge-amber { background:rgba(245,158,11,0.2); color:#f59e0b; }
.badge-green { background:rgba(52,211,153,0.2); color:#34d399; }
.badge-red { background:rgba(248,113,113,0.2); color:#f87171; }
```

**卡片**：
```css
.card { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);
        border-radius:16px; padding:28px 32px; }
.pain-card { background:rgba(248,113,113,0.06); border:2px solid rgba(248,113,113,0.2);
             border-radius:16px; padding:24px 28px; }
.tool-card { background:rgba(96,165,250,0.06); border:2px solid rgba(96,165,250,0.2);
             border-radius:16px; padding:28px 32px; }
```

**统计数据**：
```css
.stat-num { font-size:56px; font-weight:900; line-height:1; }
.stat-label { font-size:18px; color:#94a3b8; margin-top:8px; }
```

### 2.4 字号分层

| 层级 | 字号 | 权重 | 用途 |
|------|:---:|:---:|------|
| 封面主标题 | 96-108px | 900 | 仅 scene0 |
| 场景标题 | 48-82px | 800-900 | 每个场景的主标题 |
| 副标题 | 28-36px | 600-700 | 场景描述/总结句 |
| 卡片标题 | 20-28px | 700-800 | 卡片内标题 |
| 卡片正文 | 16-22px | 400 | 卡片内描述文本 |
| 标签/注释 | 18-22px | 600-700 | badge、mono tag |
| Mono标签 | 20px | 400 | 英文大写标签（PAIN POINTS 等） |

### 2.5 色彩系统

**背景**：
- 主色：`#0f172a`（slate-900）或 `#0d1117`（GitHub dark）
- 场景内文字：`#e2e8f0`（slate-200）
- 次要文字：`#94a3b8`（slate-400）

**强调色**（四色系，所有项目共用）：
```css
.accent { color:#f59e0b; }   /* amber — 核心关键词 */
.blue   { color:#60a5fa; }   /* blue — 技术/架构 */
.green  { color:#34d399; }   /* green — 成果/正面 */
.red    { color:#f87171; }   /* red — 痛点/问题 */
```

**装饰层透明度**：
- 主光晕 glow：0.08-0.15（如 `rgba(96,165,250,0.12)`）
- 辅光晕 glow：0.06-0.10
- Ghost 大字：0.03-0.05
- gline 线条：动画后 opacity 0.3

---

## 三、GSAP 时间轴规范

### 3.0 时间轴唯一权威源：T-block（先读本节再写任何时间位）

场景起点一律声明在 T-block 里，动画时间位一律写成对它的引用：

```javascript
var T = { s1: 3.0, s2: 25.3, s3: 46.4 };   // 场景起点，adjust_timeline 唯一改写对象
tl.fromTo("#s2-title", {...}, {...}, T.s2 + 0.8);   // ✅ 引用
tl.fromTo("#s2-title", {...}, {...}, 26.1);         // ❌ 绝对数字
```

**为什么必须是引用**：`adjust_timeline.py` 在存在 T-block 时只改写 `var T = {...};` 这一个块（`adjust_t_block`，`adjust_timeline.py:679-684`），并**短路**逐行正则兜底路径。写成绝对数字的时间位因此不随场景窗口位移，与引用系分叉。

真实代价（2026-09-26 实测）：scene2 转场写成字面量 `28.0`，而 T-block 声明 `s2: 25.3`——转场比场景边界晚 2.7s，预览在该窗口采样时画面仍停在转场中途（内容被推到 y1079），`instant_preview` 连续两轮报安全区溢出 FAIL，而根因不在布局、在时间轴。这类失败在预览报告上长得像排版问题，按排版去改会白改两轮。

**约束**：
- 本节示例中的 `T` 是符号，须落地为 `T.sN`（如 `T+0.5` → `T.s3+0.5`），**不得手工代入成具体秒数**。
- 唯一允许的字面量时间位是**封面窗口 0–3s**（§3.2）：封面不入 T-block、时长由 `data-cover-duration` 固定，现役承载者是 `preflight_check.py:703`（读该声明参与预检）与 `scene_patch_render.py:242`（head 段时间见证：声明值须等于首个 T 起点，容差 0.05s，不吻合即点名退回全量）。**原写的 `html_template_validator.check_cover_duration` 已于 2026-10-16 退役**——该件唯一调用者是零引用的 `quality_gates_runner.py`，且其安全区判据在用户验收通过的项目上判 FAIL（推荐带 150-170px vs 实际 240px），接链即造第二个安全区口径；裁定与读数见 `操作日志.md` [2026-10-16] 机制 条。
- 自查判据：一个场景 div 的入场/转场/装饰语句里出现裸数字，且该数字 ≥ 首个内容场景起点，即为待改项。

### 3.1 时间轴初始化
```javascript
window.__timelines = window.__timelines || {};
var tl = gsap.timeline({ paused: true });

// ... 所有动画添加到 tl ...

window.__timelines["main"] = tl;
```

**关键约束**：
- 必须 `paused:true`，由 HyperFrames seek-and-capture 引擎驱动
- 所有 tween 必须在同一个 `tl` 上，禁止创建独立 timeline
- 禁止使用 `setInterval` 或 `requestAnimationFrame`
- **场景内容动画起始时间一律取 `T.sN`（见 §3.0），不得代入具体秒数**；封面占 0–3s，`tl.set("body", {}, 3)` 后主线才开始

### 3.2 封面淡出
```javascript
// 封面在 2.5s 开始淡出，3.0s 完全隐藏
tl.to("#scene0", { opacity:0, duration:0.5, ease:"power2.inOut" }, 2.5);
tl.set("#scene0", { visibility:"hidden" }, 3.0);

// 全局偏移：主线从 3s 开始
tl.set("body", {}, 3);
```

### 3.3 场景转场（两种交替使用）

**水平推移（Push Slide）**：
```javascript
// 场景 N → N+1，水平推移
tl.to("#sceneN", { x:-1920, duration:0.8, ease:"power3.inOut" }, T);
tl.fromTo("#sceneN+1", { x:1920, opacity:1 }, { x:0, duration:0.8, ease:"power3.inOut" }, T);
tl.set("#sceneN", { visibility:"hidden" }, T + 1.0);
```

**垂直推移（Vertical Push）**：
```javascript
// 场景 N → N+1，垂直推移
tl.to("#sceneN", { y:-1080, duration:1.0, ease:"power2.inOut" }, T);
tl.fromTo("#sceneN+1", { y:1080, opacity:1 }, { y:0, duration:1.0, ease:"power2.inOut" }, T);
tl.set("#sceneN", { visibility:"hidden" }, T + 1.2);
```

**转场规范**：
- 两种转场交替使用（水平→垂直→水平→...）
- 转场时长 0.8-1.0s
- 转场结束后（+0.2s buffer）设置 visibility:hidden
- 转场时间计入场景窗口（如场景窗口 0-10s，转场在 10s 开始）

### 3.4 内容入场动画

**标题入场**（从 cover 3s 后开始）：
```javascript
// 英文标签
tl.fromTo("#sN-tag", { y:20, opacity:0 }, { y:0, opacity:1, duration:0.6, ease:"power3.out" }, T+0.5);
// 主标题
tl.fromTo("#sN-title", { y:50, opacity:0 }, { y:0, opacity:1, duration:0.9, ease:"power2.out" }, T+0.8);
```

**卡片错落入场**（stagger ~2-3s）：
```javascript
(function(){
  var cards = document.querySelectorAll("#sN-cards .pain-card");
  var starts = [T+2.5, T+5.0, T+7.5]; // 每个卡片间隔 2.5s
  cards.forEach(function(el, i) {
    tl.fromTo(el, { y:40, opacity:0, scale:0.92 }, 
              { y:0, opacity:1, scale:1, duration:0.8, ease:"back.out(1.2)" }, starts[i]);
  });
})();
```

### 3.5 装饰层持续动画

```javascript
// 光晕呼吸（yoyo，整个场景持续）
tl.to("#sNg1", { scale:1.15, duration:5, ease:"sine.inOut", yoyo:true, repeat:-1 }, T);
tl.to("#sNg2", { scale:1.2, x:30, duration:4, ease:"sine.inOut", yoyo:true, repeat:-1 }, T+0.5);

// Ghost 文字缓动
tl.to("#sNgh", { x:-20, opacity:0.05, duration:8, ease:"none" }, T);

// gline 线条展开
tl.fromTo("#sNl1", { scaleX:0, opacity:0 }, { scaleX:1, opacity:0.3, duration:1.0, ease:"power2.out" }, T+0.5);
```

**装饰层时间填充规则**：
- 每个场景至少有 2 个 glow 持续动画（覆盖整个窗口）
- glow 的 `repeat:-1`（无限循环），确保长场景不会静止
- glow 的 scale 变化 1.10-1.20，配合 yoyo
- ghost 文字缓慢漂移
- 这些动画填充入场完成后的等待时间，避免画面静止

### 3.6 词级 cue 锚点（可选 — 说到哪个词，画面哪个才亮）

**它解决什么**：`T.sN + 固定秒数` 锚的是"这场开场后第几秒"，与旁白说到哪儿无关；语速一变、句间停顿一插，错落入场就会跑到词前面（或落到词说完之后）。词级 cue 把时间位的锚点从"我猜的秒数"换成"音频里那个词的实际起口"。

**作者侧只有一种写法**——把属性挂在要点亮的元素上，词须**逐字**出现在该场旁白里：

```html
<div id="scene3" class="scene" data-scene-id="s3" data-scene-entry="gsap" data-scene-subtitle-safe="true">
  <div class="card" data-cue-at="耐火极限">…</div>
  <div class="card" data-cue-at="双空腔">…</div>
</div>
```

**不要在 HTML 里写时刻**。定界块由 timeline 步按音频实测**整块再生成**，落在构造 timeline 的 `<script>` 体内（幂等：每次从实测值重写，不做算术叠加，所以改语速、重跑 timeline 都不会让时刻漂移）：

```javascript
/* CUE-MAP-START — 由 timeline 步按音频实测再生成，勿手改 */
var C = { "s3:耐火极限": 12.486, "s3:双空腔": 18.02 };
/* CUE-MAP-END */
```

引用时与既有口径拼接。`C` 的值是**场内相对秒**（与 `sentences[].rel_start` 同一基准），`T.sN` 是绝对秒，两者相加才落在正确位置——这与 §3.0 的"时间位全部写成 `T.sN + offset`"是同一条规则，不是例外：

```javascript
tl.fromTo("#s3-card-a", {y:20, opacity:0}, {y:0, opacity:1, duration:0.5}, T.s3 + C["s3:耐火极限"]);
```

**四条边界**（前两条 preflight 判 FAIL 并阻断渲染，后两条自动降级、只点名）：

1. 词不在该场旁白里 → FAIL，归因写"这条 cue 永远不会亮"。归一化口径＝只留发音字符并小写（与对齐基准同一套），所以旁白写"3 大支柱"时声明"三大支柱"对不上。
2. 属性必须落在带 `data-scene-id` 的场景 div 内，否则无从归属 → FAIL；封面场（scene0）没有旁白，不可声明。
3. 同一词在该场出现多次 → 取第一次出现并 WARN。要消歧就写更长的短语，**没有序号语法**。
4. 该项目的时间记录早于字符级数据落地（旧记录没有 `chars` 节）→ 该条自动降级为**句级锚点**并点名，属设计内状态：宁可让元素早亮，也不让它亮在插值造出的虚构时刻上。拿到词级时刻的前置是该项目的 tts 步自然重跑一次。

**降级还有第二种触发，读法不同（2026-10-26 实测登记）**：记录**有** `chars`，但锚词覆盖的字符里含 `matched=0` 的插值时刻——`cue_resolve` 的 `matched_ratio < 1`、reason 点名索引（形如「覆盖字符含 1 枚插值时刻（索引 [42]）」），`anchor` 落 `sentence`。这不是记录缺失，是**选词失败**：按索引把短语挪到整段都落在实测字符上的位置，实测同一场（`prefab-agent-openatom`）：「计算与校验层」0.833／「部品层」0.667／「独立复核」0.75 **三条同时塌到 8.06s**，换成子串「计算与」「独立复」「参数先」后 ratio 全 1.0，分别得 10.6／13.8／23.14s。**这三条降级在命令输出里 `状态=PASS`、FAIL 计数 0**——不会有人替你报错，判据只能看 `anchor=char`；成片上的表现是"全图同时点亮"，也没有任何门禁会拦。本条属"自动降级并点名"，不阻断渲染，所以**不能等门禁纠错**。

**渲染前自查**（秒级，只读已落盘的时间记录，不起 TTS、不起渲染；需 tts 步已跑过）：

```
python 程序文件/脚本/cue_resolve.py --manifest 过程产物/临时产物/{项目}_audio/_timeline_manifest.json --scene 3 --cue 耐火极限
```

**花钱前的落点预检**（零 ASR、零 TTS、零渲染，只吃 `narration.json` 文本；上面那条自查要等 tts 步跑完，本条在写旁白阶段就能用）：

```
python 程序文件/脚本/cue_resolve.py --narration 程序文件/源码/hyperframes/{项目}/narration.json --html 程序文件/源码/hyperframes/{项目}/index.html
```

**为什么需要单独一条命令**：词级锚点相对句级锚点的提前量，边界是**字幕分段**而不是句子——`C` 里存的时刻与该词所属**分段**的起口同源（`enhance_video_audio._split_text_to_segments`，`group_limit=40`，先按 。？！； 切句、贪心并段、超 40 字再按逗号二次切）。所以词落在段首时，**挂 cue 与不挂是同一个时刻**。实测形态（`agent-decision-map` 首轮，2026-10-12）：按"句子"直觉选词，27 条里 3 条段内偏移＝0、6 条预估差值 <1.6s，而那 9 条在"能不能定位"这一面全部判可定位——即契约面管不住"值不值得挂"。

预检逐条给出：段号/段长/段内偏移/出现次数，判定分四档——

| 判定 | 含义 | 处置 |
|---|---|---|
| `PASS` | 可定位且偏移达阈（≥ 6 字 ≈ 1.5s） | 保留 |
| `PASS` + 警告"段内偏移仅 N 字" | 可定位但差值不可观测 | 把词移到该段后部，或换更长的短语 |
| `NOT_APPLICABLE`（段首零收益） | 词级时刻＝句级时刻，挂等于没挂 | 同上；这不是违规，别当错误修 |
| `FAIL` / `UNTESTED` | 词不在旁白／该场无旁白／分段口径取不回 | 改文本或修记录；`FAIL` 退出码 1、`UNTESTED` 为 2 |

**本命令的输出不是解析归因**：它给的是**字数偏移**，刻意不换算成秒——秒要等真实波形对齐后由上面 `--manifest` 那条读，换算即虚构。

**现状（2026-10-12 更新，覆盖此前的"从未使用"）**：本仓第一个正式使用 `data-cue-at` 的真实业务交付已落地（`agent-decision-map`，13 个内容场景共 27 条，词级与句级实测差值 1.82–7.38s，成片抽帧 27/27 确认构件起亮落在词上，取证见 `过程产物/临时产物/_取证/20261012/`）。**发布仓与使用者副本仍无带 cue 的项目**，不得读成"消费端已在用"。取舍照旧：**不要给每个元素都挂 cue**，只挂在"亮早了会被观众看出来"的构件上（结论卡、强调数字、对比项），标题与装饰层用固定秒数往往更稳。

#### 3.6.1 点亮形态：说到即亮、亮住不灭（2026-10-17 修订实证，用户反馈的原话形态是"音频到词级点了，但词级却没有给予反馈"）

**这一节管的不是时刻算得对不对，而是亮的那一下观众看不看得见。** 上述反馈发生在时刻完全正确的一批 cue 上：解析、CUE-MAP、落点全部合格，病灶在动画写法——点亮写成亚秒级脉冲（`duration:0.38, yoyo:true, repeat:1` + `clearProps` 收尾），全程 0.76s 即回到原状；浅色系底上蓝框光晕的对比本就弱，一闪即灭读作"没有反应"。**脉冲形态禁止用于 cue 点亮**（它在深色底、饱和强调色的项目上侥幸可用，随主题翻转即失效，属"缺省值决定观感"那一类，不是判据）。

判据＝**亮住不灭，下一锚点亮时前一锚让位**。三段式：过冲 `0.22s`（`power2.out`）→ 收敛到保持态 `0.34s`（`power1.inOut`）→ 让位时 `0.55s` 淡出（`power2.inOut`）。三条配套约束，缺一即出现"该亮的没亮／该灭的不灭"：

1. **句级降级锚必须晚于元素自身入场完成。** `C` 值可能是该场句首时刻（实测形态：s3 首句 147 字符里仅 85 个有实测对齐，降级后 `C["s3:入口路由"] = 0.0`）。此时 `T.sN + C[...]` 落在卡片入场动画之前，观众看到的是一张"本来就亮着"的卡——cue 等于没挂。修法给元素传入场完成时刻（本文档记作 `enterAt`），点亮时刻取 `max(cue_at, enterAt)`。**不放宽降级判据**：降级那条"宁可早亮，不亮在虚构时刻上"是设计内取舍，`enterAt` 只把它接回正确时刻。
2. **成组同时点亮的锚，熄灭对象须向前扫描。** 默认取"排序后的下一条"时，同刻组内互取 → `off ≤ on + 0.62` → 淡出分支永不成立 → 整组不灭。改为向前找第一条 `at > onAt + 0.62` 的锚。
3. **向前扫描必须封顶**（经验值 `onAt + 8s`）。不封顶会取到下一场的锚，本场元素跨场长亮；观众语境已切换，长亮即噪声。

可复制实现（一次收集、统一排序后挂到主时间轴 `tl` 上，与 §3.0 的"全部挂 `tl`"同规）：

```javascript
window.__cuePlan = [];
function cueGlow(sel, key, base, opts){
  if (!window.C || typeof C[key] !== "number") return;
  var el = document.querySelector(sel); if (!el) return;
  window.__cuePlan.push({ el: el, at: base + C[key],
    enterAt: (opts && typeof opts.enterAt === "number") ? opts.enterAt : 0,
    offAt:   (opts && typeof opts.offAt   === "number") ? opts.offAt   : null });
}
function flushCuePlan(){                        // 在构造时间轴的 script 末尾调用一次
  var plan = window.__cuePlan.slice().sort(function(a,b){ return a.at - b.at; });
  for (var i = 0; i < plan.length; i++){
    var cur = plan[i], cs = getComputedStyle(cur.el);
    var bs = cs.boxShadow; if (!bs || bs === "none") bs = "0px 0px 0px rgba(0,0,0,0)";
    var bg = cs.backgroundColor || "rgba(0,0,0,0)";
    /* 深色底卡片（白字）不走背景腿，只动边框与光晕——否则点亮把字糊掉 */
    var doBg = /rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0\s*\)/.test(bg) ||
               (function(m){ return m && m.length >= 3 && (0.299*m[0]+0.587*m[1]+0.114*m[2]) > 150; })(bg.match(/\d+/g));
    var on = { boxShadow:"0px 16px 40px rgba(37,99,235,0.45)", borderColor:"#2563eb" };
    var off = { boxShadow:bs, borderColor:(cs.borderColor || "rgba(0,0,0,0)") };
    if (doBg){ on.backgroundColor = "#eef5ff"; off.backgroundColor = bg; }
    var onAt = Math.max(cur.at, cur.enterAt), offAt = cur.offAt;
    if (offAt === null){
      offAt = onAt + 8;                          // 约束三：封顶
      for (var j = i + 1; j < plan.length; j++){ // 约束二：向前扫描
        if (plan[j].at > onAt + 0.62){ offAt = Math.min(plan[j].at, onAt + 8); break; }
      }
    }
    tl.fromTo(cur.el, off, Object.assign({}, on, { duration:0.22, ease:"power2.out" }), onAt);
    tl.to(cur.el, Object.assign({}, on, { boxShadow:"0px 8px 24px rgba(37,99,235,0.26)", duration:0.34, ease:"power1.inOut" }), onAt + 0.22);
    if (offAt > onAt + 0.62) tl.to(cur.el, Object.assign({}, off, { duration:0.55, ease:"power2.inOut" }), offAt);
  }
}
```

**两条取证事实（写文档时容易猜错，均为实测）**：

- **cue 落点能在预览上验，不必付一次全量渲染。** `CUE-MAP` 块由 timeline 步写进 `index.html` 后**常驻该文件**，因此预览期 `window.C` 存在、cue 动画在 `instant_preview.py` 的截图里真实执行。本轮正是从预览截图读出了"同一场里 l2/l3 亮、l1/l4 灭"这一不一致，才定位到约束 1 与 2。前提：本轮新增/改动的声明要先跑过一次 timeline 步（块是它写的），否则预览期 `window.C` 不存在、`cueGlow` 全部跳过——**"预览里 cue 没亮"有两种成因，先分辨是哪一种再动手**。
- **`getComputedStyle` 取到的初始态才是熄灭态的正确答案。** 上例把元素原始 `boxShadow/borderColor/backgroundColor` 在收集阶段之后、挂载之前读出来当 `off` 态，因此不必为每种卡片另写一套"恢复"值；手写固定 `off` 值会在深色底／浅底混排的项目里把卡片恢复成错的样式。

**是否加机算门禁：不加**（对齐 2026-10-10 常设裁定"不为观感问题新增代码门禁"，且本节三条都是 HTML 写法层判据，静态可判但收益低于一次预览截图自查）。翻转前置＝若再次出现"cue 时刻全部合格而观众报'没反应'"且预览自查已无法兜住，则把"脉冲形态（`yoyo:true` 且 `duration < 0.5`）挂在与 `C[...]` 同一元素上"这一形态接进 `preflight_check` 只报不拦，先积累一轮读数再定档。

---

## 四、场景设计模式

### 4.1 封面场景（scene0）

```html
<div id="scene0" class="scene" style="z-index:999;">
  <!-- 2-3 个大光晕 -->
  <div class="glow" style="width:800px;height:800px;background:rgba(245,158,11,0.15);top:-200px;right:-100px;"></div>
  <div class="glow" style="width:600px;height:600px;background:rgba(96,165,250,0.12);bottom:-150px;left:-100px;"></div>
  <div class="sc" style="align-items:center;justify-content:center;text-align:center;padding-bottom:150px;">
    <div style="max-width:1400px;">
      <p style="font-size:24px;color:#94a3b8;letter-spacing:0.12em;margin-bottom:24px;">ENGLISH SUBTITLE</p>
      <h1 style="font-size:96px;font-weight:900;line-height:1.1;letter-spacing:-0.04em;">
        <span class="accent">主标题强调词</span><br>
        <span style="font-size:64px;color:#e2e8f0;">副标题说明</span>
      </h1>
      <div style="width:160px;height:4px;background:linear-gradient(90deg,#f59e0b,#60a5fa);border-radius:2px;margin:36px auto;"></div>
      <p style="font-size:28px;color:#60a5fa;">一句话概括 · 三个关键词</p>
      <div style="display:flex;justify-content:center;gap:14px;margin-top:32px;">
        <span class="badge badge-amber">关键词1</span>
        <span class="badge badge-blue">关键词2</span>
        <span class="badge badge-green">关键词3</span>
      </div>
    </div>
  </div>
  <div class="subtitle-safe"></div>
</div>
```

### 4.2 痛点/问题场景

```html
<div class="sc" style="gap:36px;">
  <div>
    <p class="mono" style="font-size:20px;color:#f87171;letter-spacing:0.08em;margin-bottom:16px;opacity:0;">PAIN POINTS</p>
    <h2 style="font-size:64px;font-weight:900;line-height:1.15;opacity:0;">问句式标题<span class="red">？</span></h2>
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;opacity:0;">
    <div class="pain-card">
      <div class="pain-title">&#9201; 痛点名称</div>
      <div class="pain-desc">2-3行描述文本</div>
    </div>
    <!-- 更多卡片 -->
  </div>
  <div style="opacity:0;">
    <p style="font-size:32px;font-weight:700;color:#f87171;">总结性金句。</p>
  </div>
</div>
```

**网格策略**：
- 2-4 个卡片：`repeat(2,1fr)` 或 `repeat(4,1fr)`
- 3 个卡片：`repeat(3,1fr)`
- 5+ 个卡片：分行显示（4+3 或 3+3）

### 4.3 对比场景（Before/After）

```html
<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;">
  <div class="compare-old">
    <h4>&#10060; 传统方式</h4>
    <ul>
      <li>具体问题描述</li>
      <!-- 4-5 项 -->
    </ul>
  </div>
  <div class="compare-new">
    <h4>&#9989; 新方式</h4>
    <ul>
      <li>对应解决方案</li>
    </ul>
  </div>
</div>
```

> 两侧各列 4-5 条时本节合适（对比的是"清单内容"）。若对比只剩**一个可变量且可量化**（耗时、成本、道数），改用同量纲横向条形让差值直接可见，见 §4.8 形态三——它的三条硬约束（同量纲、真实比值、两条分别对齐被说出的时刻）也住那里。

### 4.4 工具/架构展示场景

```html
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px;">
  <div class="tool-card">
    <div class="tool-name">工具名称</div>
    <div class="tool-engine">技术栈描述</div>
    <div class="tool-scene">适用场景</div>
  </div>
</div>
```

> 本节默认形态是卡片网格——它装得下"有哪些工具"，装不下"它们怎么连起来"。这一场若表达的是**分层／堆栈**（架构分层、体系三层），或需要**把"点开一个判据"演出来**，见 §4.8 形态二（分层色带）、手段二（立体叠放剖面）、手段三（模拟指针）——三者的实现与边界住那里。

### 4.5 流程/步骤场景

```html
<!-- 垂直流程 -->
<div style="display:flex;flex-direction:column;gap:10px;">
  <div class="pipe-node"><span class="pipe-arrow">&#9654;</span>步骤描述</div>
  <div class="pipe-node"><span class="pipe-arrow">&#9654;</span>步骤描述</div>
</div>

<!-- 网格步骤（带编号） -->
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;">
  <div class="step-card">
    <span class="step-num">01</span>
    <span class="step-title">步骤名称</span>
    <span class="step-desc">2行描述</span>
  </div>
</div>
```

> 本节两种是**列表型**步骤（条目之间不强调"必须依次通过"）。要画"依次通过的关卡 + 不合格退回"的**节点流程图与回流环**（含 SVG 描画动效）见 §4.8 形态一，实现与时间位纪律住那里。

### 4.6 数据/统计场景

```html
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:20px;">
  <div class="card" style="text-align:center;">
    <div class="stat-num blue">1920</div>
    <div class="stat-label">指标名称</div>
  </div>
</div>
```

### 4.7 总结/收尾场景

```html
<div class="sc" style="align-items:flex-start;">
  <div style="max-width:1400px;">
    <p class="mono" style="font-size:20px;color:#f59e0b;opacity:0;">SUMMARY</p>
    <h2 style="font-size:72px;font-weight:900;opacity:0;">
      总结性标题<br><span class="accent">强调关键词</span>
    </h2>
    <div style="width:200px;height:4px;background:#f59e0b;border-radius:2px;margin-top:32px;"></div>
    <div style="display:flex;flex-direction:column;gap:20px;margin-top:40px;">
      <div style="display:flex;align-items:center;gap:16px;">
        <span style="font-size:28px;">&#10003;</span>
        <span style="font-size:28px;font-weight:600;">要点一</span>
      </div>
    </div>
  </div>
</div>
```

### 4.8 结构化图形承载（把"并列要点"画成它本来的形状）

**为什么单列一节**：卡片阵列能装下任何内容，因此写作者会一直用它，代价是观众只能逐条读、看不出关系。2026-10-17 一支已交付的 5 分钟介绍片收到的原话是"视频画面缺乏流程图等结构化的表达形式"，而同一份素材由消费侧按同一流水线做出的另一支片，画面形态明显更丰富——**差距不在素材、不在时长，在分镜阶段有没有为每场决定"这场的图形是什么形"**。本节给出**五类承载形态**（这场画的是什么形状）、**三类表现手段**（这个形状怎么被演出来）与四条共用纪律；每场的"要不要图形、要哪一类"属分镜裁定，见 `分镜设计模板_AI导演模式.md`「常设观感默认」的"结构化图形承载"行（判据住那张表，本节只给实现）。

**通用件一：SVG 描画（不依赖 drawSVG 插件）**——凡"线沿着走一遍"的动效都用这三行，路径可以是回流环、连线、坐标轴：

```javascript
var p = document.getElementById("s3-looppath");
var L = p.getTotalLength();
p.style.strokeDasharray = L; p.style.strokeDashoffset = L;
tl.to(p, { strokeDashoffset: 0, duration: 1.8, ease: "power1.inOut" }, T.s3 + 13.2);
```

**通用件二：填充类动画不改布局**——条形/色带用 `scaleX`（配 `transformOrigin:"left center"`），轨道先出、填充后长；`scaleY/scaleX` 不改变布局高度，故不引发布局跳动（与踩坑清单 §4.4"有效修复方向"同族）。

**形态一：横向节点流程图 + 回流环**（用于"依次通过的几道关卡"，且存在"不合格则退回"的语义）

```html
<div class="s3-flow">                      <!-- display:flex; align-items:stretch; gap:14px -->
  <div class="card s3-gate"><span class="g">闸1</span><div class="t">约束确认</div><div class="d">一句话补充</div></div>
  <div class="s3-farrow">→</div>
  <div class="card s3-gate">…闸2…</div><div class="s3-farrow">→</div>
  <div class="card s3-gate">…闸3…</div><div class="s3-farrow">→</div>
  <div class="card s3-gate">…闸4…</div>
</div>
<div class="s3-loop">                      <!-- position:relative; height:72px -->
  <svg viewBox="0 0 1732 72" preserveAspectRatio="none">
    <path id="s3-looppath" d="M 1500 6 C 1462 66, 300 66, 250 6" fill="none" stroke="#d97706" stroke-width="2.5"/>
  </svg>
  <div class="s3-loop-tag">证据不齐 → 回退上一道闸</div>
</div>
```

闸门卡逐个 `fromTo(… y:18,opacity:0 …)` 错落入场，箭头随后；回流环在"讲完四道闸"的那一刻描画（时间位取该句的 cue 或 `T.sN + 秒数`），标签再晚 1.4s——**回流环必须晚于它所指的关系被说出来**，否则观众先看见结论再听见理由。`viewBox` 配 `preserveAspectRatio="none"` 让弧线随容器宽自适应，但描边粗细会随宽度轻微变形，故只在整幅宽度的横向流程上用。

**形态二：分层色带**（用于"自上而下的层级/栈"，且需要区分"栈内层级"与"外围约束"）

```html
<div class="s7-cols">                       <!-- display:grid; grid-template-columns: 1fr 320px; gap:20px -->
  <div class="s7-stack">
    <div class="s7-l">L1 …</div>            <!-- 白底 -->
    <div class="s7-l">L2 …</div>            <!-- #eff6ff -->
    <div class="s7-l main">L3 …</div>       <!-- linear-gradient(135deg,#1d4ed8,#3b82f6)，白字 -->
  </div>
  <div class="s7-side">
    <div class="s7-side-card">判据／约束一</div>   <!-- border-style:dashed; border-color:#93c5fd -->
    <div class="s7-side-card">判据／约束二</div>
  </div>
</div>
```

配色纪律：一层一色阶（白 → 浅蓝 → 主色实底），**主层用实底渐变、辅助栏用虚线框**——虚线在观众那里读作"不构成层级、只作约束"，实底读作"栈里的一层"，两套语义不得互换（换成实线就变成"另一层"，信息即错）。主层是深底，其上的 cue 点亮须走 §3.6.1 的"深色底不走背景腿"分支。

**形态三：同量纲横向对比条形**（用于"过去 vs 现在"这类只有一个可变量的对照）

```html
<div class="s6-bar">
  <span class="lab">过去 · 跨 3 个专业</span>
  <div class="track"><div class="fill" id="s6-bf1" style="background:#94a3b8;width:100%;"></div></div>
  <span class="val" style="color:#64748b;">约 2 天</span>
</div>
<div class="s6-bar">
  <span class="lab">现在 · 一句话</span>
  <div class="track"><div class="fill" id="s6-bf2" style="background:#2563eb;width:3.2%;min-width:16px;"></div></div>
  <span class="val" style="color:#2563eb;">约 10 分钟</span>
</div>
```

```javascript
tl.fromTo("#s6-bf1", { scaleX:0 }, { scaleX:1, duration:1.1, ease:"power2.out" }, T.s6 + 4.2);
tl.fromTo("#s6-bf2", { scaleX:0 }, { scaleX:1, duration:0.8, ease:"power2.out" }, T.s6 + 9.5);
```

三条硬约束：①**同量纲才并置**（两条必须量同一件事，"2 天"与"10 分钟"换算后同属时长可并置；把"耗时"和"满意度"画进同一坐标即假对比）；②`width` 按真实比值给，并置 `min-width` 防极小值渲成看不见的细线；③两条的填充时刻分别对齐各自被说出的时刻（用 cue 或 `T.sN+秒数`），**禁止同时伸长**——同时伸长等于把对比压成一个动作，观众来不及读第一条。参照色（灰）与主色（蓝）分工固定，全片单色贯穿。

**形态四：等宽代码块隐喻**（用于"这套东西的执行方式本身就是指令序列"——门禁、流水线、规则注入类题材）

```html
<div class="s5-term">                       <!-- font-family: mono; 左色标 + 正文，三行 -->
  <div><span class="ln-k">[注入]</span> 新增门禁，先故意植入缺陷</div>
  <div><span class="ln-r">[确认]</span> 报错才允许合入</div>
  <div><span class="ln-g">[对照]</span> 判定前先跑已知有问题的对象</div>
</div>
```

它替代"①②③ 圆号列表"：同样的三句话，加方括号色标后观众读作"真在跑的机制"而非"三条建议"。色标语义固定为 注入=蓝／失败=红／校验=绿，别为美观换序。

**形态五：中轴延伸**（用于封面或单一主张场——一条竖线把标题"顶天立地"，给孤立论点以结构感）

```css
.s1-claim::before, .s1-claim::after { content:""; position:absolute; left:50%; width:2px; height:26px; background:rgba(37,99,235,0.35); }
.s1-claim::before { top:-40px; } .s1-claim::after { bottom:-40px; }
```

#### 表现手段（不改承载，只改呈现）

上面五类是"这场画的是什么形状"，下面三类是"这个形状怎么被演出来"。三者可以单用、可以叠加，但**都不构成承载**：用了立体与指针却答不出"这场的形状是什么"，仍是卡片墙（唯一例外见共用纪律 4）。

**手段一：同一内容换形态（表示形态递进）**

同一批内容依次换承载形态，把抽象关系演成"你刚看见的那样东西，其实是另一样东西"。最典型的一条链是 **界面形态 → 数据形态 → 结构形态**：一段对话先是聊天窗口，再原样变成一组消息数组，再拆成带类型标签的字段块——观众看到的不是示意图，而是同一份内容的三次存在形态，因此不需要任何比喻。

**首选落点是"同一场内三段"**，不是跨场：本仓每个场景是独立 `div#sceneN`、场间靠整场推转（§3.3），**跨场不存在"同一批元素跟着走"**，把一个跨场换形写成"元素在场上改排版"会直接落空。同一场内用三层绝对定位叠放、按时间位交棒：

```html
<div class="fold">                            <!-- position:relative; width:1000px; height:420px -->
  <div class="st" id="s4-a">                  <!-- 状态 A：聊天气泡，原话 -->
    <div class="bub">帮我准备 v1 的发布，周五要上线。</div>
    …
  </div>
  <div class="st" id="s4-b">…同样的三句，逐字进等宽数组…</div>
  <div class="st" id="s4-c">…由数组提炼出的字段块（目标／偏好／教训）…</div>
</div>
```
```css
.fold .st { position:absolute; inset:0; opacity:0; }
```
```javascript
// 三态交棒：每态都是「进→出」，不留两态长期并存（时间位为示例，按本场 cue 定）
tl.fromTo("#s4-a",  { opacity:0, y:16 }, { opacity:1, y:0, duration:0.45 },  T.s4 + 1.2);
tl.to("#s4-a",      { opacity:0, y:-14, duration:0.45 },                     T.s4 + 6.2);
tl.fromTo("#s4-b",  { opacity:0, y:16 }, { opacity:1, y:0, duration:0.45 },  T.s4 + 6.2);
tl.to("#s4-b",      { opacity:0, y:-14, duration:0.45 },                     T.s4 + 11.0);
tl.fromTo("#s4-c",  { opacity:0, y:16 }, { opacity:1, y:0, duration:0.45 },  T.s4 + 11.0);
```

四条约束：

1. **两段递进，不是三段文字游戏**。第一段（界面→数据）**必须逐字**——同样的句子原样进数组，改一个字观众就读成"另一件事"；第二段（数据→结构）**允许提炼压缩**，但压缩后的每一条须能指回上一段的哪一条（编号、原话片段、位置对位三者之一）。把"逐字"当成全程铁律会把最后一步逼成同义反复，把"提炼"松到全程则三步之间认不出是同一样东西。
2. **三段各答一问**：说了什么 → 系统收到的是什么 → 系统记住了什么。缺这层递进，就只是同屏换皮，观众只会记得"画面在动"。
3. **换形要占时间，时长以量测为准**：交棒用 0.4s 左右的淡出淡入是可用起点，但本仓**无实测区间**，首次落地须用 `instant_preview.py` 在交棒前后逐刻截图，确认"能认出这是同一批内容"再定时间位——不要凭感觉写死。
4. **口画一致仍然适用**：链上每一环出现的数字、条数都必须在旁白里听得到（共用纪律 1 不因换形豁免）。

**跨场变体（次选）**：确实需要跨场递进时，每场各自独立渲染自己的版本，靠两个信号让观众认出连续性——**同一标识**（编号、原话片段）＋**同一屏位**（锚元素在两场落在相近坐标）。两个信号缺一，观众读成两件不相干的事，此时不如不做。

适用范围：题材本身有"用户视角 vs 系统视角"两层的（行业介绍片几乎都属此类）；全片只有一个视角的，这套手法没有对象，不要硬凑。

**手段二：立体叠放剖面（CSS 3D）**

给分层／堆栈类承载加厚度——色带从"平面并排"变成"叠放剖面"，观众读作剖面图而非表格。本仓已在 `prefab-agent-openatom` 修订05 上落地两处（四层架构、三层体系），层带连同投影整体倾斜，层间错位即形成纵深。

```javascript
// transform 一律经 GSAP vars 传入：写进 CSS 的 transform 会被 GSAP 的 rotation 覆盖
var R = { rotationX: 6, rotationY: -4, transformPerspective: 1700 };
tl.fromTo("#s3-l1", { opacity:0, x:-30, ...R }, { opacity:1, x:0, ...R, duration:0.55, ease:"power2.out" }, T.s3 + 1.40);
```

角度与透视按层数与画布宽定（修订05 两处实测取值：6°/−4°/1700、8°/−7°/1900），**是起点不是常量**。三条边界：① 立体只加在"本来就分层"的内容上，给平面列表硬加倾角只会让文字更难读；② 倾斜后的层间遮挡顺序必须与层级语义一致，否则被压住的那层会被读成"次要"；③ 倾角上限以层内文字仍清晰为准——再大就不是剖面而是特效了。

**手段三：模拟指针（把"点开"演出来）**

画面里放一个指针，按真实动作路径走过去：入场 → 移动到构件 → 到达后 0.2–0.35s 微移（读作"点下"，s3／s5／s7 用 0.2s、s1 用 0.35s，是实测两档、不是固定值）→ 移开／淡出。用来表示"这里被打开了"，不需要旁白解释。修订05 在 4 个场景使用（s1／s3／s5／s7），落点数量按本场要被点开的构件数定——s1 三处（对应三枚缺口标签）、s3／s5／s7 各一处。

```html
<div id="cursor" style="opacity:0;">
  <svg width="34" height="46" viewBox="0 0 34 46">
    <path d="M4 3 L30 24 L17 26 L11 42 Z" fill="#ffffff" stroke="#141c26" stroke-width="2.5" stroke-linejoin="round"/>
  </svg>
</div>
```
```css
#cursor { position:absolute; left:0; top:0; z-index:600; pointer-events:none;
          filter:drop-shadow(0 6px 10px rgba(20,28,38,0.28)); }
```
```javascript
// s1 完整三段移动＋三次"点下"微移（节选自修订05；s3/s5/s7 各只有一次）
tl.fromTo("#cursor", { opacity:0, left:1320, top:430 }, { opacity:1, duration:0.3 },        T.s1 + 9.67);
tl.to("#cursor",      { left:1580, top:290, duration:0.9,  ease:"power2.inOut" },           T.s1 + 9.91);
tl.to("#cursor",      { left:1500, top:282, duration:0.35 },                                T.s1 + 10.97); // 微移＝"点下"
tl.to("#cursor",      { left:1580, top:470, duration:1.2,  ease:"power2.inOut" },           T.s1 + 15.57);
tl.to("#cursor",      { left:1570, top:646, duration:1.1,  ease:"power2.inOut" },           T.s1 + 20.06);
tl.to("#cursor",      { opacity:0, duration:0.4 },                                          T.s1 + 25.01);
```

三条边界：① **只演人手会做的动作**（点开、翻页、拖走），不演系统内部过程——指针演系统会让观众误以为要自己操作；② 落点用 `left/top` 直接写画布坐标（与绝对定位的构件同坐标系），改布局时能直接对上；换 `x/y` 需自行换算坐标；③ 指针路径不得横穿正在被读的文字——穿过一次，那一行就白写了。

**共用纪律四条**（本节各形态与手段都受约束）：

1. **图形上的数字必须来自旁白**（口画一致）。参考件里有、本片旁白没有的数据一律不引入——否则出现"画上有 59→79 的折线、耳朵里没听到这两个数"，观众要么去核要么失信任；这是取舍而非遗漏，须在交付说明里写明未引入的理由。
2. **数值/标签不得压在图形上**（既有常设观感）。条形右侧的 `val`、色带上的层级名一律留出轨道宽度，不叠印。
3. **一场一个主形态**。两类以上并置时须有从属关系（流程图为主体、回流环为其附属），不得并列两块同等体量的图形——那会把"看结构"变成"找结构"。
4. **表现手段不替代承载形态**。立体、指针、换形态回答的是"怎么演"，不是"画的是什么形"。一场若答不出五类承载中的哪一类，叠多少立体与指针都仍是卡片墙——这是本节最容易被绕过的一条：看上去"高级"的片子与没有结构的片子，差别只在有没有先答出形状。**唯一例外是手段一的换形态链**：它本身构成一种承载，表达的是"同一内容被重新组织"这层关系，此时链上首尾不必各自归入五类；但链上任何一环若只是无关系可言的卡片阵列，仍判无承载。

**形态从哪来（参考件对照法）**：手上有一支同素材／同类型的更好看的片子时，按 15–25s 间隔抽 ≥12 帧，逐帧只回答一个问题——"这一帧的形状在我这一版里有没有对应物？"缺的形态按本节五类承载形态与三类表现手段补，不缺的不动。**对照取的是"形状"不是"数据"**：把对方画面里的数字搬过来属越界（见共用纪律 1）。本仓首次兑现该法的对象是一支 5 分钟信息片的原版与 `_修订02`（取证件按临时落点纪律不入 git，故本处只留方法、不留路径指针）。

---

## 五、真实图片集成

### 5.1 全屏背景图场景
```html
<div id="sceneN" class="scene" 
     style="background-image:url('素材路径');background-size:cover;background-position:center;">
  <!-- 暗色叠层保证文字可读性 -->
  <div style="position:absolute;inset:0;background:rgba(12,18,34,0.75);"></div>
  <div class="sc" style="gap:32px;position:relative;z-index:2;">
    <!-- 场景内容 -->
  </div>
  <div class="subtitle-safe"></div>
</div>
```

### 5.2 产品展示图（split 布局）
```html
<!-- 图左文右 -->
<div class="sc" style="display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:center;">
  <div style="overflow:hidden;border-radius:12px;">
    <img src="产品图.jpg" style="width:100%;height:100%;object-fit:cover;">
  </div>
  <div>
    <h2>标题</h2>
    <p>描述文本</p>
  </div>
</div>

<!-- 竖版照片：模糊背景双层方案 -->
<div style="position:relative;overflow:hidden;border-radius:12px;height:500px;">
  <img src="竖版图.jpg" 
       style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;filter:blur(20px) brightness(0.6);transform:scale(1.1);">
  <img src="竖版图.jpg" 
       style="position:relative;height:100%;object-fit:contain;">
</div>
```

**容器高度须为字幕安全线留量**：图片容器底边必须落在安全线之上（1080 画布 → y860，推导口径见 `AGENTS.md` 字幕安全区预检条）。**不得用"画布高 − 安全线"当可用高度**——标题行、上下 padding、栅格 gap 都吃在同一区间里。实测点（2026-09-26）：竖版容器 640px 时底边压线 6-7px 被预览判溢出，降到 590px 过。数值属该布局下的观测，换排版须重新以 `instant_preview.py` 实测，不得当常量套用。

---

## 六、检查清单（HTML 制作完成后）

- [ ] `<div id="root">` 的 data-duration 和 data-cover-duration 正确
- [ ] scene0 有 z-index:999
- [ ] 每个场景有 `<div class="subtitle-safe"></div>`
- [ ] subtitle-safe height 在 120-150px
- [ ] .sc padding-bottom ≥ safe-height + 20px
- [ ] 无 `[Image]`、`placeholder`、`src=""` 占位符
- [ ] 所有 `<img src>` 引用真实文件
- [ ] 字号分层符合规范
- [ ] 装饰层 opacity ≥ 0.08
- [ ] GSAP 只有一个 `tl`（paused:true）
- [ ] 内容场景的动画时间位全部写成 `T.sN + offset`，除封面窗口（0–3s）外无裸数字时间位（§3.0）
- [ ] 若挂了 `data-cue-at`：词逐字在该场旁白里、属性在带 `data-scene-id` 的 div 内、tween 写成 `T.sN + C["sN:词"]`，且 `CUE-MAP` 块未被手工编辑（§3.6）
- [ ] 挂了 cue 的元素，点亮是"亮住不灭 + 下一锚让位"形态而非亚秒脉冲；句级降级锚（`C` 值≈场首）已配 `enterAt`；同刻组锚的熄灭向前扫描并封顶（§3.6.1）——**预览截图即可验，不必等渲染**
- [ ] 每个信息场景有结构化图形承载（流程/层级/对比/趋势/序列），非纯卡片阵列；图形上出现的每个数字都能在旁白里听到（§4.8）
- [ ] 若用了立体／模拟指针／同一内容换形态：本场仍答得出承载形态是哪一类；换形态的几场内容逐字未改、有可追踪标识；指针不横穿被读的文字（§4.8 表现手段与共用纪律 4）
- [ ] 封面淡出在 2.5s，body offset 在 3s
- [ ] 转场类型交替使用
- [ ] 卡片 stagger 间隔 2-3s
- [ ] 每个场景有 glow 持续动画
- [ ] 无 >3s 的纯静止区间
