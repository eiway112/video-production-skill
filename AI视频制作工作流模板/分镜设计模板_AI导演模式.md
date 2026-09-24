# 分镜设计模板（AI 导演模式 — 标准化输出格式）

> 每个视频项目在 HTML 手写前，AI 必须先完成此分镜设计文档，提交用户审核。
> 审核通过后才能进入 HTML 制作阶段。

---

## 项目基本信息

| 项目 | 内容 |
|------|------|
| 视频主题 | {主题} |
| 目标时长 | {N} 秒（含 3s 封面） |
| 场景数量 | {N} 个（scene0 封面 + scene1-N 内容） |
| 主题风格 | {深色/浅色/混合} |
| 旁白语音 | {zh-CN-YunxiNeural / 其他} |
| 输出文件 | {成果文件/视频/视频名.mp4} |

---

## 素材盘点摘要

（由 `asset_scanner.py` 生成，AI 补充语义标注）

### 可用图片

| 文件名 | 尺寸 | 比例 | 方向 | 内容描述 | 关联场景 |
|--------|------|------|------|---------|---------|
| {filename} | {WxH} | {ratio} | 横版/竖版 | {AI 读取图片后的内容描述} | scene{N} |

### 可用文档

| 文件名 | 类型 | 关键内容 | 关联场景 |
|--------|------|---------|---------|
| {filename} | PPT/Word/MD | {提取的内容摘要} | scene{N} |

### 缺失素材

| 场景 | 需要的素材 | 处理方式 |
|------|-----------|---------|
| scene{N} | {描述} | ImageGen 生成 / 纯信息排版 / 跳过 |

---

## 场景分镜设计

### Scene 0 — 封面

| 维度 | 设计 |
|------|------|
| **画面构成** | {描述：标题+关键词badge+光晕装饰} |
| **标题** | {主标题文字} |
| **副标题** | {英文大写标签} |
| **关键词** | {badge1, badge2, badge3} |
| **背景** | {深色 #0f172a / 图片+叠层} |
| **持续时间** | 3s（固定） |
| **动效** | 标题渐入 → badge 弹入 → 2.5s 封面淡出 |

### Scene N — {场景标题}

| 维度 | 设计 |
|------|------|
| **旁白** | {旁白原文} |
| **旁白全文** | {完整旁白文本，用于 TTS 生成和 config.json} |
| **预估时长** | {TTS 时长}s（TTS 占窗口 {60-75}%） |
| **场景类型** | {hero / cards / split / comparison / stats / pipeline / closing} |
| **布局** | {描述：grid 列数、左右分栏、全屏图等} |
| **画面构成** | {详细描述每个视觉元素及其位置} |
| **素材引用** | {引用的图片文件名，或“纯文字排版”} |
| **入场动画** | {tag→title→cards 错落入场} |
| **持续动效** | {入场后填充：glow 呼吸 / 卡片脉冲 / 光线漂移} |
| **转场方式** | {Push Slide(水平) / Vertical Push(垂直)} → 到下一场景 |

（每个场景重复此表格）

---

## 数字规范自检（规定）

每个 Scene 的**旁白全文**定稿后、提交用户审核节点前逐条自检。机器门为 preflight 检查 13（`narration_digits`，`preflight_check.py:1199-1213`：render 模式命中即阻断、audit 模式降级 warning）；本节是**撰写侧**自检——门禁通过不替代本节（白名单只管例外，可读性由人裁定）：

- [ ] 旁白与字幕中的数值一律阿拉伯数字："30小时"、"5%"、"20元"；禁止"三十小时"、"百分之五"形态
- [ ] 例外仅成语（如"三心二意"）与序数（如"第三"）；白名单唯一权威源 `程序文件/配置/config/quality/narration_digits_rules.json`（`idiom_whitelist`），追加例外改该文件或经 config 项目级 `narration_digits_whitelist` 声明——**不得在分镜/旁白里 ad-hoc 开口子**
- [ ] 带 json 两张单位表（`primary_units` / `extended_units`）内单位的量化数值必须全部阿拉伯（此为检查 13 裁定面）；表外单位的中文数字组合不在门禁裁定面，撰写侧按可读性裁定，但不得借此为表内量化项保留中文数字
- [ ] 字幕与旁白同源：旁白数值改动后同步核对字幕数值，只改一侧即分叉

---

## 时间分配总表

| 场景 | 窗口(s) | TTS时长(s) | TTS占比 | 入场动画(s) | 持续动效 | 转场(s) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 0 | 0-3 | — | — | 淡出 0.5s | — | — |
| 1 | 3-{end} | {dur} | {%} | {dur}s | {type} | {type} 0.8s |
| ... | | | | | | |
| **合计** | **{total}s** | | | | | |

**时间规范检查**：
- [ ] 每个场景 TTS 占窗口 60-75%
- [ ] 无场景连续静止 >3s
- [ ] 转场 0.8-1.0s

---

## 视觉参数决策

| 参数 | 值 | 依据 |
|------|:---:|------|
| subtitle-safe height | {130-140}px（深色）/ {120-140}px（浅色） | {深色/浅色主题} |
| .sc padding-bottom | {150-160}px | safe-height + 20px |
| 主光晕 opacity | {0.08-0.15} | {场景氛围} |
| 装饰元素最大 y | {≤840} | 远离 y=860 安全线 |
| 字号体系 | 封面标题 {96-108}px / 场景标题 {48-82}px / 副标题 {28-36}px / 卡片标题 {20-28}px / 正文 {16-22}px | 内容密度 |

---

## 用户审核节点

以下内容需要用户确认后才能进入 HTML 制作：

1. **图文匹配**：每个场景引用的图片是否与旁白内容匹配？
2. **缺失素材**：标记为“ImageGen 生成”或“跳过”的场景是否可接受？
3. **时间节奏**：各场景时长分配是否合理？有没有太长或太短的？
4. **遗漏素材**：素材盘点中有哪些可用素材没有被分配？
5. **视觉风格**：深色/浅色主题选择、字号体系是否满意？

---

## config.json 生成（审核通过后执行）

分镜设计审核通过后，生成 config.json 作为渲染流水线输入：

```json
{
  "video_name": "{视频名}",
  "video_duration": {total_seconds},
  "voice": "zh-CN-YunxiNeural",
  "scenes": [
    {
      "id": "scene{N}",
      "narration": "{旁白全文}",
      "window": {start, end},
      "duration": {window_end - window_start}
    }
  ],
  "paths": {
    "html": "{project_name}/index.html",
    "output": "{output_path}"
  }
}
```

生成方式：
- 推荐：`python scene_config_generator.py --narrations narrations.txt --output config.json`
- 手写：AI 根据分镜设计的时间分配表直接生成

---

## 使用方式

```
Step 1: python asset_scanner.py --project {name}
Step 2: AI 阅读扫描结果 + SDL/旁白，填写本文档
Step 3: 用户审核分镜方案
Step 4: 审核通过 → AI 手写 HTML（参照 HyperFrames_HTML模板参考基准.md）
Step 5: 生成 config.json（scene_config_generator.py 或 AI 手写）
Step 6: preflight_check.py 预检 PASS
Step 7: pipeline_runner.py --config 渲染流水线
```
