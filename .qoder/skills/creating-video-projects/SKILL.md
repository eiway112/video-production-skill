---
name: creating-video-projects
description: 通过现有视频制作工作流（Doc2Video → AI导演 → HyperFrames 流水线）从原始输入端到端创建视频项目。输入识别、工具链路由、分镜设计、HTML制作、流水线执行、门禁验收全流程编排。当用户要求"制作视频"、"创建视频项目"、"把PPT/Word/文档/脚本做成视频"、"渲染视频"时使用。
---

# 视频项目创建 — 端到端编排协议

## 前置条件（先读这一节）

**本 Skill 是编排协议，不是独立工具**：包内只有本文件，不含任何脚本。下文所有命令都住在视频制作工作流仓的 `程序文件/脚本/` 下——未取到该仓时，阶段0 的 `python config_manager.py` 即报 `FileNotFoundError`。**属环境未就绪，不得据此判本 Skill 失效，也不得据此改判据。**

工作流仓为公开仓，GitHub 与 Gitee 双端点同名同内容：

- `https://github.com/eiway112/video-production-skill`
- `https://gitee.com/eiway112/video-production-skill`

克隆后按该仓 `README.md` 的安装段配置环境（Python / FFmpeg / Node + hyperframes 渲染器 / Chrome / TTS 凭据）。**版本号与安装命令一律以该 README 为权威源，本文件不复制**——渲染器版本钉住过也升过，抄进这里即随源件过期。该仓同时携带下文「权威参照」表列出的 4 份文档。

## 适用范围（v1）

- ✅ 主路径：文档(PPT/Word/MD) → AI导演模式 → HyperFrames 渲染（已被 21 支交付视频验证）
- ✅ 直通路径：已有 HTML+Config 项目直接进流水线
- ❌ OpenMontage 路径（clip-factory/podcast/cinematic 等）：**本 Skill 不承诺**，未经本地端到端验证；如用户明确要求，告知实验性状态后走 `pipeline_runner.py --engine openmontage`

## 权威参照（先读后做，禁止凭记忆复制其内容）

| 文档 | 用途 | 消费时机 |
|------|------|---------|
| `AGENTS.md` | 核心原则、交付治理、命名规范 | 全程 |
| `AI视频制作工作流模板/新项目接手简报_20261105.md` | 画面永动三条路线、不做项、TTS 纪律 | **阶段B 必读**（开新线一律按它起手） |
| `AI视频制作工作流模板/新路径实施_踩坑清单与防范检查表.md` | 阶段A-D检查表、参数基准、决策树 | 阶段B/C 必读 |
| `AI视频制作工作流模板/HyperFrames_HTML模板参考基准.md` | HTML+GSAP 技术基准 | 阶段C 必读 |
| `AI视频制作工作流模板/分镜设计模板_AI导演模式.md` | 分镜输出格式 | 阶段B 必读 |

## 输入识别与路由（AI 语义判断，不依赖扩展名）

| 输入特征 | 路由 | 入口命令 |
|---------|------|---------|
| .pptx / .docx 文档 | 阶段0→A→B→C→D 全流程 | `doc_to_markdown.py` 起步 |
| HTML **源文档**（技术手册/文档站/长文，即输入是"要讲的内容"） | 跳过 `doc_to_markdown.py`，从阶段A素材盘点开始 | `markitdown` 转换，见阶段A 第 1 步末注 |
| Markdown / 文本脚本 | 跳过转换，从阶段A素材盘点开始 | — |
| 已有 HTML **项目**（`程序文件/源码/hyperframes/<项目>/` 存在 index.html，即输入是"要渲染的分镜"） | 直通阶段D | `pipeline_runner.py --config <配置>` |
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

   **输入是 HTML 源文档时不走 `doc_to_markdown.py`**：其 `converters` 仅注册 `.pptx`/`.docx`，喂 HTML 会 `ValueError: Unsupported format`。改用 venv 内的 `markitdown`：

   ```
   python -c "from markitdown import MarkItDown as M; print(M().convert(r'<输入HTML>').text_content)" > "过程产物/临时产物/<项目名>_md/<项目名>.md"
   ```

   转完必须做正文完整性核验，**不得按输出体量判成败**：2026-09-26 以一份 320KB 级产品技术手册实测，源 320,068 B 转出正文仅 7,176 字符（UTF-8 计 15,086 B，重定向落盘 15,447 B，差额＝每行一个 CR）——这个"缩水"是对的，页面大头是 UI 脚本与内联图片。核验口径是**结构量逐档比对**而非字节比：同测源 `<h2>` 9 个 → 产物 `^## ` 9 行、`<h3>` 20 → `^### ` 20 行，另抽样比对表格数值。计数须用行首锚 `^`，漏锚会把行内 `## ` 算进来（实测同一文件漏锚读成 43/34）。缺档即判转换不可信，回退人工抽取并留来源指针。

   **但"逐档相等"在源静态结构量本就为 0 时是空判据**——源 `<h2>` 为 0、产物 `^## ` 亦为 0，读起来像通过，实际什么都没抽到。2026-10-15 一份 3.67 MB 的客户端渲染文档站即此形态：静态标签普查 `<h1>/<h2>/<table>` 全 0、`<script>` 1 个，`markitdown` 转出 0 字符。该 HTML 输入形态已两次出事（前一次为 320 KB 手册转出偏小），属会复发路径，按五步取回正文：

   1. **静态标签普查**（`<h1>`/`<h2>`/`<table>` 计数）。全 0 即判"正文不在静态 DOM"——既不得判转换失败，也不得因"两边都是 0"判通过，须继续走第 2 步。
   2. **脚本内声明普查**。同一 `<script>` 里常同时住渲染库与正文（实测 3.49 MB 的 `__MERMAID_LIB__` 与 77 KB 的 `__CONTENT_HTML__` 并存），**按变量名与内容形态挑，不得按体量挑**。
   3. **JS 字符串字面量解码**取正文，须处理 `\uXXXX` / `\u{...}` / `\xNN` / 行延续 / 引号转义五类转义。**解码器落盘为脚本文件执行，不内联进 `python -c`**——反引号会被 shell 吞掉（实测 Exit 2）。
   4. 解出的 HTML **再转 Markdown**。
   5. 上述结构量逐档比对的**基准换成第 3 步抽出的那份 HTML**（源静态量作不得基准）。实测该件：h1/h2/h3/table 四档与产物逐档相等、50 表 310 行存活，核验方才成立。
2. 素材盘点：按踩坑清单 §6.1 生成清单（图片数量/尺寸/比例、内容摘要、无素材场景标注、未使用素材确认）。盘点根是素材库 `素材文件/图片/`，不是项目空 `photos/`——项目起步时后者恒为空，只扫它会得出"无素材"结论并直接跳到 ImageGen。图片比例用 ffprobe 或 PIL 实测，禁止目测。

### 阶段B：分镜设计（AI 导演判断 + 用户确认节点）

1. 按分镜设计模板输出：场景划分、每场景画面构成、**场间承接**（每场从上一场哪个图元／命名／位置长出来）、时间分配（窗口 = TTS + 场景余量 + 转场，**TTS 占比是派生量不是设计目标**，口径见踩坑清单 §八·补）、素材引用表（场景号→文件名→展示方式）、**动效编排声明（逐场必填）**。
   **动效编排声明**：每场在时间分配总表的「持续动效」列写明所用路线（A1 相机微位移 / A2 信息层微动 / A3 粒子层 / B 装饰层呼吸 / 组合），并答一句"入场之后这几秒里什么在动"。写不出＝该场只有静态排版，须补信息层强调形态。刻意静态的场（如纯数据对比表）须写"静态＋理由"。路线定义与可抄骨架住接手简报§五与踩坑清单§八，此处不复述。**缺此列的分镜表不完整，不得提交用户确认节点。**
   **强调形态声明**：同表「强调形态」列逐场必填（`chip`／`callout`／`hud`／组合，未用写 `—`）。取值同值写入 `narration.json` 的 `emphasis_forms` 与场景 div 的 `data-emphasis`，预检 check 7h 对撞（WARN 级）。三件套骨架与契约住 HTML 基准 §4.9；**缺此列的分镜表不完整，不得提交用户确认节点**（与「动效编排声明」同一条门禁）。
   **点亮锚词声明**：同表「点亮锚词」列逐场必填——枚举类内容（"4 类部件""5 层"这类逐项展开）逐词给锚词，非枚举写 `—`。锚词只进 HTML 的 `data-cue-at`（机制与写法住 HTML 基准 §3.6），CUE-MAP 是唯一权威源，**不得另建 narration 字段**。**缺此列的分镜表不完整，不得提交用户确认节点**（与「强调形态声明」同一条门禁）。
   **画面绑定声明（仅"一行多列靠位置对应"的画面需要）**：凡表格行／对比行／参数 HUD 的值来自某条事实，就在 `facts.json` 该条事实的 `visual_bindings` 里声明它落在哪一场哪一行（定位两式、`key` 整词匹配与"只证声明值在声明行、不证该行语义正确"这条边界住分镜模板「画面绑定 `visual_bindings`」节，此处不复述）。预检 check 13c 逐行对撞；未声明则不产出该项读数（计数 0），是否漏声明由作者按分镜模板核对——**不得把“未声明”读成“已核对”**。存在理由：2026-11-26 一支交付的对比表把密度错绑到另一行的吸声系数、另一张表整列厚度缺失，旁白与台账都对，渲染前全链门禁全绿。
2. 旁白撰写约束：数值一律阿拉伯数字（`narration_digits_rules.json` 管理例外）；语义完整不碎片化；过一遍分镜模板「叙事链自检」四项与「画面节奏自检」四项（每句至少一个焦点／长句与空档要有第二拍／入场≠持续／焦点保持到下一句；判据住该节，此处不复述）。
3. **用户确认节点（阻塞）**：按踩坑清单 §6.2 一次性提交分镜表待确认——图文匹配、时间节奏、遗漏素材。这是全流程唯一的强制人工节点，其余环节不中断用户。

### 阶段C：项目文件制作（AI 手写）

产出物落在 `程序文件/源码/hyperframes/<项目名>/`（项目名用英文短横线风格）：

1. `narration.json` — 场景单一权威源。结构参照随包夹具（如 `quickstart-demo/narration.json`）：scene_id/title/start/end/duration/type(cover|gsap|static)/narration。
2. 流水线配置 JSON → `程序文件/配置/config/<项目名>.json`（随包示例：`quickstart-demo.json`；`config/pipelines/` 是开发仓历史落点，不随包）。参照 `quickstart-demo.json` 结构：paths(html_project/video_name/subtitle_name/temp_subdir)、video_duration、narration_source 指向 `./narration.json`、audio（**缺省引擎是 ElevenLabs**：不写 `tts_engine` 即用它，需 `ELEVENLABS_API_KEY`，`voice` 传 voice_id；qwen 须显式 `tts_engine: "qwen"` 并选配套音色，禁止 Edge 格式音色 zh-CN-*Neural 搭配 qwen；ElevenLabs 不支持 SSML `<break>`，带 `pause_marks` 的项目改用该引擎时须删声明，详见 AGENTS.md TTS 引擎纪律）、fps/resolution 声明必须与渲染器实际输出一致（当前渲染器输出 25fps，media_qa_gate 检查13会实测回比）、delivery（中文业务名，禁止 final/v01/render/raw/tmp）。
3. `index.html` — 手写 HTML+GSAP。**完成后逐项核对踩坑清单"阶段C检查表"全部条目**（占位符、subtitle-safe 高度、装饰层 opacity、字号分层、无 >3s 静止、动画必须挂在主 timeline `tl` 上等），并对照 HTML 模板参考基准。**遮挡自查**：预览报告的 `Box Overlap` 节会逐条点名"绘制盒压在他人字形上"（只点名不阻断），有点名即改布局让位、不得靠抬 `z-index` 解决；判据本体住 HTML 基准 §4.9 共用纪律 5，此处不复述阈值。**裁切自查**：preview 步另跑一次容器裁切探针（零渲染，量 `overflow:hidden/clip` 祖先把内容裁掉多少像素），读数落 `temp/_scene_overflow_check.json` 并留痕 `state.scene_overflow_check`；印 `[SCENE-OVERLAP-WARN]` 即逐场复核——**该项只告警不阻断**（存量项目有同类读数，升阻断会误杀既有交付），但新写的 HTML 出现裁切就是缺陷，改布局而不是忽略告警。
4. 场景 div 必须声明 `data-scene-id` / `data-scene-entry` / `data-scene-subtitle-safe`（AGENTS.md HTML 契约）。
5. 节奏面：每个 cue 不只负责点亮，还要把焦点保持到下一句（`cueHi` + `flushCuePlan`，骨架与四条原因住模板基准 §3.6.2；保持区间用有限次呼吸铺满，不得用 `repeat:-1`）。入场动画是一次性的（约 0.5s），旁白是持续的——入了场就不再变化，会被观众读成"画面卡住"。**自查**：预览报告 `[CHOREO-PREVIEW]` 的 cue 锚条数与预检 CUE-MAP 条数须与分镜「点亮锚词」列对得上（三处不一致先查锚词是否真写进了 `data-cue-at`）。

### 阶段D：流水线执行 + 交付（代码执行）

1. 首次执行入口：
   ```bash
   python pipeline_runner.py --config <项目名>.json --fresh
   ```
   修订轮用 `--resume`；长任务后台执行并等待完成通知。恢复、预算、增量渲染和交付槽位纪律只指向 `agents.md`「错误恢复纪律」，此处不复述。
2. 失败处理入口：定点修复后 `--resume`；字幕/BGM 排查可用 `--quick-fix`，但它不产生可交付产物。禁止用 `--force` 绕过门禁。同一问题修复超过 3 次仍无改善 → 停止，输出阻塞报告等用户方向。
3. 唯一验收入口：pipeline 的 `completion_report` 步。该步执行 `generate_completion_report.py --audit --config-file`，技术报告通过后才写交付登记，再投影四轴状态；不得对同一报告重复后置审计。
4. 停止条件：普通内容制作在四轴投影后停止；只有共享 Harness 变更才进入工程验收。工程证据失配先重审并显式 `--rebaseline`，失败投影不推进旧基线。渲染后冻结共享代码和配置，阻断交付的问题集中修复并只重渲一次。外部协议强制 L3 时只对最终交付物和最终代码差异执行一次。
5. 收尾：确认视频与字幕就位并展示路径，再按既有清理入口处理中间产物；已交付文件不覆盖，修订加 `_修订NN`。

## 技术审计与验收出口

- 六维技术审计只在 `completion_report` 步执行；定义与失败处置以完工报告工具和 `agents.md`「执行模式与四轴验收」为单源指针。
- 工程验收的独立复核与治理证据由主智能体在 pipeline 外提供；项目任务只消费 `scope=project` 的体检报告，production 不读取治理三件套。治理 FAIL 不回写视频交付事实，技术 FAIL 不得登记交付。
- 同一证据只验收一次；变化、缺失和复用边界由 `delivery_acceptance.py` 的 `evidence_binding` 裁定。

## 常见陷阱

- **描述替代运行**：任何"已完成"声明必须附真实命令输出，禁止描述"应该会成功"。
- **跳过用户分镜确认**直接写 HTML → 图文错配返工成本极高。
- **绕过 doc_to_markdown 直接"看图说话"**写旁白 → 丢失文档表格/层级信息。
- **配置里手工复制场景数据**而不指向 narration.json → 违反单一权威源，改一处漏一处。
- **在素材目录或项目根目录生成中间文件** → 一律进 `过程产物/临时产物/`。
- **渲染前不跑 preview 就 render** → 11 分钟渲染浪费在本可静态发现的问题上。
