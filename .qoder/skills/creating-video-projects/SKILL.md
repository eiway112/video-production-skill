---
name: creating-video-projects
description: 通过现有视频制作工作流（Doc2Video → AI导演 → HyperFrames 流水线）从原始输入端到端创建视频项目。输入识别、工具链路由、分镜设计、HTML制作、流水线执行、门禁验收全流程编排。当用户要求"制作视频"、"创建视频项目"、"把PPT/Word/文档/脚本做成视频"、"渲染视频"时使用。
---

# 视频项目创建 — 端到端编排协议

## 适用范围（v1）

- ✅ 主路径：文档(PPT/Word/MD) → AI导演模式 → HyperFrames 渲染（已被 21 支交付视频验证）
- ✅ 直通路径：已有 HTML+Config 项目直接进流水线
- ❌ OpenMontage 路径（clip-factory/podcast/cinematic 等）：**本 Skill 不承诺**，未经本地端到端验证；如用户明确要求，告知实验性状态后走 `pipeline_runner.py --engine openmontage`

## 权威参照（先读后做，禁止凭记忆复制其内容）

| 文档 | 用途 | 消费时机 |
|------|------|---------|
| `AGENTS.md` | 核心原则、交付治理、命名规范 | 全程 |
| `AI视频制作工作流模板/新路径实施_踩坑清单与防范检查表.md` | 阶段A-D检查表、参数基准、决策树 | 阶段B/C 必读 |
| `AI视频制作工作流模板/HyperFrames_HTML模板参考基准.md` | HTML+GSAP 技术基准 | 阶段C 必读 |
| `AI视频制作工作流模板/分镜设计模板_AI导演模式.md` | 分镜输出格式 | 阶段B 必读 |

## 输入识别与路由（AI 语义判断，不依赖扩展名）

| 输入特征 | 路由 | 入口命令 |
|---------|------|---------|
| .pptx / .docx 文档 | 阶段0→A→B→C→D 全流程 | `doc_to_markdown.py` 起步 |
| Markdown / 文本脚本 | 跳过转换，从阶段A素材盘点开始 | — |
| 已有 HTML 项目（`程序文件/源码/hyperframes/<项目>/` 存在 index.html） | 直通阶段D | `pipeline_runner.py --config <配置>` |
| SDL YAML | 直通阶段D 一键入口 | `pipeline_runner.py --sdl <yaml>` |
| 语义模糊（如"把这个做成视频"但意图不明） | **暂停**，列出待确认项问用户 | — |

## 执行流程

所有命令在 `程序文件/脚本/` 下用 venv Python 执行：
`<工作流根目录>/程序文件/运行环境/venv/Scripts/python.exe`（脚本内部经 `_script_env.py` 自动探测根目录，异机可用环境变量 `VIDEO_WORKFLOW_ROOT` 显式指定）

### 阶段0：环境预检（代码执行）

```
python config_manager.py
```
`validate()` 返回问题非空 → 先修复环境再继续。

### 阶段A：输入转换 + 素材盘点（代码执行）

1. 文档转换（输出到过程产物，不污染素材目录）：
   ```
   python doc_to_markdown.py "<输入文档>" -o "过程产物/临时产物/<项目名>_md/<项目名>.md"
   ```
   提取的图片自动落在同目录 `images/` 下。
2. 素材盘点：按踩坑清单 §6.1 生成清单（图片数量/尺寸/比例、内容摘要、无素材场景标注、未使用素材确认）。图片比例用 ffprobe 或 PIL 实测，禁止目测。

### 阶段B：分镜设计（AI 导演判断 + 用户确认节点）

1. 按分镜设计模板输出：场景划分、每场景画面构成、时间分配（TTS 占 60-75%）、素材引用表（场景号→文件名→展示方式）。
2. 旁白撰写约束：数值一律阿拉伯数字（`narration_digits_rules.json` 管理例外）；语义完整不碎片化。
3. **用户确认节点（阻塞）**：按踩坑清单 §6.2 一次性提交分镜表待确认——图文匹配、时间节奏、遗漏素材。这是全流程唯一的强制人工节点，其余环节不中断用户。

### 阶段C：项目文件制作（AI 手写）

产出物落在 `程序文件/源码/hyperframes/<项目名>/`（项目名用英文短横线风格）：

1. `narration.json` — 场景单一权威源。结构参照现有成功项目（如 `quickstart-demo/narration.json`）：scene_id/title/start/end/duration/type(cover|gsap|static)/narration。
2. 流水线配置 JSON → `程序文件/配置/config/pipelines/<项目名>.json`。参照 `config/quickstart-demo.json` 结构：paths(html_project/video_name/subtitle_name/temp_subdir)、video_duration、narration_source 指向 `./narration.json`、audio（qwen 引擎音色如 `longanling`；禁止 Edge 格式音色 zh-CN-*Neural 搭配 qwen 引擎，TTS 步会报错拒收）、fps/resolution 声明必须与渲染器实际输出一致（当前渲染器输出 25fps，media_qa_gate 检查13会实测回比）、delivery（中文业务名，禁止 final/v01/render/raw/tmp）。
3. `index.html` — 手写 HTML+GSAP。**完成后逐项核对踩坑清单"阶段C检查表"全部条目**（占位符、subtitle-safe 高度、装饰层 opacity、字号分层、无 >3s 静止、动画必须挂在主 timeline `tl` 上等），并对照 HTML 模板参考基准。
4. 场景 div 必须声明 `data-scene-id` / `data-scene-entry` / `data-scene-subtitle-safe`（AGENTS.md HTML 契约）。

### 阶段D：流水线执行 + 交付（代码执行）

1. 全链执行（首次/验收运行必须 `--fresh`，禁止信任旧状态）：
   ```
   python pipeline_runner.py --config <项目名>.json --fresh
   ```
   门禁自动继承：preflight → tts → timeline → preview（硬阻断）→ render → verify → visual_check → postprocess。**禁止用 --force 绕过门禁**。
   - TTS 生成并发数由 `程序文件/配置/config/system/hyperframes_config.json` 的 `tts_concurrency` 控制（默认 3，硬上限 5，设 1 为串行；读取失败回退 3 并告警）；并发仅作用于 TTS 网络生成阶段，失败场景自动串行兜底重试一次。
   - 渲染期间的 `[RENDER]` 前缀进度/心跳日志来自旁路观察者线程，纯观测输出，不改变渲染控制流与退出码。
2. 失败处理：修复后 `--resume`（自动回退到最早失效步）；字幕/BGM 类修改用 `--quick-fix`。注意 `--quick-fix` 会将渲染链前置步骤标记 skipped，delivery 硬门禁必然拦截（错误码 `QUICKFIX_BLOCKED`）——仅用于快速排查，不产生可交付产物，交付前须跑完整流水线。同一问题修复超过 3 次 → 停止，输出阻塞报告等用户方向。
3. 交付验收：
   ```
   python generate_completion_report.py（按脚本 --help 传参）
   ```
   报告数据必须源自 ffprobe/pipeline_state/SRT 真实测量。
4. 收尾：确认 `成果文件/视频/`、`成果文件/字幕/` 产物就位（路径前置展示给用户）→ `project_cleanup.py --execute` 清理中间产物。已交付文件永不覆盖，修订加 `_修订NN` 后缀。

## 自评量表（阶段D完成后逐项打分，任一项 <3 不得声明完成）

| 维度 | 5分标准 |
|------|--------|
| 端到端真实性 | mp4 由本次流水线真实产出，pipeline_state.json 全步 passed 且带指纹 |
| 门禁完整性 | 无 --force，preview/verify/visual_check/postprocess 全部真实通过 |
| 交付合规 | 中文业务命名、srt 同名、无覆盖已交付文件 |
| 分镜忠实度 | 成片与用户确认的分镜表一致，无擅自增删场景 |
| 收尾完成度 | 完工报告基于真实测量 + 中间产物已清理 |

## 常见陷阱

- **描述替代运行**：任何"已完成"声明必须附真实命令输出，禁止描述"应该会成功"。
- **跳过用户分镜确认**直接写 HTML → 图文错配返工成本极高。
- **绕过 doc_to_markdown 直接"看图说话"**写旁白 → 丢失文档表格/层级信息。
- **配置里手工复制场景数据**而不指向 narration.json → 违反单一权威源，改一处漏一处。
- **在素材目录或项目根目录生成中间文件** → 一律进 `过程产物/临时产物/`。
- **渲染前不跑 preview 就 render** → 11 分钟渲染浪费在本可静态发现的问题上。
- **visual_check 为三点采样**（场景时长 30%/50%/70%，取最坏值判定），比旧两点采样更严；旧项目边缘内容可能由 PASS 变 FAIL，属预期质量增强，应修内容而非绕门禁。
