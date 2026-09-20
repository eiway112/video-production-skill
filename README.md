# AI视频制作工作流

从旁白脚本到成片的自动化视频生产流水线：单一权威源、真实性门禁、时间轴自动收敛。配套 Qoder Skill（`creating-video-projects`），可由 AI 智能体端到端驱动。

## 功能特性

- **HyperFrames HTML → MP4**：GSAP 动画场景 + TTS 旁白 + 字幕，一条命令出片
- **单一权威源**：旁白只在 `narration.json` 定义一处，配置通过 `narration_source` 指针消费
- **真实性门禁**：TTS 产物 7 层验证、媒体 11 项质量检查、数据驱动完工报告，杜绝虚报成功
- **输入指纹**：SHA256 指纹驱动步骤缓存与自动失效，改输入即重跑
- **回归测试**：18 个用例覆盖关键链路，异机部署验收核心

## 环境依赖

| 依赖 | 版本 | 安装方式 | 说明 |
|------|------|---------|------|
| Python | ≥ 3.10 | 官方安装包 | `pip install -r 程序文件/requirements.txt` |
| FFmpeg（含 ffprobe） | ≥ 4.4 | 需在 PATH | silencedetect 过滤器必需 |
| Node.js | ≥ 18 LTS | 官方安装包 | |
| hyperframes 渲染器 | 最新 | `npm install -g hyperframes` | 全局 npm 包，流水线经 `npx hyperframes render` 调用 |
| Chrome | 稳定版 | 官方安装包 | 预览与渲染载体 |
| DashScope API Key | — | 用户自备（可选） | qwen TTS 引擎；无 Key 时可用离线/Edge 路径 |
| GSAP（随仓 vendored） | 3.14.2 | 无需安装，随仓携带于 `程序文件/源码/hyperframes/quickstart-demo/gsap.min.js` | GreenSock Standard License（https://gsap.com/standard-license ），允许开源项目随附使用 |

### API Key 配置（可选）

如需使用 qwen（CosyVoice）TTS，将真实 Key 写入 `程序文件/配置/openmontage.env.local`（已被 `.gitignore` 保护，**切勿**写入 `openmontage.env` 提交入库）：

```
DASHSCOPE_API_KEY=sk-你的真实Key
```

不配置 Key 时，在项目 config.json 声明 `"tts_engine": "edge"` 使用免费的 Edge-TTS。

## 快速开始

```bash
# 1. 安装 Python 依赖
pip install -r 程序文件/requirements.txt

# 2. 安装渲染器
npm install -g hyperframes

# 3. 跑回归测试验证环境（预期 18/18 通过）
python 程序文件/脚本/regression_test.py --run-all

# 4. 端到端渲染示例项目
python 程序文件/脚本/pipeline_runner.py --config quickstart-demo.json --fresh
```

成片输出至 `成果文件/视频/quickstart-demo.mp4`，字幕输出至 `成果文件/字幕/quickstart-demo.srt`。

## 目录结构

```
<仓根>/
├── AGENTS.md                        # 智能体协作规范（根目录探测标记①，勿改名）
├── .qoder/skills/creating-video-projects/SKILL.md   # Qoder Skill 入口
├── AI视频制作工作流模板/            # 分镜/HTML/交付模板文档
├── 程序文件/                        # 根目录探测标记②
│   ├── requirements.txt
│   ├── 脚本/                        # 28 个流水线与门禁脚本
│   ├── 配置/                        # 质量阈值、校验规则、TTS 环境
│   └── 源码/hyperframes/quickstart-demo/   # 最小示例项目
├── 素材文件/图片/                   # 用户素材（不入库）
├── 过程产物/临时产物/               # 中间产物（不入库）
└── 成果文件/视频/ + 成果文件/字幕/  # 交付产物（不入库）
```

> 部署到非同构目录时，设置环境变量 `VIDEO_WORKFLOW_ROOT` 显式指定仓根；根探测失败会直接 RuntimeError 报错。

## Skill 使用说明

本仓内置 Qoder Skill `creating-video-projects`（`.qoder/skills/creating-video-projects/SKILL.md`）。在 Qoder 中打开本仓后，直接对智能体说"帮我把这份文档做成视频"即可触发；Skill 会引导智能体走 分镜设计 → HTML 制作 → TTS → 渲染 → 质量门禁 的标准链路。

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
