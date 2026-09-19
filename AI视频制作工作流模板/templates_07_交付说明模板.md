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
