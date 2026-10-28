# 交付说明_{主题}

## 交付文件清单

| 文件名 | 用途 | 画幅 | 时长 | 声音版本 |
|---|---|---|---|---|
| {主题}_{声音版本}.mp4 | {产品宣传/培训/复盘} | 1920×1080 | {MM:SS} | {有声版/静音版/纯音乐版} |
| {主题}.srt | 配套字幕 | — | — | — |

## 播放验证

| 项目 | 值 |
|---|---|
| 视频编码 | H.264 |
| 分辨率 | 1920×1080 |
| 帧率 | 30fps |
| 音频编码 | AAC 256kbps |
| 采样率 | 44100Hz |
| 声道 | 立体声 |
| 文件大小 | {XX.X} MB |

## 制作信息

- **工具链**: {HyperFrames / OpenMontage}
- **HTML 项目**: {项目目录名}
- **配置文件**: {config}.json
- **场景数**: {N}
- **TTS 语音**: {voice}
- **渲染日期**: {YYYY-MM-DD}

## 零渲染前置复核

各门禁的当轮读数由执行者从流水线输出抄录；**本表是这些读数唯一的版本化留痕面**——
`temp/`、`_pause_audit.json`、`completion_report.json` 与流水线日志全在 `.gitignore` 的
`过程产物/` 之下，交付后不可追溯。留空即等于"没跑"，不得事后凭印象补填。

| 判据 | 读数 |
|---|---|
| 预检 `preflight_check --mode render` | {PASS/FAIL，error/warning 计数与良性项说明} |
| 词级 cue 声明契约 | {声明条数／是否全部可定位} |
| 停顿声明（`pause_marks`） | {条数／覆盖场数／是否可执行} |
| 停顿审计两桶（渲染前实测） | {连读桶 N 条／急促桶 M 条／未裁定位点数；来源 `[PAUSE-AUDIT]` 行，temp 读数不入 git} |
| 场景插入三腿（`check_scene_insert`） | {仅在增删/重排场景时填} |
| 章级条（`check_chapter_bar`） | {章名格／刻度格配对} |
| 预览 `instant_preview` | {安全区／文字塌陷／柱状溢出／动画密度四判据} |
| 空窗预判（渲染前置） | {实测 N/M 场过；曾用 --force 须写明} |
| 交付审计 `--audit` | {六维逐项 PASS 与关键读数} |

## 字幕时间戳来源（必填，主路径命中率）

Subtitle timestamp source: asr_forced {n}/{N} ({xx%}) | punct_gap {n} | char_prop {n} — 阈值 ≥{th%}

数据源：`media_qa_gate` 检查项 `subtitle_timestamp_source`（终检报告行
`Subtitle timestamp source:` 与完工报告 `data_sources.subtitle_timestamp_source` 同源，
取数面是 step5 逐场实测落盘的 `temp/_subtitle_timestamp_source.json`）。本节由
`pipeline_runner` 在完工报告 VALIDATED 后自动写入，是该结论的**唯一版本化留痕面**——
`过程产物/` 与项目 temp 不入 git，日志行在交付后不可追溯。

> 本节正文与标题结构以 `media_qa_gate.DELIVERY_NOTES_SECTION_TITLE` 为唯一生成点，
> 模板不另设表格形态（避免同一分布抄两份）；阈值数值取
> `audio_sync_rules.json → alignment.min_asr_forced_ratio`，此处只写指针不写死。
> 手工新建交付说明时保留标题、正文留空即可，流水线会在下一次 VALIDATED 时填充。

## 已清理的过程文件

- [ ] render_raw.mp4
- [ ] work-* 渲染工作目录
- [ ] tts_44k 缓存
- [ ] scene_*.mp3 原始 TTS
- [ ] .noaudio.mp4 备份

## 备注

{其他需要记录的信息}
