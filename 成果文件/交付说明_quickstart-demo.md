# 交付说明_quickstart-demo

## 交付文件清单

| 文件名 | 用途 | 画幅 | 时长 | 声音版本 |
|---|---|---|---|---|
| quickstart-demo.mp4 | 工作流快速上手演示 | 1920×1080 | 00:47 | 有声版 |
| quickstart-demo.srt | 配套字幕（5 条） | — | — | — |

## 播放验证（ffprobe 实测）

| 项目 | 值 |
|---|---|
| 视频编码 | H.264 |
| 分辨率 | 1920×1080 |
| 帧率 | 25fps |
| 音频编码 | AAC |
| 采样率 | 44100Hz |
| 声道 | 立体声 |
| 文件大小 | 2.99 MB |

## 制作信息

- **工具链**: HyperFrames
- **HTML 项目**: quickstart-demo
- **配置文件**: quickstart-demo.json
- **场景数**: 5（内容场景）+ 封面
- **时长**: 46.92s（config 声明 46.9s，一致性偏差 0.02s）
- **TTS 语音**: qwen / longanling_v3（rate +5%, pitch +0Hz）
- **渲染日期**: 2026-09-21

## 交付背书

- 终检 `media_qa_gate`：verdict=PASS（PASS=20 / FAIL=0 / UNTESTED=0 / NOT_APPLICABLE=0）
- 完工报告：Status=VALIDATED，15/15 checks passed
- 渲染成本预算：`--accept-over-render` 显式放行并留痕（第 3/3 次全量渲染达 soft 上限）

## 备注

本说明由发布仓侧端到端验收运行产出，成片实测值以 `成果文件/交付登记.json` 为交付态权威台账。

## 字幕时间戳来源（必填，主路径命中率）

Subtitle timestamp source: asr_forced 5/5 (100%) | punct_gap 0 | char_prop 0 — 阈值 ≥80%

数据源：`media_qa_gate` 检查项 `subtitle_timestamp_source`（终检报告行 `Subtitle timestamp source:` 与完工报告 `data_sources.subtitle_timestamp_source` 同源，取数面是 step5 逐场实测落盘的 `temp/_subtitle_timestamp_source.json`）。本节由 `pipeline_runner` 在完工报告 VALIDATED 后自动写入，是该结论的唯一版本化留痕面——`过程产物/` 与项目 temp 不入 git，日志行在交付后不可追溯。
