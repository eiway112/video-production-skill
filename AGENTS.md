# AI视频制作工作流 - 智能体协作规范

> 本文件是发布仓根目录探测标记之一（`AGENTS.md` + `程序文件/` 同时存在），**文件名不可更改**。
> 非同构目录部署时，可改用环境变量 `VIDEO_WORKFLOW_ROOT` 显式指定根目录。

---

## 核心原则（Tier 1）

### 1. 单一权威源原则

**规范**：任何信息不得同时存在于多个地方。

| 信息类型 | 权威源 | 消费者 |
|---------|-------|--------|
| 场景定义 | `narration.json` | 编译器 → `_compiled_scenes.json` |
| 时间轴 | HTML `T-block` | GSAP代码 + `adjust_timeline.py` |
| 字幕 | `.srt` 文件 | 字幕后处理 + 交付验收 |
| 交付路径 | `config/delivery` | 交付脚本 + 完工报告 |

**实施**：
- 所有消费者通过 `_gsap_time_utils.py` 读取 T-block/S-block
- 配置不再 hardcode 场景，只声明 `narration_source` 指针
- 禁止"为方便"维护本地副本

### 2. 输入指纹与自动失效

**规范**：输入变化 → 指纹变化 → 相关步骤自动失效，强制重新执行

**机制**：
- 每个步骤记录输入的 SHA256 哈希（配置、旁白、HTML）
- 修改任一输入 → 步骤及其下游自动标记为 `invalidated`
- `should_rerun_step()` 检查指纹是否一致
- 缓存命中 ⇔ 输入未变 AND 步骤已通过

**代码位置**：`pipeline_state_fingerprint.py`

### 3. 产物真实性门禁

**规范**：所有"成功"都必须通过真实数据验证，禁止虚报

**三层门禁**：

| 层级 | 检查项 | 触发时机 |
|-----|--------|---------|
| TTS | 音频文件存在、非零、能解析、非静音 | step1_generate_tts() 末尾 |
| 媒体 | 视频/音频流、时长一致、字幕不溢出 | postprocess 前 |
| 报告 | 无硬编码 PASS，所有状态源自真实测量 | 交付阶段 |

**代码位置**：`verify_tts_product.py` + `media_qa_gate.py` + `generate_completion_report.py`

### 4. 完整依赖链强制

**规范**：HARD_GATES 定义了不可跳过的依赖关系

**关键约束**：
```
preflight → tts → timeline → render
                ↓          ↓
              verify      visual_check
                ↓          ↓
              postprocess → delivery
                ↓
          completion_report
```

**禁止**：
- render 不能跳过 tts（确保有音频）
- postprocess 不能跳过 visual_check（确保画面合规）
- 交付不能跳过 final_media_qa（确保质量）

---

## 操作规范（Tier 2）

### 场景数据模型

**必须字段**（所有场景）：
- `scene_id`：唯一标识（s0, s1, s2...）
- `type`：cover | content | transition
- `start`, `end`, `duration`：秒数
- `narration`：旁白文本（cover/transition 可为空）
- `narration_required`：布尔值

**可选字段**：
- `subtitle_required`: 是否需要字幕
- `assets`: 引用的图片文件
- `audio_file`: 对应的 TTS 音频文件

### HTML 场景结构契约

所有场景 div 必须声明：
```html
<div data-scene-id="s1" 
     data-scene-entry="gsap|static" 
     data-scene-subtitle-safe="true|false">
     <!-- 内容 -->
</div>
```

编译器自动输出这些属性，手写 HTML 需手动添加。

### 数值规范

**旁白与字幕中的数值**：一律使用阿拉伯数字
- ✅ 正确："30小时"、"5%"、"20元"
- ❌ 错误："三十小时"、"百分之五"

**例外**：仅保留成语（三心二意）与序数（第三），由 `narration_digits_rules.json` 管理

### 破坏性操作审核

**要求**：批量删除/覆盖前必须：
1. 列出所有涉及文件的路径
2. 确认无已交付内容被覆盖
3. Git 记录清晰

**工具**：`project_cleanup.py --execute` 提供事务式清理

---

## 工作流路由（Tier 3）

### 入口选择

| 输入类型 | 工具链 | 优先级 |
|---------|-------|--------|
| AI 导演模式（推荐） | 素材盘点 → 分镜 → HTML → 渲染 | ⭐⭐⭐ |
| HyperFrames HTML | HyperFrames → 渲染 | ⭐⭐ |
| PPT/Word | Doc2Markdown → HyperFrames → 渲染 | ⭐⭐ |
| Markdown 脚本 | OpenMontage → 渲染 | ⭐ |
| 网页 URL | website-to-hyperframes | ⭐ |

### 参照文档

- **技术基准**：`AI视频制作工作流模板/HyperFrames_HTML模板参考基准.md`
- **设计输出**：`AI视频制作工作流模板/分镜设计模板_AI导演模式.md`
- **踩坑清单**：`AI视频制作工作流模板/新路径实施_踩坑清单与防范检查表.md`

---

## 音画同步约束（Tier 3）

**原则**：场景窗口由 TTS 时长驱动，禁止手工塞入 dead-air

**实施**：
- `pipeline_runner.py` 默认对 `adjust_timeline.py` 传入 `--shrink`
- 自动收敛 window > TTS+margin 的场景
- 任一场景 dead-air 超过阈值（默认 3.0s）→ 拒收

**阈值表**：`config/quality/audio_sync_rules.json`

---

## 交付约束（Tier 3）

### 素材文件治理

**统一存放规则**：所有 AI 生成素材（ImageGen、渲染截图等）统一存放至 `素材文件/图片/`，**禁止**出现在项目根目录。

**ImageGen 输出处理**：
- 图像生成工具若默认输出到根目录 `vibe_images/`，生成后必须立即将图片移动至 `素材文件/图片/` 并删除 `vibe_images/` 空目录
- `.gitignore` 已兜底忽略 `vibe_images/`，但物理文件仍需清理
- 项目根目录不允许出现任何非规划目录（参见项目结构）

### 成果文件治理

**位置**：
- 视频 → `成果文件/视频/{项目名}.mp4`
- 字幕 → `成果文件/字幕/{项目名}.srt`

**命名**：
- ✅ 中文业务名：`{业务描述性名称}.mp4`
- ✅ 修订后缀：`{项目名}_修订01.mp4`
- ❌ 技术词：不允许 `final`, `v01`, `render`, `raw`, `tmp`

**约定**：
- 已交付文件不得覆盖（需修订时加后缀）
- 交付后用 `project_cleanup.py --execute` 清理中间产物
- 永不手工触碰成果文件

### 完工报告

**生成工具**：`generate_completion_report.py`

**数据来源**：
1. ffprobe 输出（视频元数据）
2. pipeline_state.json（流水线执行状态）
3. SRT 解析（字幕统计）
4. 文件系统（文件大小、修改时间）

**禁止**：任何硬编码的 "COMPLETED"、"PASS" 或 "成功"

---

## 环境与工具版本

**最小依赖**：
- Python >= 3.10
- FFmpeg >= 4.4（含 silencedetect）
- Node.js >= 18 LTS（HyperFrames）
- Git 2.0+

**安装**：
```bash
pip install -r 程序文件/requirements.txt
```

详细依赖与快速开始见 `README.md`。
