# AI视频制作工作流

从旁白脚本到成片的自动化视频生产流水线：单一权威源、真实性门禁、时间轴自动收敛。以 `AGENTS.md` 为通用协作规范，任何能读写文件的 AI 编码智能体（或纯手工命令行）均可端到端驱动；仓内附带跨平台智能体使用指南与可移植 Skill 定义。

## 功能特性

- **HyperFrames HTML → MP4**：GSAP 动画场景 + TTS 旁白 + 字幕，一条命令出片
- **单一权威源**：旁白只在 `narration.json` 定义一处，配置通过 `narration_source` 指针消费
- **真实性门禁**：TTS 产物 7 层验证、媒体全量四态质量检查、数据驱动完工报告，杜绝虚报成功
- **输入指纹**：SHA256 指纹驱动步骤缓存与自动失效，改输入即重跑
- **回归测试**：全套用例覆盖关键链路（数量与通过数以 `--run-all` 实测为准），异机部署验收核心

## 环境依赖

| 依赖 | 版本 | 安装方式 | 说明 |
|------|------|---------|------|
| Python | ≥ 3.10 | 官方安装包 | `pip install -r 程序文件/requirements.txt` |
| FFmpeg（含 ffprobe） | ≥ 4.4 | 需在 PATH | silencedetect 过滤器必需 |
| Node.js | ≥ 18 LTS | 官方安装包 | |
| hyperframes 渲染器 | 最新 | `npm install -g hyperframes` | 全局 npm 包，流水线经 `npx hyperframes render` 调用 |
| Chrome | 稳定版 | 官方安装包 | 预览与渲染载体 |
| DashScope API Key | — | 用户自备（可选） | qwen TTS 主引擎，正式交付必需；无 Key 时可显式声明 Edge-TTS 做零成本环境自检 |
| GSAP（随仓 vendored） | 3.14.2 | 无需安装，随仓携带于 `程序文件/源码/hyperframes/quickstart-demo/gsap.min.js` | GreenSock Standard License（https://gsap.com/standard-license ），允许开源项目随附使用 |

### API Key 配置（可选）

如需使用 qwen（CosyVoice）TTS，将真实 Key 写入 `程序文件/配置/openmontage.env.local`（已被 `.gitignore` 保护，**切勿**写入 `openmontage.env` 提交入库）：

```
DASHSCOPE_API_KEY=<阿里云百炼控制台获取的 API Key>
```

不配置 Key 时，可在项目 config.json 显式声明 `"tts_engine": "edge"`，用免费的 Edge-TTS 跑通全流程自检。此为声明式例外（流水线禁止跨引擎自动降级），正式交付仍以 Qwen（CosyVoice）主引擎为准，见 `AGENTS.md` TTS 引擎纪律。

## 快速开始

```bash
# 1. 安装 Python 依赖
pip install -r 程序文件/requirements.txt

# 2. 安装渲染器
npm install -g hyperframes

# 3. 跑回归测试验证环境（预期全部通过、0 失败）
python 程序文件/脚本/regression_test.py --run-all

# 4. 端到端渲染示例项目
python 程序文件/脚本/pipeline_runner.py --config quickstart-demo.json --fresh
```

成片输出至 `成果文件/视频/quickstart-demo.mp4`，字幕输出至 `成果文件/字幕/quickstart-demo.srt`。

## 目录结构

```
<仓根>/
├── AGENTS.md                        # 智能体协作规范（根目录探测标记①，勿改名）
├── .qoder/skills/creating-video-projects/SKILL.md   # 智能体 Skill 定义（流程文档，可移植至任意智能体运行时）
├── AI视频制作工作流模板/            # 分镜/HTML/交付模板文档
├── 程序文件/                        # 根目录探测标记②
│   ├── requirements.txt
│   ├── 脚本/                        # 流水线与门禁脚本（清单以目录实测为准）
│   ├── 配置/                        # 质量阈值、校验规则、TTS 环境
│   └── 源码/hyperframes/quickstart-demo/   # 最小示例项目
├── 素材文件/图片/                   # 用户素材（不入库）
├── 过程产物/临时产物/               # 中间产物（不入库）
└── 成果文件/视频/ + 成果文件/字幕/  # 交付产物（不入库）
```

> 部署到非同构目录时，设置环境变量 `VIDEO_WORKFLOW_ROOT` 显式指定仓根；根探测失败会直接 RuntimeError 报错。

## 智能体驱动说明

本仓的端到端流程不绑定任何特定智能体平台：

- **通用路径（任意 AI 编码智能体）**：在 Qoder、Claude Code、Cursor、Codex CLI 等任一能读写文件的智能体中打开本仓，让其先读 `AGENTS.md`（协作规范）与 `.qoder/skills/creating-video-projects/SKILL.md`（流程定义，路径为 Qoder 约定位置，内容本身平台无关），然后提出"帮我把这份文档做成视频"即可触发 分镜设计 → HTML 制作 → TTS → 渲染 → 质量门禁 的标准链路。
- **手工路径（无智能体）**：按「新建自己的项目」一节逐步执行 `pipeline_runner.py`，同样可完整出片。

关键参照文档：

- 技术基准：`AI视频制作工作流模板/HyperFrames_HTML模板参考基准.md`
- 分镜设计：`AI视频制作工作流模板/分镜设计模板_AI导演模式.md`
- 避坑清单：`AI视频制作工作流模板/新路径实施_踩坑清单与防范检查表.md`
- 协作规范：`AGENTS.md`（Tier 1-3 强制约束）

## 新建自己的项目

1. 复制 `程序文件/源码/hyperframes/quickstart-demo/` 为新项目目录，改写 `narration.json`（权威旁白源）与 `index.html`（GSAP 场景）
2. 复制 `程序文件/配置/config/quickstart-demo.json` 为 `<项目名>.json`，同步改 `paths` 与保留 `narration_source` 指针
3. 运行 `python 程序文件/脚本/pipeline_runner.py --config <项目名>.json --fresh`

## 许可证

[MIT](LICENSE)
