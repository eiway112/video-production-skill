# 复用组件库

每做完一条视频，把可复用的动效组件注册到这里。做得越多，后面越快。

## 注册规范

每个组件必须记录以下信息，确保下次使用时不需要重新理解或重写。

---

## 结构性组件（HTML 骨架）

### S-01 基础模板骨架

所有 HyperFrames 项目的 HTML 骨架，包含 GSAP CDN 引入、root 容器、script 注册。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{width:1920px;height:1080px;overflow:hidden;background:/* 主题色 */;
     font-family:"Noto Sans SC","Montserrat",sans-serif;color:/* 文字色 */;}
.scene{position:absolute;top:0;left:0;width:1920px;height:1080px;overflow:hidden;}
.sc{display:flex;flex-direction:column;width:100%;height:100%;
    padding:60px 100px 200px;position:relative;}
/* .subtitle-safe 必须在每个场景中存在 */
</style>
</head>
<body>
<div id="root" data-composition-id="main" data-width="1920" data-height="1080"
     data-start="0" data-duration="/* 总时长 */" data-cover-duration="3">
  <!-- scenes here -->
</div>
<script>
const tl = gsap.timeline({ paused: true });
window.__timelines = window.__timelines || {};
/* animations */
tl.set("body", {}, 3.0);  // 封面时长全局偏移
window.__timelines["main"] = tl;
</script>
</body>
</html>
```

| 参数 | 说明 | 来源 |
|---|---|---|
| `data-duration` | 视频总时长（含封面 3s） | config JSON `video_duration` |
| `data-cover-duration` | 封面持续秒数，固定 3 | 所有项目统一 |
| `padding-bottom` | `.sc` 必须 ≥ 150px（字幕安全区） | 强制校验 |

### S-02 封面场景 (scene0)

静态封面，t=0 可见，2.5s 后渐隐露出 scene1。所有项目共用此模式。

```html
<div id="scene0" class="scene" style="z-index:999;">
  <!-- 装饰光晕 -->
  <div class="glow" style="width:800px;height:800px;
    background:radial-gradient(circle,rgba(/*主色*/,0.18) 0%,transparent 70%);
    top:-200px;right:-100px;"></div>
  <div class="glow" style="width:600px;height:600px;
    background:radial-gradient(circle,rgba(/*辅色*/,0.12) 0%,transparent 70%);
    bottom:-150px;left:-100px;"></div>
  <div class="sc" style="justify-content:center;align-items:center;text-align:center;">
    <div style="font-size:72px;font-weight:900;">视频标题</div>
    <div style="font-size:28px;margin-top:16px;opacity:0.7;">关键词 / 副标题</div>
  </div>
</div>
```

GSAP 封面渐隐动画：
```javascript
tl.to("#scene0", { opacity: 0, duration: 0.5, ease: "power2.inOut" }, 2.5);
tl.set("#scene0", { visibility: "hidden" }, 3.0);
```

### S-03 字幕安全区 (subtitle-safe)

每个 `<div id="sceneN">` 内必须包含此元素，否则 step0 校验失败。

```html
<div class="subtitle-safe"></div>
```

CSS（浅色主题 / 深色主题二选一）：
```css
/* 浅色主题 */
.subtitle-safe{position:absolute;bottom:0;left:0;width:100%;height:200px;
  background:linear-gradient(to bottom,rgba(BG_R,BG_G,BG_B,0) 0%,
  rgba(BG_R,BG_G,BG_B,0.85) 50%,rgba(BG_R,BG_G,BG_B,0.95) 100%);
  z-index:100;pointer-events:none;}

/* 深色主题 */
.subtitle-safe{position:absolute;bottom:0;left:0;width:100%;height:130px;
  background:linear-gradient(to bottom,rgba(26,31,46,0) 0%,
  rgba(26,31,46,0.85) 40%,rgba(26,31,46,0.95) 100%);
  z-index:100;pointer-events:none;}
```

### S-04 结尾渐黑 (end-fade)

视频最后一帧平滑过渡到黑色，避免突兀截断。

```html
<div id="scene-end-fade" style="position:absolute;inset:0;background:#000;
  opacity:0;z-index:9999;pointer-events:none;"></div>
```

```javascript
tl.to("#scene-end-fade", { opacity: 1, duration: 1.5, ease: "power2.inOut" },
    /* VIDEO_DURATION - 2 */);
```

---

## CSS 通用组件

### C-01 装饰光晕 (glow)

背景装饰性径向渐变圆，增加视觉层次。参数化颜色和位置。

```css
.glow{position:absolute;border-radius:50%;pointer-events:none;}
```

| 变体 | 尺寸 | 模糊 | 来源 |
|---|---|---|---|
| 标准（大） | 800×800px | 无/120px | 所有项目 |
| 标准（小） | 600×600px | 无/120px | 所有项目 |
| 呼吸动画 | scale 1.0→1.1, 6-8s, sine.inOut yoyo | — | hotel/hospital |

### C-02 徽章 (badge)

圆角药丸标签，用于场景分类标记。

```css
.badge{display:inline-block;padding:8px 20px;border-radius:999px;
  font-size:20px;font-weight:700;}
.badge-amber{background:rgba(217,119,6,0.12);color:#d97706;
  border:1px solid rgba(217,119,6,0.2);}
.badge-teal{background:rgba(13,148,136,0.12);color:#0d9488;
  border:1px solid rgba(13,148,136,0.2);}
.badge-blue{background:rgba(30,64,175,0.1);color:#1e40af;
  border:1px solid rgba(30,64,175,0.15);}
.badge-coral{background:rgba(238,108,77,0.2);color:#ee6c4d;}  /* 暗色主题 */
```

### C-03 内容卡片 (card)

通用信息容器，浅色/深色两套。

```css
/* 浅色主题 */
.card{background:#fffef7;border:1px solid rgba(217,119,6,0.12);
  border-radius:16px;padding:24px 28px;
  box-shadow:0 2px 12px rgba(217,119,6,0.06);}

/* 深色主题 */
.card{background:rgba(61,90,128,0.15);border:2px solid rgba(61,90,128,0.3);
  border-radius:16px;padding:28px 36px;}
.card-accent{background:rgba(238,108,77,0.1);border-color:rgba(238,108,77,0.3);}
```

### C-04 水印大字 (ghost)

超大半透明背景文字，增加视觉纵深。

```css
.ghost{position:absolute;font-size:200px;font-weight:900;opacity:0.04;
  color:/* 主题色 */;pointer-events:none;letter-spacing:-0.05em;}
```

### C-05 参数指标行 (spec-row)

键值对展示，左侧标签右侧数值。

```css
.spec-row{display:flex;align-items:center;justify-content:space-between;
  padding:14px 0;border-bottom:1px solid rgba(217,119,6,0.1);}
.spec-label{font-size:20px;color:#78716c;font-weight:600;}
.spec-val{font-size:28px;font-weight:900;color:#d97706;}
```

### C-06 环形指标 (metric-ring)

圆形大数字展示，用于核心数据强调。

```css
.metric-ring{width:220px;height:220px;border-radius:50%;
  border:6px solid rgba(217,119,6,0.2);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:rgba(217,119,6,0.04);}
.metric-val{font-size:72px;font-weight:900;color:#d97706;line-height:1;}
.metric-unit{font-size:18px;color:#78716c;margin-top:4px;}
```

### C-07 模拟浏览器窗口 (mock-browser)

软件 UI 展示场景的浏览器外框。

```css
.mock-browser{background:#151a27;border-radius:16px;
  border:2px solid rgba(61,90,128,0.25);overflow:hidden;
  box-shadow:0 20px 60px rgba(0,0,0,0.4);}
.mock-topbar{height:44px;background:#1e2435;display:flex;
  align-items:center;padding:0 18px;gap:10px;}
.mock-dot{width:12px;height:12px;border-radius:50%;}
.mock-body{padding:32px;}
```

---

## 转场组件

### T-01 Push Slide（水平推）

当前场景向左滑出，下一场景从右滑入。最常用转场。

```javascript
/* TRANSITION N->N+1: Push Slide (T=Xs) */
tl.to("#sceneN", { x: -1920, duration: 0.8, ease: "power3.inOut" }, T);
tl.fromTo("#sceneN+1", { x: 1920, opacity: 1 },
  { x: 0, duration: 0.7, ease: "power3.inOut" }, T);
tl.set("#sceneN", { visibility: "hidden" }, T + 1.7);
```

### T-02 Vertical Push（垂直推）

当前场景向上滑出，下一场景从下滑入。交替使用避免单调。

```javascript
/* TRANSITION N->N+1: Vertical Push (T=Xs) */
tl.to("#sceneN", { y: -1080, duration: 0.8, ease: "power2.inOut" }, T);
tl.fromTo("#sceneN+1", { y: 1080, opacity: 1 },
  { y: 0, duration: 0.7, ease: "power2.inOut" }, T);
tl.set("#sceneN", { visibility: "hidden" }, T + 1.7);
```

### T-03 封面渐隐

见 S-02，固定 2.5s 触发，0.5s 渐隐。

---

## GSAP 动画模式

### A-01 元素入场（标准）

```javascript
tl.fromTo("#el-id", { y: 30, opacity: 0 },
  { y: 0, opacity: 1, duration: 0.5, ease: "power2.out" }, T);
```

变体：`x: -40`（从左滑入）、`x: 40`（从右滑入）、`scale: 0.5`（缩放弹入）。

### A-02 标签+标题入场序列

```javascript
// 标签先入（小字号，快速）
tl.fromTo("#sN-tag", { x: -20, opacity: 0 },
  { x: 0, opacity: 1, duration: 0.3, ease: "power3.out" }, T);
// 标题紧随（稍慢）
tl.fromTo("#sN-title", { y: 30, opacity: 0 },
  { y: 0, opacity: 1, duration: 0.6, ease: "power2.out" }, T + 0.3);
```

### A-03 光晕呼吸动画

```javascript
tl.to("#sNg1", { scale: 1.1, duration: 8, ease: "sine.inOut",
  yoyo: true, repeat: 1 }, T);
tl.to("#sNg2", { scale: 1.12, x: 20, duration: 6, ease: "sine.inOut",
  yoyo: true, repeat: 1 }, T + 0.5);
```

### A-04 弹性弹入

```javascript
tl.fromTo("#el", { scale: 0.5, opacity: 0 },
  { scale: 1, opacity: 1, duration: 0.6, ease: "elastic.out(1,0.6)" }, T);
```

### A-05 进度条生长

```javascript
tl.fromTo("#bar", { opacity: 0, width: 0 },
  { opacity: 1, width: "100%", duration: 0.5, ease: "power2.out" }, T);
```

### A-06 全局封面偏移

所有主线动画时间 = 原始时间 + cover_duration。通过一行代码实现：

```javascript
tl.set("body", {}, /* COVER_DURATION */);
```

---

## 主题配色方案

| 方案 | 背景色 | 文字色 | 主色 | 辅色 | 使用项目 |
|---|---|---|---|---|---|
| 暖色浅色 | `#fef9e7` | `#1c1917` | `#d97706`(amber) | `#0d9488`(teal) | hotel, hospital |
| 冷色浅色 | `#f0f9ff` | `#0f172a` | `#1e40af`(blue) | `#10b981`(green) | partition-wall |
| 深色科技 | `#1a1f2e` | `#e8ecf4` | `#ee6c4d`(coral) | `#98c1d9`(blue) | crm-video |
| 深色蓝紫 | `#0f172a` | `#e2e8f0` | `#f59e0b`(amber) | `#818cf8`(indigo) | file-governance |
| 深色蓝 | `#0c1222` | `#e8ecf4` | `#60a5fa`(blue) | `#f97316`(orange) | workflow-summary |

---

## 文字动效组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
| 痛点数据卡 | 行业痛点数据卡片+解决方案对比，暖色卡片逐个弹出 | hotel-partition-wall | index.html #scene1 | pain_points[], solution | 行业切入、问题引出 |

## 卡片组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
| 产品参数面板 | 左侧产品图+右侧核心参数逐个弹出 | hotel-partition-wall | index.html #scene2 | image, params[], title | 产品展示、规格说明 |
| 墙体截面分层构建 | 从内到外逐层展示墙体结构，每层标注材料和厚度 | hotel-partition-wall | index.html #scene3 | layers[], durations[] | 产品构造展示、材料层分析 |
| 密封工艺特写 | CSS/SVG绘制型材截面+密封条安装动画 | hotel-partition-wall | index.html #scene4 | profile_type, seal_type | 技术细节展示、工艺说明 |
| 安装流程卡片 | 四步安装流程编号+图标+描述 | hotel-partition-wall | index.html #scene5 | steps[] | 施工流程、系统讲解 |
| 管线分离示意 | 龙骨空腔内管线走线图+可拆卸面板演示 | hotel-partition-wall | index.html #scene6 | pipe_routes[], removable | 管线分离、维护便利展示 |
| 隔声频段曲线 | 简化版隔声频率响应曲线CSS绘制 | hotel-partition-wall | index.html #scene7 | freq_bands[], values[] | 声学性能展示 |
| 价值主张卡片 | 三大价值对比卡（更快/更轻/更安静） | hotel-partition-wall | index.html #scene8 | values[], icons[] | 品牌收尾、核心卖点总结 |
| CRM 工作台仪表盘 | 模拟浏览器窗口+统计数字+状态流 | crm-video | index.html #scene3 | stats[], statuses[] | 软件 UI 展示 |
| 设计原则卡片 | 编号+标题+描述的原则列表 | crm-video | index.html #scene2 | principles[] | 理念阐述、设计规范 |
| AI 智能提取演示 | 截图上传→信息提取→确认写入的三步流程 | crm-video | index.html #scene5 | source_img, extracted[] | AI 功能演示 |

## 背景组件

| 组件名 | 效果描述 | 来源项目 | 参数 | 适用场景 |
|---|---|---|---|---|
| 双光晕背景 | 右上 800px + 左下 600px 径向渐变 | 所有项目 | color1, color2, opacity | 通用背景装饰 |
| 水印大字 | 200px 4%透明度背景文字 | 所有项目 | text, color | 视觉纵深、品牌感 |
| 装饰线条 | 水平渐变线条（两端透明→中间实色） | crm-video | color, width, y | 分割区域、节奏感 |
| 氛围背景图 | ImageGen 生成图+暗色叠层保证文字可读 | hospital-partition-wall | image_url, overlay_rgba | 真实空间感场景 |

## 转场组件

| 组件名 | 效果描述 | 来源项目 | 参数 | 适用场景 |
|---|---|---|---|---|
| Push Slide | 水平推移切换场景 | 所有项目 | direction, duration | 场景间过渡（奇数→偶数） |
| Vertical Push | 垂直推移切换场景 | hotel/hospital | direction, duration | 场景间过渡（偶数→奇数） |
| 封面渐隐 | scene0 z-index:999 渐隐 | 所有项目 | cover_duration | 视频开头 |
| 结尾渐黑 | 全屏黑色叠层渐显 | 所有项目 | duration | 视频结尾 |

## 标注组件

| 组件名 | 效果描述 | 来源项目 | 参数 | 适用场景 |
|---|---|---|---|---|
| 照片轮播标注 | 施工照片轮播+动态标注框 | hotel-partition-wall | photos[], labels[] | 施工现场展示、案例展示 |
| 状态药丸 | 圆角状态标签+彩色圆点 | crm-video | text, dot_color | 状态流、流程节点 |
| 遮罩文字条 | 纯色底遮挡文字（悬念效果） | crm-video | text, bg_color | 悬念制造、信息延迟揭示 |

---

## 复用规则

- 新组件必须先在这里注册，再在新项目中使用。
- 组件代码必须自包含，不依赖特定项目的路径或变量。
- 参数必须明确，不允许硬编码颜色、尺寸、文案。
- 每个组件附带一个使用示例，确保下次使用时能直接跑通。
- 从旧项目移植组件时，必须做参数化改造，不直接复制粘贴。
- 转场必须交替使用 Push Slide / Vertical Push，避免连续使用同一类型。
- 每个场景必须有 `.subtitle-safe` 且 `.sc` padding-bottom ≥ 150px。
# 复用组件库

每做完一条视频，把可复用的动效组件注册到这里。做得越多，后面越快。

## 注册规范

每个组件必须记录以下信息，确保下次使用时不需要重新理解或重写。

## 文字动效组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
| 痛点数据卡 | 行业痛点数据卡片+解决方案对比，暖色卡片逐个弹出 | hotel-partition-wall | index.html #scene1 | pain_points[], solution | 行业切入、问题引出 |

示例：
```
逐字高亮 | 文字逐字变色，配合口播节奏 | Loop Engineer | src/effects/text-highlight.js | text, color, speed | 关键概念、术语解释
故障字效果 | CRT扫描线+文字闪烁 | Loop Engineer | src/effects/glitch-text.js | text, intensity | 赛博风/科技感场景
数字滚动 | 数字从0滚动到目标值 | 创始人手册 | src/effects/number-roll.js | target, duration, prefix | 金额、参数、百分比
```

## 卡片组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
| 产品参数面板 | 左侧产品图+右侧核心参数逐个弹出 | hotel-partition-wall | index.html #scene2 | image, params[], title | 产品展示、规格说明 |
| 墙体截面分层构建 | 从内到外逐层展示墙体结构，每层标注材料和厚度 | hotel-partition-wall | index.html #scene3 | layers[], durations[] | 产品构造展示、材料层分析 |
| 密封工艺特写 | CSS/SVG绘制型材截面+密封条安装动画 | hotel-partition-wall | index.html #scene4 | profile_type, seal_type | 技术细节展示、工艺说明 |
| 安装流程卡片 | 四步安装流程编号+图标+描述 | hotel-partition-wall | index.html #scene5 | steps[] | 施工流程、系统讲解 |
| 管线分离示意 | 龙骨空腔内管线走线图+可拆卸面板演示 | hotel-partition-wall | index.html #scene6 | pipe_routes[], removable | 管线分离、维护便利展示 |
| 隔声频段曲线 | 简化版隔声频率响应曲线CSS绘制 | hotel-partition-wall | index.html #scene7 | freq_bands[], values[] | 声学性能展示 |
| 价值主张卡片 | 三大价值对比卡（更快/更轻/更安静） | hotel-partition-wall | index.html #scene8 | values[], icons[] | 品牌收尾、核心卖点总结 |

示例：
```
产品参数卡 | 左侧产品图+右侧参数列表 | 辅材产品视频 | src/components/product-card.html | image, params[], title | 产品展示、材料对比
成本拆解卡 | 横向条形图+金额标注 | 旧改案例视频 | src/components/cost-breakdown.html | items[], total | 报价说明、成本分析
工艺流程卡 | 步骤编号+图标+描述 | 装配式培训视频 | src/components/process-flow.html | steps[] | 施工流程、系统讲解
```

## 背景组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

## 转场组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
| 封面渐隐过渡 | scene0 z-index:999封面2.5s渐隐露出scene1 | hotel-partition-wall | index.html #scene0 | cover_duration | 视频开头封面过渡 |
| 场景推送切换 | transform translateX/Y push slide场景间切换 | hotel-partition-wall | index.html | direction, duration | 场景间过渡 |

## 标注组件

| 组件名 | 效果描述 | 来源项目 | 代码位置 | 参数 | 适用场景 |
|---|---|---|---|---|---|
| 照片轮播标注 | 施工照片轮播+动态标注框 | hotel-partition-wall | index.html #scene1/5 | photos[], labels[] | 施工现场展示、案例展示 |

示例：
```
红框标注 | 在图片上添加红色矩形框 | 施工工艺视频 | src/components/red-box.html | x, y, w, h, label | 施工节点、细节放大
箭头指引 | 动态箭头指向关键区域 | 招商培训视频 | src/components/arrow.html | from, to, color | 流程指引、重点标注
```

## 复用规则

- 新组件必须先在这里注册，再在新项目中使用。
- 组件代码必须自包含，不依赖特定项目的路径或变量。
- 参数必须明确，不允许硬编码颜色、尺寸、文案。
- 每个组件附带一个使用示例，确保下次使用时能直接跑通。
- 从旧项目移植组件时，必须做参数化改造，不直接复制粘贴。
