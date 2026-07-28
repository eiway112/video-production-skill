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
- **场景内容动画起始时间 T ≥ 3**（封面占 0-3s，`tl.set("body", {}, 3)` 后主线才开始）

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
- [ ] 封面淡出在 2.5s，body offset 在 3s
- [ ] 转场类型交替使用
- [ ] 卡片 stagger 间隔 2-3s
- [ ] 每个场景有 glow 持续动画
- [ ] 无 >3s 的纯静止区间
