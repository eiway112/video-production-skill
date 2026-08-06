#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
场景编译器 v2 (Scene Compiler) — 内容感知视频生成

架构: SDL 声明意图 → 内容分析 → 智能路由 → 分路径生产 → 统一组装
      用户只写"讲什么"，系统决定"怎么呈现"

四条生产路径（系统自动选择，用户无需指定）:
  A. 组件编译 — 信息类型有现成组件 → 参数化组装 (~60%)
  B. AI 设计  — 需要定制可视化 → 调用 AI 生成 HTML (~25%)
  C. 氛围场景 — 封面/结尾 → 组件库 + ImageGen (~15%)
  D. 人工注入 — SDL 中有 custom_html → 直接嵌入 (可选)

SDL 格式（YAML）:
    video:
      name: "产品宣传"
      theme: medical
      cover:
        title: "装配式隔墙系统"
        subtitle: "革新建筑空间"
        keywords: ["隔音", "防火"]
    scenes:
      - type: cards
        style: pain          # 风格变体: pain/feature/zone
        heading: "四大难题"
        items: [...]
      - type: split
        style: tech          # 风格变体: tech/callout
        heading: "墙体构造"
        image: "diagram.png"

用法:
    python scene_compiler.py project.yaml
    python scene_compiler.py project.yaml --dry-run
    python scene_compiler.py project.yaml --theme medical
"""

import sys, io, json, argparse, shutil, re
from pathlib import Path
from datetime import datetime

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml"); sys.exit(1)

SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parent.parent

# ═══════════════════════════════════════════════════════════════
#  主题系统 v2 (Theme System — 含语义角色色)
# ═══════════════════════════════════════════════════════════════

THEMES = {
    'dark-tech': {
        'bg': '#0c1222',
        'bg_gradient': 'radial-gradient(ellipse at 30% 40%, #1a2744 0%, #0c1222 60%, #060a14 100%)',
        'surface': '#141e33', 'surface_alt': '#1c2d4a',
        'text': '#e8edf5', 'text_dim': '#8899b3',
        'accent1': '#3b82f6', 'accent2': '#06b6d4', 'accent3': '#8b5cf6', 'accent_warm': '#f59e0b',
        'font_heading': "'Inter', 'Microsoft YaHei', sans-serif",
        'font_body': "'Inter', 'Microsoft YaHei', sans-serif",
        'glow': '0 0 80px rgba(59,130,246,0.15)',
        'card_shadow': '0 8px 32px rgba(0,0,0,0.4)',
        # Semantic role colors
        'pain_color': '#ef4444', 'pain_bg': 'rgba(239,68,68,0.08)',
        'feature_color': '#3b82f6', 'feature_bg': 'rgba(59,130,246,0.08)',
        'success_color': '#10b981', 'success_bg': 'rgba(16,185,129,0.08)',
        'warning_color': '#f59e0b', 'warning_bg': 'rgba(245,158,11,0.08)',
        'dark_overlay': 'rgba(12,18,34,0.85)',
    },
    'warm-corp': {
        'bg': '#faf6ee',
        'bg_gradient': 'linear-gradient(135deg, #faf6ee 0%, #f0e8d8 50%, #e8dcc8 100%)',
        'surface': '#ffffff', 'surface_alt': '#f5f0e0',
        'text': '#1a1a1a', 'text_dim': '#666666',
        'accent1': '#3b5e3a', 'accent2': '#cc8832', 'accent3': '#c45d3e', 'accent_warm': '#d4956a',
        'font_heading': "'Georgia', 'Microsoft YaHei', serif",
        'font_body': "'Inter', 'Microsoft YaHei', sans-serif",
        'glow': '0 0 60px rgba(59,94,58,0.1)',
        'card_shadow': '0 8px 24px rgba(0,0,0,0.08)',
        'pain_color': '#c45d3e', 'pain_bg': 'rgba(196,93,62,0.08)',
        'feature_color': '#3b5e3a', 'feature_bg': 'rgba(59,94,58,0.08)',
        'success_color': '#3b5e3a', 'success_bg': 'rgba(59,94,58,0.08)',
        'warning_color': '#cc8832', 'warning_bg': 'rgba(204,136,50,0.08)',
        'dark_overlay': 'rgba(26,26,26,0.85)',
    },
    'medical': {
        'bg': '#f0f5fa',
        'bg_gradient': 'linear-gradient(180deg, #f0f5fa 0%, #e4edf6 50%, #d8e4f0 100%)',
        'surface': '#ffffff', 'surface_alt': '#eef3f9',
        'text': '#1a2a3a', 'text_dim': '#5a6a7a',
        'accent1': '#0077b6', 'accent2': '#00b4d8', 'accent3': '#48cae4', 'accent_warm': '#f77f00',
        'font_heading': "'Inter', 'Microsoft YaHei', sans-serif",
        'font_body': "'Inter', 'Microsoft YaHei', sans-serif",
        'glow': '0 0 60px rgba(0,119,182,0.1)',
        'card_shadow': '0 8px 24px rgba(0,0,0,0.06)',
        'pain_color': '#e63946', 'pain_bg': 'rgba(230,57,70,0.08)',
        'feature_color': '#0077b6', 'feature_bg': 'rgba(0,119,182,0.08)',
        'success_color': '#2a9d8f', 'success_bg': 'rgba(42,157,143,0.08)',
        'warning_color': '#e9c46a', 'warning_bg': 'rgba(233,196,106,0.08)',
        'dark_overlay': 'rgba(12,18,34,0.75)',
    },
    'hospitality': {
        'bg': '#1a1a2e',
        'bg_gradient': 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
        'surface': '#1f2940', 'surface_alt': '#253352',
        'text': '#f0e6d3', 'text_dim': '#a0998a',
        'accent1': '#c9a96e', 'accent2': '#e8d5b7', 'accent3': '#8b7355', 'accent_warm': '#d4956a',
        'font_heading': "'Georgia', 'Microsoft YaHei', serif",
        'font_body': "'Inter', 'Microsoft YaHei', sans-serif",
        'glow': '0 0 80px rgba(201,169,110,0.12)',
        'card_shadow': '0 8px 32px rgba(0,0,0,0.4)',
        'pain_color': '#ef4444', 'pain_bg': 'rgba(239,68,68,0.08)',
        'feature_color': '#c9a96e', 'feature_bg': 'rgba(201,169,110,0.08)',
        'success_color': '#10b981', 'success_bg': 'rgba(16,185,129,0.08)',
        'warning_color': '#f59e0b', 'warning_bg': 'rgba(245,158,11,0.08)',
        'dark_overlay': 'rgba(15,23,42,0.85)',
    },
}


# ═══════════════════════════════════════════════════════════════
#  内容分析器 (Content Analyzer)
# ═══════════════════════════════════════════════════════════════

def analyze_scene(scene_data, scene_idx, total_scenes):
    """分析场景的信息类型，返回推荐 style 和 atmosphere。

    信息类型:
      enumeration — N个并列要点 (items数组)
      declaration — 核心概念宣告 (title+subtitle)
      structure   — 技术结构分解 (image+技术文本)
      metrics     — 数值指标 (value+unit+label)
      process     — 时序步骤 (items有序)
      comparison  — 两方对照 (两列内容)
      closing     — CTA/总结

    路由规则:
      enumeration + 首场景 + 负面词 → pain style
      enumeration + 功能/优势词 → feature style
      structure + image → tech/callout style
      封面/结尾 → atmosphere 场景
    """
    stype = scene_data.get('type', 'hero')
    heading = (scene_data.get('heading', '') + scene_data.get('title', '')).lower()
    narration = scene_data.get('narration', '').lower()
    items = scene_data.get('items', [])
    has_image = bool(scene_data.get('image'))

    result = {'info_type': stype, 'style': scene_data.get('style', ''), 'atmosphere': ''}

    # Explicit style overrides analysis
    if result['style']:
        return result

    # Route based on content signals
    if stype == 'cards' and items:
        pain_signals = ['难题', '挑战', '问题', '困难', '痛点', '风险', '不足', '缺陷']
        feature_signals = ['功能', '优势', '特点', '特性', '亮点', '能力']
        zone_signals = ['分区', '区域', '场景', '适用', '应用']

        combined = heading + narration
        if any(s in combined for s in pain_signals):
            result['style'] = 'pain'
            result['atmosphere'] = 'dark'  # Pain points deserve dramatic dark bg
        elif any(s in combined for s in zone_signals):
            result['style'] = 'zone'
        elif any(s in combined for s in feature_signals):
            result['style'] = 'feature'
        else:
            result['style'] = 'default'

    elif stype == 'hero':
        if scene_idx == 0 or scene_idx == 1:
            result['atmosphere'] = 'gradient'
        result['style'] = 'default'

    elif stype == 'split':
        if has_image:
            # Check if content is technical/structural
            tech_signals = ['构造', '结构', '剖面', '层', '材料', '工艺', '系统', '组件']
            combined = heading + narration + scene_data.get('text', '').lower()
            if any(s in combined for s in tech_signals):
                result['style'] = 'tech'
            else:
                result['style'] = 'callout'
        else:
            result['style'] = 'default'

    elif stype == 'stats':
        result['style'] = 'default'
        result['atmosphere'] = ''  # Stats work well on default bg

    elif stype == 'list':
        process_signals = ['步骤', '流程', '阶段', '第一步', '然后', '接着']
        if any(s in heading + narration for s in process_signals):
            result['style'] = 'process'
        else:
            result['style'] = 'default'

    elif stype == 'closing':
        result['atmosphere'] = 'gradient'
        result['style'] = 'default'

    elif stype == 'structure':
        # AI 设计路径: structure 类型自动路由到定制可视化
        layers = scene_data.get('layers', [])
        if layers:
            result['style'] = 'cross_section'
            result['atmosphere'] = 'dark'  # Technical cross-sections → dark bg
        else:
            # No layers data → fallback to tech split
            result['style'] = 'tech'

    return result


# ═══════════════════════════════════════════════════════════════
#  氛围系统 (Atmosphere System)
# ═══════════════════════════════════════════════════════════════

def gen_atmosphere_bg(scene_idx, atmosphere, theme):
    """为场景生成独立的背景 CSS。

    atmosphere 类型:
      '' (空) — 使用全局默认背景，不生成额外CSS
      'dark'  — 深色背景 + 微光效果 (适合痛点/严肃内容)
      'gradient' — 主题色渐变 (适合封面/结尾)
      'image' — 背景图片 + 暗色叠层 (适合应用场景)
    """
    sid = f'scene{scene_idx}'
    if not atmosphere:
        return '', ''  # No custom atmosphere, use global

    css = ''
    html_overlay = ''

    if atmosphere == 'dark':
        # Use dark overlay color from theme, fallback to deep navy
        dark_bg = theme.get('dark_overlay', 'rgba(12,18,34,1.0)')
        # Convert rgba overlay to solid hex-like for scene bg
        dark_solid = '#0c1222'  # Deep navy, consistent with hand-crafted references
        css = (
            f'#{sid} .sc {{ background: {dark_solid}; }}\n'
        )
        # Add subtle glow orbs for visual depth
        html_overlay = (
            f'<div class="atmo-glow" style="position:absolute;width:500px;height:500px;'
            f'background:radial-gradient(circle,{theme["accent1"]}15 0%,transparent 70%);'
            f'top:-100px;right:100px;border-radius:50%;pointer-events:none;"></div>\n'
            f'<div class="atmo-glow" style="position:absolute;width:400px;height:400px;'
            f'background:radial-gradient(circle,{theme["accent2"]}10 0%,transparent 70%);'
            f'bottom:50px;left:200px;border-radius:50%;pointer-events:none;"></div>\n'
        )

    elif atmosphere == 'gradient':
        # Richer gradient than the global one
        css = (
            f'#{sid} .sc {{ background: linear-gradient(135deg, '
            f'{theme["accent1"]}12 0%, {theme["bg"]} 40%, {theme["accent2"]}08 100%); }}\n'
        )

    return css, html_overlay


def gen_scene_decorations(scene_idx, theme, t_start, t_end, t_ref=None):
    """为每个场景生成装饰层 HTML + 持续动画 JS。

    装饰层包括：透视网格、环境光源、光晕球、扫描线、浮动点、浮动环。
    持续动画包括：光晕呼吸、浮点漂浮、环旋转、扫描线闪动、网格呼吸。
    所有参数由 scene_idx 自动决定（确定性 + 多样性），无需人工调整。

    t_ref: optional T-block expression (e.g. "T.s1") for structured timeline.
    """
    sid = f'scene{scene_idx}'
    dur = t_end - t_start
    accent1 = theme.get('accent1', '#06b6d4')
    accent2 = theme.get('accent2', '#f59e0b')
    t0 = t_ref if t_ref else f'{t_start:.1f}'
    t05 = f'{t_ref} + 0.5' if t_ref else f'{t_start + 0.5:.1f}'
    t03 = f'{t_ref} + 0.3' if t_ref else f'{t_start + 0.3:.1f}'

    # ── 装饰层 HTML ──
    # 位置参数由 scene_idx 轮换，保证不同场景有不同的构图
    offsets = [
        # (glow1_x, glow1_y, glow2_x, glow2_y, dot1_x, dot1_y, ring_x, ring_y)
        ('right:180px', 'top:-120px', 'left:120px', 'bottom:360px', 'top:140px', 'left:180px', 'right:160px', 'top:200px'),
        ('left:200px', 'top:-100px', 'right:80px', 'bottom:380px', 'top:120px', 'right:200px', 'left:140px', 'bottom:380px'),
        ('right:120px', 'top:-80px', 'left:200px', 'bottom:400px', 'bottom:380px', 'left:160px', 'right:200px', 'top:160px'),
        ('left:160px', 'top:-140px', 'right:160px', 'bottom:370px', 'top:100px', 'right:180px', 'left:200px', 'bottom:400px'),
    ]
    o = offsets[scene_idx % len(offsets)]

    decorations_html = (
        # 透视网格 — 全局底层装饰，提供空间纵深感
        f'<div class="deco-grid" id="{sid}-grid" style="position:absolute;inset:0;'
        f'background-image:linear-gradient(rgba(255,255,255,0.03) 1px,transparent 1px),'
        f'linear-gradient(90deg,rgba(255,255,255,0.03) 1px,transparent 1px);'
        f'background-size:80px 80px;pointer-events:none;"></div>\n'
        # 环境光源 — 大面积柔和光晕，从顶部或角落铺洒
        f'<div class="deco-ambient" id="{sid}-amb" style="position:absolute;'
        f'top:-200px;left:50%;transform:translateX(-50%);width:1200px;height:600px;'
        f'background:radial-gradient(ellipse,{accent1}08 0%,transparent 70%);'
        f'pointer-events:none;"></div>\n'
        # 光晕球 x2 — 不同主题色，提供色彩层次
        f'<div class="deco-glow" id="{sid}-g1" style="position:absolute;width:550px;height:550px;'
        f'background:radial-gradient(circle,{accent1}12 0%,transparent 70%);'
        f'{o[0]};{o[1]};border-radius:50%;pointer-events:none;"></div>\n'
        f'<div class="deco-glow" id="{sid}-g2" style="position:absolute;width:400px;height:400px;'
        f'background:radial-gradient(circle,{accent2}08 0%,transparent 70%);'
        f'{o[2]};{o[3]};border-radius:50%;pointer-events:none;"></div>\n'
        # 扫描线 — 水平光线，提供科技感
        f'<div class="deco-scan" id="{sid}-scan" style="position:absolute;left:0;width:100%;height:2px;'
        f'background:linear-gradient(90deg,transparent,{accent1}20,transparent);'
        f'top:500px;pointer-events:none;"></div>\n'
        # 浮动点 x2 — 小圆点漂浮，增加活力
        f'<div class="deco-dot" id="{sid}-d1" style="position:absolute;width:10px;height:10px;'
        f'border-radius:50%;background:{accent1};opacity:0.4;'
        f'{o[4]};pointer-events:none;"></div>\n'
        f'<div class="deco-dot" id="{sid}-d2" style="position:absolute;width:10px;height:10px;'
        f'border-radius:50%;background:{accent2};opacity:0.35;'
        f'{o[5]};pointer-events:none;"></div>\n'
        # 浮动环 — 空心圆环旋转
        f'<div class="deco-ring" id="{sid}-r1" style="position:absolute;width:60px;height:60px;'
        f'border-radius:50%;border:2px solid {accent1}35;'
        f'{o[6]};{o[7]};pointer-events:none;"></div>\n'
    )

    # ── 持续动画 JS ──
    # 呼吸：光晕 scale 脉动 + 环境光源微闪
    # 漂浮：浮点 y/x 往复 + 环旋转
    # 闪动：扫描线 opacity + 网格 opacity
    # repeat 用有限值（HyperFrames capture engine 不支持 repeat:-1）
    # yoyo 动画 cycle = duration*2，非 yoyo = duration
    def _rep(cycle):
        """计算有限 repeat 数，覆盖场景窗口 + 余量"""
        return max(int(dur / cycle) + 2, 10)
    r_g1, r_g2 = _rep(8), _rep(10)       # glow 4s*2=8s, 5s*2=10s
    r_amb, r_d1, r_d2 = _rep(6), _rep(5), _rep(6)  # 3*2, 2.5*2, 3*2
    r_r1 = _rep(8)                         # ring 8s (non-yoyo)
    r_scan, r_grid = _rep(6), _rep(8)      # 3*2, 4*2
    anim_code = (
        f'  // Scene {scene_idx} continuous decorations\n'
        # 光晕呼吸 — scale 1.0↔1.2，yoyo 有限循环
        f'  tl.to("#{sid}-g1", {{ scale:1.2, duration:4, ease:"sine.inOut", yoyo:true, repeat:{r_g1} }}, {t0});\n'
        f'  tl.to("#{sid}-g2", {{ scale:1.15, x:10, duration:5, ease:"sine.inOut", yoyo:true, repeat:{r_g2} }}, {t05});\n'
        # 环境光源微闪 — opacity 0.6↔1.0
        f'  tl.to("#{sid}-amb", {{ opacity:0.6, duration:3, ease:"sine.inOut", yoyo:true, repeat:{r_amb} }}, {t0});\n'
        # 浮动点漂浮 — y ±15px, x ±8px
        f'  tl.to("#{sid}-d1", {{ y:"-=15", x:"+=8", duration:2.5, ease:"sine.inOut", yoyo:true, repeat:{r_d1} }}, {t0});\n'
        f'  tl.to("#{sid}-d2", {{ y:"+=12", x:"-=6", duration:3, ease:"sine.inOut", yoyo:true, repeat:{r_d2} }}, {t03});\n'
        # 浮动环旋转 — 线性 360°
        f'  tl.to("#{sid}-r1", {{ rotation:360, duration:8, ease:"none", repeat:{r_r1} }}, {t0});\n'
        # 扫描线闪动 — opacity 0.08↔0.2 + y 偏移
        f'  tl.to("#{sid}-scan", {{ opacity:0.2, y:8, duration:3, ease:"sine.inOut", yoyo:true, repeat:{r_scan} }}, {t0});\n'
        # 网格呼吸 — opacity 0.04↔0.07
        f'  tl.to("#{sid}-grid", {{ opacity:0.7, duration:4, ease:"sine.inOut", yoyo:true, repeat:{r_grid} }}, {t0});\n'
    )

    return decorations_html, anim_code


# ═══════════════════════════════════════════════════════════════
#  动画变化池 (Animation Variation Pools)
#  全局机制：所有变化由元素位置(index)自动决定，零可调参数
#  同一SDL每次编译输出一致(确定性)，不同元素之间天然不同(多样性)
# ═══════════════════════════════════════════════════════════════

# 入场缓动池 — 4种物理特征各异，按元素index轮换
ENTRY_EASE_POOL = [
    'back.out(1.4)',
    'power3.out',
    'expo.out',
    'elastic.out(1, 0.6)',
]

# 转场方向序列 — push-left / push-up / fade 三种轮换，打破视觉单调
TRANSITION_SEQUENCE = ['push-left', 'push-up', 'fade']


def _pick_ease(idx):
    """从缓动池中选择，由元素index决定，无需人工指定。"""
    return ENTRY_EASE_POOL[idx % len(ENTRY_EASE_POOL)]


def _pick_transition(scene_idx):
    """从转场序列中选择，由场景index决定，无需人工指定。"""
    return TRANSITION_SEQUENCE[scene_idx % len(TRANSITION_SEQUENCE)]


# ═══════════════════════════════════════════════════════════════
#  动画引擎 v2 (Animation Engine — 内容驱动 + 自变化)
# ═══════════════════════════════════════════════════════════════

def _te(t):
    """Convert timing value to GSAP position expression string.

    Accepts float (legacy absolute time) or str (T-block reference like "T.s1 + 0.5").
    Enables structured timeline: when T-block is rewritten by adjust_timeline,
    all GSAP positions automatically follow — no regex scanning needed.
    """
    if isinstance(t, str):
        return t
    return f'{t:.1f}'


def _expr_offset(t):
    """Extract numeric offset from a T-block expression for internal arithmetic.

    "T.s1 + 2.3" -> 2.3, "T.s1" -> 0.0, 5.7 -> 5.7
    Used ONLY for computing spans/intervals inside animation functions.
    The actual GSAP output always uses the full expression string.
    """
    if isinstance(t, (int, float)):
        return float(t)
    m = re.search(r'[+\-]\s*([\d.]+)\s*$', t)
    if m:
        return float(m.group(1))
    return 0.0


def anim_drift_in(selector, t, direction='right', duration=1.0, stagger=0):
    """Drift-in: opacity + x/y偏移 + scale → 归位。
    自变化机制：stagger模式下每个元素自动获得不同的ease/direction/duration。
    """
    offsets = {
        'right': ('x: "+=50",', 'y: 0,'),
        'left': ('x: "-=50",', 'y: 0,'),
        'bottom': ('x: 0,', 'y: "+=40",'),
        'top': ('x: 0,', 'y: "-=40",'),
    }
    alt_dirs = ['right', 'bottom', 'left', 'top']
    if stagger > 0:
        return (
            f'  gsap.utils.toArray("{selector}").forEach((el, i) => {{\n'
            f'    var _e = ["back.out(1.4)","power3.out","expo.out","elastic.out(1, 0.6)"][i % 4];\n'
            f'    var _d = ["right","bottom","left","top"][i % 4];\n'
            f'    var _x = (_d==="left"?-50:_d==="right"?50:0);\n'
            f'    var _y = (_d==="top"?-40:_d==="bottom"?40:0);\n'
            f'    tl.fromTo(el,\n'
            f'      {{ opacity: 0, x: _x, y: _y, scale: 0.9 }},\n'
            f'      {{ opacity: 1, x: 0, y: 0, scale: 1, duration: {duration} * (1 - (i % 3) * 0.08), ease: _e }},\n'
            f'      {_te(t)} + i * {stagger}\n    );\n  }});\n'
        )
    x_off, y_off = offsets.get(direction, offsets['right'])
    return (
        f'  tl.fromTo("{selector}",\n'
        f'    {{ opacity: 0, {x_off} {y_off} scale: 0.9 }},\n'
        f'    {{ opacity: 1, x: 0, y: 0, scale: 1, duration: {duration}, ease: "back.out(1.4)" }},\n'
        f'    {_te(t)}\n  );\n'
    )


def anim_fade_up(selector, t, duration=0.8, stagger=0):
    """Fade-up: opacity + y偏移 → 归位。
    自变化机制：stagger模式下每个元素自动获得不同的ease。
    """
    if stagger > 0:
        return (
            f'  gsap.utils.toArray("{selector}").forEach((el, i) => {{\n'
            f'    var _e = ["power2.out","power3.out","expo.out","sine.out"][i % 4];\n'
            f'    tl.fromTo(el,\n      {{ opacity: 0, y: 30 }},\n'
            f'      {{ opacity: 1, y: 0, duration: {duration}, ease: _e }},\n'
            f'      {_te(t)} + i * {stagger}\n    );\n  }});\n'
        )
    return (
        f'  tl.fromTo("{selector}",\n'
        f'    {{ opacity: 0, y: 30 }},\n'
        f'    {{ opacity: 1, y: 0, duration: {duration}, ease: "power2.out" }},\n'
        f'    {_te(t)}\n  );\n'
    )


def anim_count_up(selector, t, end_val, unit='%', duration=1.5):
    """Count-up: 数字从0增长到目标值。"""
    t_expr = _te(t)
    t_plus = f'{t_expr} + 0.3' if isinstance(t, str) else f'{t + 0.3:.1f}'
    return (
        f'  tl.fromTo("{selector}",\n'
        f'    {{ opacity: 0, scale: 0.8 }},\n'
        f'    {{ opacity: 1, scale: 1, duration: 0.6, ease: "back.out(1.7)" }},\n'
        f'    {t_expr}\n  );\n'
        f'  tl.to("{selector}", {{\n'
        f'    innerText: {end_val}, duration: {duration}, snap: {{ innerText: 1 }},\n'
        f'    ease: "power2.out",\n'
        f'    onUpdate: function() {{\n'
        f'      this.targets()[0].textContent = Math.round(parseFloat(this.targets()[0].innerText)) + "{unit}";\n'
        f'    }}\n  }}, {t_plus});\n'
    )


def anim_progressive_reveal(selectors, t_start, t_end, theme, t_ref=None):
    """渐进式揭示：按时间均匀分布多个元素的入场动画。
    替代 glow-pulse 填充，让画面始终有内容在变化。
    自变化机制：每个元素自动获得不同的ease（通过anim_fade_up stagger模式）。

    t_ref: optional T-block expression base (e.g. "T.s1"). If provided, all
           emitted positions use T-ref expressions instead of absolute numbers.
    """
    if not selectors:
        return ''
    n = len(selectors)
    available = _expr_offset(t_end) - _expr_offset(t_start) - 3  # Leave 3s buffer
    if available < 1:
        return ''

    code = ''
    interval = min(available / max(n, 1), 2.0)
    base_off = 0.5  # offset from t_start for first element

    for i, sel in enumerate(selectors):
        offset = base_off + i * interval
        if t_ref:
            t_emit = f'{t_ref} + {offset:.1f}'
        else:
            t_emit = t_start + offset
        if i == 0:
            code += anim_fade_up(sel, t_emit, 0.6)
        else:
            code += anim_fade_up(sel, t_emit, 0.5)

    # Subtle continuous animation: gentle highlight sweep (varied per element)
    if available > 4 and n > 0:
        sweep_start_off = n * interval + 2
        sweep_end_off = (_expr_offset(t_end) - _expr_offset(t_start)) - 1  # relative offset from t_start
        if sweep_end_off > sweep_start_off:
            sweep_eases = ['sine.inOut', 'power1.inOut', 'circ.inOut']
            for i, sel in enumerate(selectors):
                pulse_off = sweep_start_off + (i * 0.8)
                if pulse_off < sweep_end_off - 1:
                    ease = sweep_eases[i % len(sweep_eases)]
                    if t_ref:
                        pt = f'{t_ref} + {pulse_off:.1f}'
                        pt2 = f'{t_ref} + {pulse_off + 0.8:.1f}' if i % 3 == 0 else f'{t_ref} + {pulse_off + 0.6:.1f}' if i % 3 == 1 else f'{t_ref} + {pulse_off + 0.7:.1f}'
                    else:
                        pt = f'{t_start + pulse_off:.1f}'
                        pt2 = f'{t_start + pulse_off + 0.8:.1f}' if i % 3 == 0 else f'{t_start + pulse_off + 0.6:.1f}' if i % 3 == 1 else f'{t_start + pulse_off + 0.7:.1f}'
                    if i % 3 == 0:
                        code += (
                            f'  tl.to("{sel}", {{ scale: 1.02, duration: 0.8, ease: "{ease}" }}, {pt});\n'
                            f'  tl.to("{sel}", {{ scale: 1.0, duration: 0.8, ease: "{ease}" }}, {pt2});\n'
                        )
                    elif i % 3 == 1:
                        code += (
                            f'  tl.to("{sel}", {{ opacity: 0.85, duration: 0.6, ease: "{ease}" }}, {pt});\n'
                            f'  tl.to("{sel}", {{ opacity: 1, duration: 0.6, ease: "{ease}" }}, {pt2});\n'
                        )
                    else:
                        code += (
                            f'  tl.to("{sel}", {{ y: -4, duration: 0.7, ease: "{ease}" }}, {pt});\n'
                            f'  tl.to("{sel}", {{ y: 0, duration: 0.7, ease: "{ease}" }}, {pt2});\n'
                        )
    return code


def anim_glow(selector, t_start, t_end, color='rgba(59,130,246,0.3)', t_ref=None):
    """持续呼吸光晕 (backward compat)."""
    cycle = 3.0
    code = ''
    span = _expr_offset(t_end) - _expr_offset(t_start)
    off = 0.0
    while off < span - 1:
        if t_ref:
            t1 = f'{t_ref} + {off:.1f}'
            t2 = f'{t_ref} + {off + cycle/2:.1f}'
        else:
            t1 = f'{_expr_offset(t_start) + off:.1f}'
            t2 = f'{_expr_offset(t_start) + off + cycle/2:.1f}'
        code += (
            f'  tl.to("{selector}", {{ boxShadow: "0 0 40px {color}", duration: {cycle/2}, ease: "sine.inOut" }}, {t1});\n'
            f'  tl.to("{selector}", {{ boxShadow: "0 0 10px transparent", duration: {cycle/2}, ease: "sine.inOut" }}, {t2});\n'
        )
        off += cycle
    return code


# ═══════════════════════════════════════════════════════════════
#  转场生成器 (Transition Generator)
# ═══════════════════════════════════════════════════════════════

def gen_transition(scene_out_id, scene_in_id, t, direction='push-left'):
    """生成场景间转场动画。支持 push-left / push-up / fade 三种方向。
    t can be float or string (T-block expression).
    """
    dur = 0.8
    t_expr = _te(t)
    t_hide = f'{t_expr} + {dur + 0.1}' if isinstance(t, str) else f'{t + dur + 0.1:.1f}'
    if direction == 'push-left':
        return (
            f'  /* TRANSITION {scene_out_id}->{scene_in_id}: Push Left */\n'
            f'  tl.to("#scene{scene_out_id}", {{ x: -1920, duration: {dur}, ease: "power2.inOut" }}, {t_expr});\n'
            f'  tl.fromTo("#scene{scene_in_id}",\n'
            f'    {{ x: 1920, opacity: 1 }},\n'
            f'    {{ x: 0, duration: {dur}, ease: "power2.inOut" }}, {t_expr});\n'
            f'  tl.set("#scene{scene_out_id}", {{ visibility: "hidden" }}, {t_hide});\n'
        )
    elif direction == 'push-up':
        return (
            f'  /* TRANSITION {scene_out_id}->{scene_in_id}: Push Up */\n'
            f'  tl.to("#scene{scene_out_id}", {{ y: -1080, duration: {dur}, ease: "power2.inOut" }}, {t_expr});\n'
            f'  tl.fromTo("#scene{scene_in_id}",\n'
            f'    {{ y: 1080, opacity: 1 }},\n'
            f'    {{ y: 0, duration: {dur}, ease: "power2.inOut" }}, {t_expr});\n'
            f'  tl.set("#scene{scene_out_id}", {{ visibility: "hidden" }}, {t_hide});\n'
        )
    elif direction == 'fade':
        t_fade = f'{t_expr} + 0.2' if isinstance(t, str) else f'{t + 0.2:.1f}'
        return (
            f'  /* TRANSITION {scene_out_id}->{scene_in_id}: Fade */\n'
            f'  tl.to("#scene{scene_out_id}", {{ opacity: 0, duration: {dur}, ease: "power1.inOut" }}, {t_expr});\n'
            f'  tl.fromTo("#scene{scene_in_id}",\n'
            f'    {{ opacity: 0 }},\n'
            f'    {{ opacity: 1, duration: {dur}, ease: "power1.inOut" }}, {t_fade});\n'
            f'  tl.set("#scene{scene_out_id}", {{ visibility: "hidden" }}, {t_hide});\n'
        )
    return ''


# ═══════════════════════════════════════════════════════════════
#  组件库 v2 (Component Library — Style Variants)
# ═══════════════════════════════════════════════════════════════

def gen_scene_hero(scene, idx, theme, t_start, t_end, analysis=None):
    """Hero 场景：全屏标题卡。Style: default/dark"""
    sid = f'scene{idx}'
    title = scene.get('title', '')
    subtitle = scene.get('subtitle', '')
    keywords = scene.get('keywords', [])
    style = (analysis or {}).get('style', 'default')
    is_dark = style == 'dark' or (analysis or {}).get('atmosphere') == 'dark'

    text_color = '#f0f5fa' if is_dark else theme['text']
    dim_color = '#a0b0c0' if is_dark else theme['text_dim']
    bg_override = f'#{sid} .sc {{ background: #0c1222; }}\n' if is_dark else ''

    kw_html = ''
    if keywords:
        kw_items = ''.join(f'<span class="kw-tag">{k}</span>' for k in keywords)
        kw_html = f'<div class="kw-row">{kw_items}</div>'

    # Add atmosphere glows for dark style
    glow_html = ''
    if is_dark:
        glow_html = (
            f'<div style="position:absolute;width:600px;height:600px;'
            f'background:radial-gradient(circle,{theme["accent1"]}18 0%,transparent 70%);'
            f'top:-150px;right:-50px;border-radius:50%;pointer-events:none;"></div>\n'
        )

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx};'
        f'{" opacity:0;" if idx > 0 else ""}"'
        f' data-scene-entry="{"gsap" if idx > 0 else "static"}"'
        f' data-scene-subtitle-safe="true">\n'
        f'  {glow_html}'
        f'  <div class="sc">\n'
        f'    <div class="hero-wrap">\n'
        f'      <h1 class="hero-title">{title}</h1>\n'
        f'      <p class="hero-sub">{subtitle}</p>\n'
        f'      {kw_html}\n'
        f'    </div>\n'
        f'    <div class="subtitle-safe"></div>\n'
        f'  </div>\n</div>\n'
    )

    kw_css = ''
    if keywords:
        kw_css = (
            f'  .kw-row {{ display:flex; gap:16px; justify-content:center; margin-top:40px; flex-wrap:wrap; }}\n'
            f'  .kw-tag {{ padding:10px 28px; background:{theme["surface_alt"]}; color:{theme["accent2"]};\n'
            f'    border-radius:30px; font-size:30px; font-weight:500; letter-spacing:1px;\n'
            f'    border:1px solid {theme["accent1"]}40; opacity:0; }}\n'
        )

    css = (
        f'{bg_override}'
        f'#{sid} .sc {{ display:flex; align-items:center; justify-content:center; padding-bottom:calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .hero-wrap {{ text-align:center; max-width:1500px; }}\n'
        f'#{sid} .hero-title {{ font-size:88px; font-weight:700; color:{text_color};\n'
        f'  line-height:1.15; letter-spacing:-2px; opacity:0;\n'
        f'  font-family:{theme["font_heading"]}; text-shadow:{theme["glow"]}; }}\n'
        f'#{sid} .hero-sub {{ font-size:44px; color:{dim_color};\n'
        f'  margin-top:24px; font-weight:400; opacity:0; font-family:{theme["font_body"]}; }}\n'
        f'{kw_css}'
    )

    t_ref = f'T.s{idx}'
    anim = anim_drift_in(f'#{sid} .hero-title', f'{t_ref} + 0.5', 'bottom', 1.0)
    anim += anim_fade_up(f'#{sid} .hero-sub', f'{t_ref} + 1.5', 0.8)
    if keywords:
        anim += anim_drift_in(f'#{sid} .kw-tag', f'{t_ref} + 2.0', 'bottom', 0.6, stagger=0.3)

    return html, css, anim


def gen_scene_cards(scene, idx, theme, t_start, t_end, analysis=None):
    """Cards 场景：Style variants — default/pain/feature/zone"""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])
    n = len(items)
    cols = min(n, 4)
    style = (analysis or {}).get('style', 'default')

    # Route to style-specific generator
    if style == 'pain':
        return _gen_cards_pain(scene, idx, theme, t_start, t_end, analysis)
    elif style == 'feature':
        return _gen_cards_feature(scene, idx, theme, t_start, t_end, analysis)
    elif style == 'zone':
        return _gen_cards_zone(scene, idx, theme, t_start, t_end, analysis)
    return _gen_cards_default(scene, idx, theme, t_start, t_end, analysis)


def _cards_scene_html(sid, idx, heading, cols, cards_html, extra_html=''):
    """Cards 系列场景共享 HTML 骨架（scene 容器 + 标题 + cards-grid + 字幕安全区）。"""
    return (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'{extra_html}'
        f'  <div class="sc">\n    <h2 class="section-heading">{heading}</h2>\n'
        f'    <div class="cards-grid" style="grid-template-columns:repeat({cols},1fr);">\n'
        f'      {cards_html}\n    </div>\n    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )


def _cards_scene_css_head(sid, theme, text_color=None, grid_gap=20):
    """Cards 系列场景共享 CSS 前导（.sc 布局 / .section-heading / .cards-grid）。"""
    text_c = text_color or theme["text"]
    return (
        f'#{sid} .sc {{ display:flex; flex-direction:column; align-items:center; padding-top:50px; padding-bottom:calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .section-heading {{ font-size:56px; font-weight:700; color:{text_c};\n'
        f'  margin-bottom:24px; opacity:0; font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .cards-grid {{ display:grid; gap:{grid_gap}px; width:100%; max-width:1760px; }}\n'
    )


def _cards_scene_anim(sid, idx, n, theme, t_start, t_end):
    """Cards 系列场景共享动画（标题淡入 + 卡片渐进显现 + 逐卡发光）。"""
    t_ref = f'T.s{idx}'
    anim = anim_fade_up(f'#{sid} .section-heading', f'{t_ref} + 0.3', 0.8)
    selectors = [f'#{sid}-c{i}' for i in range(n)]
    anim += anim_progressive_reveal(selectors, t_start + 0.8, t_end - 2, theme, t_ref=t_ref)
    for _i in range(n):
        anim += anim_glow(f'#{sid}-c{_i}', t_start + 2.0, t_end - 1, t_ref=t_ref)
    return anim


def _gen_cards_default(scene, idx, theme, t_start, t_end, analysis):
    """Default cards: clean grid with subtle styling."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])
    n = len(items)
    cols = min(n, 4)

    cards_html = ''
    for i, item in enumerate(items):
        badge = f'<span class="card-badge">{item["badge"]}</span>' if item.get('badge') else ''
        cards_html += (
            f'<div class="card-item" id="{sid}-c{i}">\n  {badge}\n'
            f'  <h3 class="card-title">{item.get("title", "")}</h3>\n'
            f'  <p class="card-desc">{item.get("desc", "")}</p>\n</div>\n'
        )

    html = _cards_scene_html(sid, idx, heading, cols, cards_html)

    css = _cards_scene_css_head(sid, theme, grid_gap=20) + (
        f'#{sid} .card-item {{ background:{theme["surface"]}; border-radius:20px; padding:40px 32px;\n'
        f'  box-shadow:{theme["card_shadow"]}; border:1px solid {theme["accent1"]}20; opacity:0; }}\n'
        f'#{sid} .card-badge {{ display:inline-block; padding:6px 18px; background:{theme["accent1"]}25;\n'
        f'  color:{theme["accent1"]}; border-radius:20px; font-size:24px; font-weight:600; margin-bottom:16px; }}\n'
        f'#{sid} .card-title {{ font-size:40px; font-weight:700; color:{theme["text"]}; margin-bottom:12px;\n'
        f'  font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .card-desc {{ font-size:30px; color:{theme["text_dim"]}; line-height:1.5;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
    )

    anim = _cards_scene_anim(sid, idx, n, theme, t_start, t_end)
    return html, css, anim


def _gen_cards_pain(scene, idx, theme, t_start, t_end, analysis):
    """Pain grid: color-coded problem cards with numbered indicators + dark bg."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])
    n = len(items)
    cols = min(n, 4)
    pain_c = theme.get('pain_color', '#ef4444')
    pain_bg = theme.get('pain_bg', 'rgba(239,68,68,0.08)')
    text_c = '#f0f5fa'  # Pain grids use light text on dark bg
    dim_c = '#a0b0c0'

    # Color cycle for variety
    accent_colors = [
        theme.get('pain_color', '#ef4444'),
        theme.get('warning_color', '#f59e0b'),
        theme.get('feature_color', '#3b82f6'),
        theme.get('success_color', '#10b981'),
    ]

    # Atmosphere glows
    glow_html = (
        f'<div style="position:absolute;width:500px;height:500px;'
        f'background:radial-gradient(circle,{pain_c}10 0%,transparent 70%);'
        f'top:50px;right:100px;border-radius:50%;pointer-events:none;"></div>\n'
        f'<div style="position:absolute;width:400px;height:400px;'
        f'background:radial-gradient(circle,{theme["accent1"]}08 0%,transparent 70%);'
        f'bottom:100px;left:200px;border-radius:50%;pointer-events:none;"></div>\n'
    )

    cards_html = ''
    for i, item in enumerate(items):
        color = accent_colors[i % len(accent_colors)]
        badge = item.get('badge', f'{i+1}')
        cards_html += (
            f'<div class="pain-card" id="{sid}-c{i}" style="border-left-color:{color};">\n'
            f'  <span class="pain-num" style="color:{color};">{badge}</span>\n'
            f'  <div class="pain-body">\n'
            f'    <h3 class="pain-title">{item.get("title", "")}</h3>\n'
            f'    <p class="pain-desc">{item.get("desc", "")}</p>\n'
            f'  </div>\n</div>\n'
        )

    html = _cards_scene_html(sid, idx, heading, cols, cards_html, extra_html=f'  {glow_html}')

    css = _cards_scene_css_head(sid, theme, text_color=text_c, grid_gap=24) + (
        f'#{sid} .pain-card {{ display:flex; align-items:flex-start; gap:20px;\n'
        f'  background:rgba(255,255,255,0.05); border-left:4px solid {pain_c};\n'
        f'  border-radius:12px; padding:28px 24px; opacity:0; }}\n'
        f'#{sid} .pain-num {{ font-size:48px; font-weight:900; line-height:1; min-width:60px; }}\n'
        f'#{sid} .pain-body {{ display:flex; flex-direction:column; gap:8px; }}\n'
        f'#{sid} .pain-title {{ font-size:36px; font-weight:700; color:{text_c};\n'
        f'  font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .pain-desc {{ font-size:28px; color:{dim_c}; line-height:1.5;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
    )

    anim = _cards_scene_anim(sid, idx, n, theme, t_start, t_end)
    return html, css, anim


def _gen_cards_feature(scene, idx, theme, t_start, t_end, analysis):
    """Feature grid: icon-style cards with accent highlights."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])
    n = len(items)
    cols = min(n, 4)
    feat_c = theme.get('feature_color', theme['accent1'])

    cards_html = ''
    for i, item in enumerate(items):
        icon = item.get('icon', '')
        badge = f'<span class="feat-badge">{item.get("badge", "")}</span>' if item.get('badge') else ''
        icon_html = f'<div class="feat-icon">{icon}</div>' if icon else badge
        cards_html += (
            f'<div class="feat-card" id="{sid}-c{i}">\n  {icon_html}\n'
            f'  <h3 class="feat-title">{item.get("title", "")}</h3>\n'
            f'  <p class="feat-desc">{item.get("desc", "")}</p>\n</div>\n'
        )

    html = _cards_scene_html(sid, idx, heading, cols, cards_html)

    css = _cards_scene_css_head(sid, theme, grid_gap=18) + (
        f'#{sid} .feat-card {{ background:{theme["surface"]}; border:2px solid {feat_c}20;\n'
        f'  border-radius:16px; padding:32px 28px; display:flex; flex-direction:column;\n'
        f'  gap:8px; box-shadow:{theme["card_shadow"]}; opacity:0; }}\n'
        f'#{sid} .feat-badge {{ display:inline-block; padding:6px 16px; background:{feat_c}15;\n'
        f'  color:{feat_c}; border-radius:20px; font-size:22px; font-weight:700; width:fit-content;\n'
        f'  margin-bottom:8px; }}\n'
        f'#{sid} .feat-icon {{ font-size:48px; margin-bottom:8px; }}\n'
        f'#{sid} .feat-title {{ font-size:36px; font-weight:800; color:{feat_c};\n'
        f'  font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .feat-desc {{ font-size:28px; color:{theme["text_dim"]}; line-height:1.5;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
    )

    anim = _cards_scene_anim(sid, idx, n, theme, t_start, t_end)
    return html, css, anim


def _gen_cards_zone(scene, idx, theme, t_start, t_end, analysis):
    """Zone grid: categorical cards with icon + centered layout."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])
    n = len(items)
    cols = min(n, 4)

    cards_html = ''
    for i, item in enumerate(items):
        icon = item.get('icon', '')
        badge = item.get('badge', '')
        icon_html = f'<div class="zone-icon">{icon}</div>' if icon else (
            f'<span class="zone-badge">{badge}</span>' if badge else ''
        )
        cards_html += (
            f'<div class="zone-card" id="{sid}-c{i}">\n  {icon_html}\n'
            f'  <h3 class="zone-title">{item.get("title", "")}</h3>\n'
            f'  <p class="zone-desc">{item.get("desc", "")}</p>\n</div>\n'
        )

    html = _cards_scene_html(sid, idx, heading, cols, cards_html)

    css = _cards_scene_css_head(sid, theme, grid_gap=18) + (
        f'#{sid} .zone-card {{ background:{theme["surface"]}; border:2px solid {theme["accent1"]}15;\n'
        f'  border-radius:16px; padding:32px 24px; display:flex; flex-direction:column;\n'
        f'  align-items:center; gap:10px; text-align:center; box-shadow:{theme["card_shadow"]}; opacity:0; }}\n'
        f'#{sid} .zone-icon {{ font-size:48px; }}\n'
        f'#{sid} .zone-badge {{ display:inline-block; padding:8px 20px; background:{theme["accent1"]}15;\n'
        f'  color:{theme["accent1"]}; border-radius:20px; font-size:24px; font-weight:700; }}\n'
        f'#{sid} .zone-title {{ font-size:34px; font-weight:800; color:{theme["text"]};\n'
        f'  font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .zone-desc {{ font-size:26px; color:{theme["text_dim"]}; line-height:1.5;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
    )

    anim = _cards_scene_anim(sid, idx, n, theme, t_start, t_end)
    return html, css, anim


def gen_scene_stats(scene, idx, theme, t_start, t_end, analysis=None):
    """Stats 场景：数据统计展示 + count-up 动画。"""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])

    stats_html = ''
    for i, item in enumerate(items):
        unit = item.get('unit', '%')
        stats_html += (
            f'<div class="stat-item" id="{sid}-s{i}">\n'
            f'  <div class="stat-ring">\n'
            f'    <div class="stat-number" data-target="{item.get("value", 0)}">0{unit}</div>\n'
            f'  </div>\n'
            f'  <div class="stat-label">{item.get("label", "")}</div>\n</div>\n'
        )

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  <div class="sc">\n    <h2 class="section-heading">{heading}</h2>\n'
        f'    <div class="stats-row">\n      {stats_html}\n    </div>\n'
        f'    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )

    css = (
        f'#{sid} .sc {{ display:flex; flex-direction:column; align-items:center; padding-top:50px; padding-bottom:calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .section-heading {{ font-size:56px; font-weight:700; color:{theme["text"]};\n'
        f'  margin-bottom:24px; opacity:0; font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .stats-row {{ display:flex; gap:60px; justify-content:center; flex-wrap:wrap; }}\n'
        f'#{sid} .stat-item {{ text-align:center; opacity:0; }}\n'
        f'#{sid} .stat-ring {{ width:220px; height:220px; border-radius:50%;\n'
        f'  border:6px solid {theme["accent1"]}30; display:flex; align-items:center;\n'
        f'  justify-content:center; background:{theme["accent1"]}08; margin:0 auto; }}\n'
        f'#{sid} .stat-number {{ font-size:72px; font-weight:800; color:{theme["accent1"]};\n'
        f'  font-family:{theme["font_heading"]}; line-height:1; }}\n'
        f'#{sid} .stat-label {{ font-size:32px; color:{theme["text_dim"]}; margin-top:20px;\n'
        f'  font-family:{theme["font_body"]}; font-weight:600; }}\n'
    )

    t_ref = f'T.s{idx}'
    anim = anim_fade_up(f'#{sid} .section-heading', f'{t_ref} + 0.3', 0.8)
    anim += anim_drift_in(f'#{sid} .stat-item', f'{t_ref} + 1.0', 'bottom', 0.8, stagger=0.4)
    for i, item in enumerate(items):
        val = item.get('value', 0)
        unit = item.get('unit', '%')
        if isinstance(val, (int, float)):
            t_count = f'{t_ref} + {1.8 + i * 0.4:.1f}'
            anim += (
                f'  tl.to("#{sid}-s{i} .stat-number", {{\n'
                f'    innerText: {val}, duration: 1.5, snap: {{ innerText: 1 }}, ease: "power2.out",\n'
                f'    onUpdate: function() {{\n'
                f'      this.targets()[0].textContent = Math.round(parseFloat(this.targets()[0].innerText)) + "{unit}";\n'
                f'    }}\n  }}, {t_count});\n'
            )

    return html, css, anim


def gen_scene_split(scene, idx, theme, t_start, t_end, analysis=None):
    """Split 场景：Style variants — default/tech/callout"""
    style = (analysis or {}).get('style', 'default')
    if style == 'tech':
        return _gen_split_tech(scene, idx, theme, t_start, t_end, analysis)
    return _gen_split_default(scene, idx, theme, t_start, t_end, analysis)


def _gen_split_default(scene, idx, theme, t_start, t_end, analysis):
    """Default split: image + text with callout styling."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    text = scene.get('text', '')
    image = scene.get('image', '')
    layout = scene.get('layout', 'left-image')

    img_html = f'<img src="{image}" class="split-img" alt="" />' if image else (
        f'<div class="split-placeholder"><span>[Image]</span></div>'
    )
    text_dir = 'row' if layout == 'left-image' else 'row-reverse'

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  <div class="sc">\n    <div class="split-wrap" style="flex-direction:{text_dir};">\n'
        f'      <div class="split-media">{img_html}</div>\n'
        f'      <div class="split-content">\n'
        f'        <h2 class="split-heading">{heading}</h2>\n'
        f'        <p class="split-text">{text}</p>\n'
        f'      </div>\n    </div>\n    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )

    css = (
        f'#{sid} .sc {{ display:flex; align-items:center; justify-content:center; padding:60px 100px calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .split-wrap {{ display:flex; gap:60px; align-items:center; width:100%; max-width:1700px; }}\n'
        f'#{sid} .split-media {{ flex:1; min-width:0; opacity:0; }}\n'
        f'#{sid} .split-img {{ width:100%; height:auto; max-height:700px; border-radius:16px;\n'
        f'  object-fit:cover; box-shadow:{theme["card_shadow"]}; }}\n'
        f'#{sid} .split-placeholder {{ width:100%; height:500px; background:{theme["surface_alt"]};\n'
        f'  border-radius:16px; display:flex; align-items:center; justify-content:center;\n'
        f'  font-size:48px; color:{theme["text_dim"]}; }}\n'
        f'#{sid} .split-content {{ flex:1; min-width:0; }}\n'
        f'#{sid} .split-heading {{ font-size:56px; font-weight:700; color:{theme["text"]};\n'
        f'  margin-bottom:24px; opacity:0; font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .split-text {{ font-size:34px; color:{theme["text_dim"]}; line-height:1.7;\n'
        f'  opacity:0; font-family:{theme["font_body"]}; }}\n'
    )

    t_ref = f'T.s{idx}'
    img_dir = 'left' if layout == 'left-image' else 'right'
    anim = anim_drift_in(f'#{sid} .split-media', f'{t_ref} + 0.3', img_dir, 1.0)
    anim += anim_fade_up(f'#{sid} .split-heading', f'{t_ref} + 1.0', 0.8)
    anim += anim_fade_up(f'#{sid} .split-text', f'{t_ref} + 1.8', 0.8)
    return html, css, anim


def _gen_split_tech(scene, idx, theme, t_start, t_end, analysis):
    """Tech split: annotated image with spec callouts. For structural/technical content."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    text = scene.get('text', '')
    image = scene.get('image', '')
    layout = scene.get('layout', 'left-image')

    img_html = f'<img src="{image}" class="tech-img" alt="" />' if image else (
        f'<div class="tech-placeholder"><span>[Technical Diagram]</span></div>'
    )
    text_dir = 'row' if layout == 'left-image' else 'row-reverse'

    # Extract spec-like data from text (numbers + units)
    specs = re.findall(r'(\d+(?:\.\d+)?(?:mm|kg|dB|°C|h|m²|%)?)', text)
    spec_html = ''
    if specs:
        spec_items = ''.join(f'<span class="spec-chip">{s}</span>' for s in specs[:6])
        spec_html = f'<div class="spec-bar">{spec_items}</div>'

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  <div class="sc">\n    <div class="split-wrap" style="flex-direction:{text_dir};">\n'
        f'      <div class="split-media">{img_html}</div>\n'
        f'      <div class="split-content">\n'
        f'        <span class="tech-tag">TECHNICAL</span>\n'
        f'        <h2 class="split-heading">{heading}</h2>\n'
        f'        <p class="split-text">{text}</p>\n'
        f'        {spec_html}\n'
        f'      </div>\n    </div>\n    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )

    feat_c = theme.get('feature_color', theme['accent1'])
    css = (
        f'#{sid} .sc {{ display:flex; align-items:center; justify-content:center; padding:60px 100px calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .split-wrap {{ display:flex; gap:60px; align-items:center; width:100%; max-width:1700px; }}\n'
        f'#{sid} .split-media {{ flex:1; min-width:0; opacity:0; }}\n'
        f'#{sid} .tech-img {{ width:100%; height:auto; max-height:700px; border-radius:12px;\n'
        f'  object-fit:contain; border:1px solid {theme["accent1"]}15;\n'
        f'  box-shadow:{theme["card_shadow"]}; background:{theme["surface"]}; }}\n'
        f'#{sid} .tech-placeholder {{ width:100%; height:500px; background:{theme["surface_alt"]};\n'
        f'  border-radius:12px; display:flex; align-items:center; justify-content:center;\n'
        f'  font-size:36px; color:{theme["text_dim"]}; }}\n'
        f'#{sid} .split-content {{ flex:1; min-width:0; }}\n'
        f'#{sid} .tech-tag {{ display:inline-block; padding:6px 16px; background:{feat_c}15;\n'
        f'  color:{feat_c}; border-radius:20px; font-size:22px; font-weight:700;\n'
        f'  letter-spacing:0.1em; margin-bottom:12px; opacity:0; font-family:{theme["font_body"]}; }}\n'
        f'#{sid} .split-heading {{ font-size:48px; font-weight:700; color:{theme["text"]};\n'
        f'  margin-bottom:20px; opacity:0; font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .split-text {{ font-size:32px; color:{theme["text_dim"]}; line-height:1.7;\n'
        f'  opacity:0; font-family:{theme["font_body"]}; }}\n'
    )
    if specs:
        css += (
            f'#{sid} .spec-bar {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:24px; }}\n'
            f'#{sid} .spec-chip {{ padding:8px 18px; background:{feat_c}12; color:{feat_c};\n'
            f'  border-radius:8px; font-size:26px; font-weight:700; opacity:0;\n'
            f'  font-family:{theme["font_body"]}; border:1px solid {feat_c}25; }}\n'
        )

    t_ref = f'T.s{idx}'
    img_dir = 'left' if layout == 'left-image' else 'right'
    anim = anim_drift_in(f'#{sid} .split-media', f'{t_ref} + 0.3', img_dir, 1.0)
    anim += anim_fade_up(f'#{sid} .tech-tag', f'{t_ref} + 1.0', 0.5)
    anim += anim_fade_up(f'#{sid} .split-heading', f'{t_ref} + 1.3', 0.8)
    anim += anim_fade_up(f'#{sid} .split-text', f'{t_ref} + 2.0', 0.8)
    if specs:
        anim += anim_drift_in(f'#{sid} .spec-chip', f'{t_ref} + 2.8', 'bottom', 0.5, stagger=0.2)
    return html, css, anim


def gen_scene_list(scene, idx, theme, t_start, t_end, analysis=None):
    """List 场景：Style variants — default/process"""
    style = (analysis or {}).get('style', 'default')
    if style == 'process':
        return _gen_list_process(scene, idx, theme, t_start, t_end, analysis)
    return _gen_list_default(scene, idx, theme, t_start, t_end, analysis)


def _gen_list_default(scene, idx, theme, t_start, t_end, analysis):
    """Default list: vertical items."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])

    items_html = ''
    for i, item in enumerate(items):
        icon = item.get('icon', '●')
        items_html += (
            f'<div class="list-item" id="{sid}-li{i}">\n'
            f'  <span class="list-icon">{icon}</span>\n'
            f'  <div class="list-body">\n'
            f'    <strong class="list-title">{item.get("title", "")}</strong>\n'
            f'    <span class="list-desc">{item.get("desc", "")}</span>\n'
            f'  </div>\n</div>\n'
        )

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  <div class="sc">\n    <h2 class="section-heading">{heading}</h2>\n'
        f'    <div class="list-wrap">{items_html}</div>\n'
        f'    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )

    css = (
        f'#{sid} .sc {{ display:flex; flex-direction:column; align-items:center; padding-top:70px; padding-bottom:calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .section-heading {{ font-size:56px; font-weight:700; color:{theme["text"]};\n'
        f'  margin-bottom:24px; opacity:0; font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .list-wrap {{ display:flex; flex-direction:column; gap:18px; max-width:1600px; width:100%; }}\n'
        f'#{sid} .list-item {{ display:flex; align-items:center; gap:24px;\n'
        f'  background:{theme["surface"]}; padding:28px 36px; border-radius:16px;\n'
        f'  box-shadow:{theme["card_shadow"]}; border:1px solid {theme["accent1"]}15; opacity:0; }}\n'
        f'#{sid} .list-icon {{ font-size:36px; color:{theme["accent1"]}; min-width:48px; text-align:center; }}\n'
        f'#{sid} .list-body {{ display:flex; flex-direction:column; gap:6px; }}\n'
        f'#{sid} .list-title {{ font-size:36px; font-weight:600; color:{theme["text"]};\n'
        f'  font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .list-desc {{ font-size:30px; color:{theme["text_dim"]}; font-family:{theme["font_body"]}; }}\n'
    )

    t_ref = f'T.s{idx}'
    anim = anim_fade_up(f'#{sid} .section-heading', f'{t_ref} + 0.3', 0.8)
    selectors = [f'#{sid}-li{i}' for i in range(len(items))]
    anim += anim_progressive_reveal(selectors, f'{t_ref} + 0.8', t_end - 2, theme, t_ref=t_ref)
    return html, css, anim


def _gen_list_process(scene, idx, theme, t_start, t_end, analysis):
    """Process flow: horizontal step cards with connectors."""
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    items = scene.get('items', [])
    n = len(items)

    steps_html = ''
    for i, item in enumerate(items):
        connector = '<div class="step-connector"></div>' if i < n - 1 else ''
        steps_html += (
            f'<div class="step-card" id="{sid}-st{i}">\n'
            f'  <div class="step-num">{i + 1}</div>\n'
            f'  <h3 class="step-title">{item.get("title", "")}</h3>\n'
            f'  <p class="step-desc">{item.get("desc", "")}</p>\n'
            f'</div>\n{connector}\n'
        )

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  <div class="sc">\n    <h2 class="section-heading">{heading}</h2>\n'
        f'    <div class="process-row">{steps_html}</div>\n'
        f'    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )

    css = (
        f'#{sid} .sc {{ display:flex; flex-direction:column; align-items:center; padding-top:50px; padding-bottom:calc(var(--safe-zone-height) + 60px); }}\n'
        f'#{sid} .section-heading {{ font-size:56px; font-weight:700; color:{theme["text"]};\n'
        f'  margin-bottom:24px; opacity:0; font-family:{theme["font_heading"]}; }}\n'
        f'#{sid} .process-row {{ display:flex; align-items:flex-start; gap:0; max-width:1700px; width:100%; }}\n'
        f'#{sid} .step-card {{ flex:1; background:{theme["surface"]}; border:2px solid {theme["accent1"]}15;\n'
        f'  border-radius:16px; padding:28px 24px; text-align:center; box-shadow:{theme["card_shadow"]}; opacity:0; }}\n'
        f'#{sid} .step-num {{ font-size:48px; font-weight:900; color:{theme["accent1"]}; line-height:1; margin-bottom:12px; }}\n'
        f'#{sid} .step-title {{ font-size:32px; font-weight:700; color:{theme["text"]};\n'
        f'  font-family:{theme["font_heading"]}; margin-bottom:8px; }}\n'
        f'#{sid} .step-desc {{ font-size:26px; color:{theme["text_dim"]}; line-height:1.4;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
        f'#{sid} .step-connector {{ width:40px; height:4px; background:{theme["accent1"]}40;\n'
        f'  margin-top:60px; flex-shrink:0; border-radius:2px; }}\n'
    )

    t_ref = f'T.s{idx}'
    anim = anim_fade_up(f'#{sid} .section-heading', f'{t_ref} + 0.3', 0.8)
    selectors = [f'#{sid}-st{i}' for i in range(n)]
    anim += anim_progressive_reveal(selectors, f'{t_ref} + 0.8', t_end - 2, theme, t_ref=t_ref)
    return html, css, anim


def gen_scene_closing(scene, idx, theme, t_start, t_end, analysis=None):
    """Closing/CTA 场景。"""
    sid = f'scene{idx}'
    title = scene.get('title', '')
    subtitle = scene.get('subtitle', '')
    cta = scene.get('cta', '')

    cta_html = f'<div class="cta-btn">{cta}</div>' if cta else ''

    # Atmosphere glow
    glow_html = (
        f'<div style="position:absolute;width:700px;height:700px;'
        f'background:radial-gradient(circle,{theme["accent1"]}12 0%,transparent 70%);'
        f'top:-200px;left:50%;transform:translateX(-50%);border-radius:50%;pointer-events:none;"></div>\n'
    )

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  {glow_html}'
        f'  <div class="sc">\n    <div class="closing-wrap">\n'
        f'      <h1 class="closing-title">{title}</h1>\n'
        f'      <p class="closing-sub">{subtitle}</p>\n'
        f'      {cta_html}\n'
        f'    </div>\n    <div class="subtitle-safe"></div>\n  </div>\n</div>\n'
    )

    css = (
        f'#{sid} .sc {{ display:flex; align-items:center; justify-content:center; padding-bottom:calc(var(--safe-zone-height) + 60px);\n'
        f'  background:linear-gradient(135deg, {theme["accent1"]}08 0%, {theme["bg"]} 50%, {theme["accent2"]}08 100%); }}\n'
        f'#{sid} .closing-wrap {{ text-align:center; max-width:1500px; }}\n'
        f'#{sid} .closing-title {{ font-size:80px; font-weight:700; color:{theme["text"]};\n'
        f'  line-height:1.15; opacity:0; font-family:{theme["font_heading"]}; text-shadow:{theme["glow"]}; }}\n'
        f'#{sid} .closing-sub {{ font-size:38px; color:{theme["text_dim"]};\n'
        f'  margin-top:20px; opacity:0; font-family:{theme["font_body"]}; }}\n'
        f'#{sid} .cta-btn {{ display:inline-block; margin-top:48px; padding:20px 60px;\n'
        f'  background:linear-gradient(135deg, {theme["accent1"]}, {theme["accent2"]});\n'
        f'  color:#fff; font-size:32px; font-weight:600; border-radius:40px;\n'
        f'  opacity:0; font-family:{theme["font_body"]}; box-shadow:0 8px 32px {theme["accent1"]}50; }}\n'
    )

    t_ref = f'T.s{idx}'
    anim = anim_drift_in(f'#{sid} .closing-title', f'{t_ref} + 0.5', 'bottom', 1.0)
    anim += anim_fade_up(f'#{sid} .closing-sub', f'{t_ref} + 1.5', 0.8)
    if cta:
        anim += anim_drift_in(f'#{sid} .cta-btn', f'{t_ref} + 2.2', 'bottom', 0.7)
    return html, css, anim


# ═══════════════════════════════════════════════════════════════
#  AI 设计路径: Structure 类型 (定制可视化)
# ═══════════════════════════════════════════════════════════════

def gen_scene_structure(scene, idx, theme, t_start, t_end, analysis=None):
    """Structure 场景：技术结构分解。自动路由到最佳可视化。

    SDL 格式:
      - type: structure
        heading: "墙体构造"
        image: "diagram.png"          # 可选参考图
        layers:                        # 结构化层数据
          - {name: "水泥板", thickness: 10, material: "饰面层"}
          - {name: "岩棉", thickness: 50, material: "100kg/m³"}
        annotations:                   # 可选标注卡
          - {label: "双空腔", desc: "弱化共振"}

    路由:
      layers 字段存在 → cross_section (横截面可视化)
      无 layers      → 降级到 split/tech
    """
    layers = scene.get('layers', [])
    if layers:
        return _gen_structure_cross_section(scene, idx, theme, t_start, t_end, analysis)
    # Fallback: no structured data, use tech split
    return gen_scene_split(scene, idx, theme, t_start, t_end,
                           analysis={'style': 'tech', 'info_type': 'structure', 'atmosphere': 'dark'})


def _gen_structure_cross_section(scene, idx, theme, t_start, t_end, analysis):
    """横截面可视化：按比例排列的材料层 + 标注卡。

    视觉设计（对标外部信息图标杆）:
      - 暗色背景突出技术感
      - 层按厚度比例分配宽度（最小5%保证可读性）
      - 每层有材料标签 + 厚度标注
      - 底部注释卡指向关键特征
      - 逐步入场动画（层从左到右，注释从下到上）
    """
    sid = f'scene{idx}'
    heading = scene.get('heading', '')
    layers = scene.get('layers', [])
    annotations = scene.get('annotations', [])
    image = scene.get('image', '')

    # Calculate proportional widths
    total_thickness = sum(l.get('thickness', 10) for l in layers)
    n = len(layers)

    # Color palette for layers (cycle through semantic colors)
    layer_colors = [
        ('#64748b', 'rgba(100,116,139,0.25)'),   # slate (structural)
        ('#3b82f6', 'rgba(59,130,246,0.15)'),     # blue (insulation)
        ('#06b6d4', 'rgba(6,182,212,0.3)'),       # cyan (acoustic)
        ('#3b82f6', 'rgba(59,130,246,0.15)'),     # blue (insulation, repeat)
        ('#64748b', 'rgba(100,116,139,0.25)'),   # slate (structural)
        ('#10b981', 'rgba(16,185,129,0.2)'),      # green (special)
        ('#f59e0b', 'rgba(245,158,11,0.2)'),      # amber (fireproof)
    ]

    # ── Build layer HTML ──
    layers_html = ''
    for i, layer in enumerate(layers):
        thickness = layer.get('thickness', 10)
        pct = max(5, round(thickness / total_thickness * 100))
        name = layer.get('name', f'Layer {i+1}')
        material = layer.get('material', '')
        color, bg = layer_colors[i % len(layer_colors)]

        mat_html = f'<div class="layer-mat">{material}</div>' if material else ''
        layers_html += (
            f'<div class="cs-layer" id="{sid}-L{i}" '
            f'style="width:{pct}%;background:{bg};border-color:{color}40;">\n'
            f'  <div class="layer-name" style="color:{color};">{name}</div>\n'
            f'  <div class="layer-thick">{thickness}mm</div>\n'
            f'  {mat_html}\n'
            f'</div>\n'
        )

    # ── Build annotation cards ──
    anno_html = ''
    if annotations:
        cards = ''
        for i, ann in enumerate(annotations):
            anno_c = theme.get('success_color', '#10b981') if i % 2 == 0 else theme.get('feature_color', theme['accent1'])
            cards += (
                f'<div class="anno-card" id="{sid}-a{i}" style="border-top-color:{anno_c};">\n'
                f'  <div class="anno-label" style="color:{anno_c};">{ann.get("label", "")}</div>\n'
                f'  <div class="anno-desc">{ann.get("desc", "")}</div>\n'
                f'</div>\n'
            )
        anno_html = f'<div class="anno-row">{cards}</div>'

    # ── Optional reference image ──
    img_html = ''
    if image:
        img_html = (
            f'<div class="cs-ref">\n'
            f'  <img src="{image}" class="cs-ref-img" alt="" />\n'
            f'</div>\n'
        )

    # ── Atmosphere glows (dark bg) ──
    glow_html = (
        f'<div style="position:absolute;width:500px;height:500px;'
        f'background:radial-gradient(circle,{theme["accent1"]}08 0%,transparent 70%);'
        f'top:50px;right:100px;border-radius:50%;pointer-events:none;"></div>\n'
    )

    html = (
        f'<div id="{sid}" class="scene" style="z-index:{idx}; opacity:0;" data-scene-entry="gsap" data-scene-subtitle-safe="true">\n'
        f'  {glow_html}'
        f'  <div class="sc">\n'
        f'    <div class="cs-header">\n'
        f'      <span class="cs-tag">CROSS SECTION</span>\n'
        f'      <h2 class="cs-title">{heading}</h2>\n'
        f'    </div>\n'
        f'    <div class="cs-body">\n'
        f'      <div class="cs-wall">{layers_html}</div>\n'
        f'      {anno_html}\n'
        f'    </div>\n'
        f'    {img_html}\n'
        f'    <div class="subtitle-safe"></div>\n'
        f'  </div>\n</div>\n'
    )

    # ── CSS ──
    text_c = '#f0f5fa'
    dim_c = '#94a3b8'
    feat_c = theme.get('feature_color', theme['accent1'])

    css = (
        f'#{sid} .sc {{ display:flex; flex-direction:column; '
        f'gap:16px; padding:50px 100px calc(var(--safe-zone-height) + 60px); }}\n'
        # Header
        f'#{sid} .cs-header {{ display:flex; align-items:center; gap:16px; }}\n'
        f'#{sid} .cs-tag {{ display:inline-block; padding:6px 16px; background:{feat_c}15;\n'
        f'  color:{feat_c}; border-radius:20px; font-size:20px; font-weight:700;\n'
        f'  letter-spacing:0.1em; opacity:0; font-family:{theme["font_body"]}; }}\n'
        f'#{sid} .cs-title {{ font-size:48px; font-weight:900; color:{text_c}; opacity:0;\n'
        f'  font-family:{theme["font_heading"]}; }}\n'
        # Wall cross-section
        f'#{sid} .cs-wall {{ display:flex; align-items:stretch; height:340px;\n'
        f'  border-radius:12px; overflow:hidden; border:2px solid {theme["accent1"]}20;\n'
        f'  box-shadow:0 4px 24px rgba(0,0,0,0.3); }}\n'
        f'#{sid} .cs-layer {{ display:flex; flex-direction:column; align-items:center;\n'
        f'  justify-content:center; position:relative; border-right:1px solid rgba(255,255,255,0.1);\n'
        f'  padding:12px 8px; opacity:0; }}\n'
        f'#{sid} .cs-layer:last-child {{ border-right:none; }}\n'
        f'#{sid} .layer-name {{ font-size:22px; font-weight:700; text-align:center;\n'
        f'  font-family:{theme["font_heading"]}; line-height:1.2; }}\n'
        f'#{sid} .layer-thick {{ font-size:28px; font-weight:900; color:{text_c};\n'
        f'  margin-top:8px; font-family:{theme["font_body"]}; }}\n'
        f'#{sid} .layer-mat {{ font-size:18px; color:{dim_c}; margin-top:4px;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
        # Annotations
        f'#{sid} .anno-row {{ display:flex; gap:16px; padding:0 8px; }}\n'
        f'#{sid} .anno-card {{ flex:1; background:rgba(255,255,255,0.04);\n'
        f'  border:1px solid rgba(255,255,255,0.06); border-top:3px solid {feat_c};\n'
        f'  border-radius:10px; padding:14px 12px; text-align:center; opacity:0; }}\n'
        f'#{sid} .anno-label {{ font-size:22px; font-weight:800; }}\n'
        f'#{sid} .anno-desc {{ font-size:20px; color:{dim_c}; margin-top:4px;\n'
        f'  font-family:{theme["font_body"]}; }}\n'
        # Reference image
        f'#{sid} .cs-ref {{ opacity:0; }}\n'
        f'#{sid} .cs-ref-img {{ height:200px; object-fit:contain; border-radius:10px;\n'
        f'  background:rgba(255,255,255,0.03); border:1px solid {theme["accent1"]}15; }}\n'
    )

    # ── GSAP: progressive layer entry + annotation pop ──
    t_ref = f'T.s{idx}'
    anim = anim_fade_up(f'#{sid} .cs-tag', f'{t_ref} + 0.2', 0.5)
    anim += anim_fade_up(f'#{sid} .cs-title', f'{t_ref} + 0.5', 0.7)

    # Layers enter left-to-right with stagger
    layer_selectors = [f'#{sid}-L{i}' for i in range(n)]
    layer_end_off = 1.2 + n * 1.5
    anim += anim_progressive_reveal(layer_selectors, f'{t_ref} + 1.2', f'{t_ref} + {layer_end_off:.1f}', theme, t_ref=t_ref)

    # Annotations enter after layers
    if annotations:
        anno_selectors = [f'#{sid}-a{i}' for i in range(len(annotations))]
        anno_start_off = layer_end_off + 0.5
        anno_end_off = anno_start_off + len(annotations) * 1.2
        anim += anim_progressive_reveal(anno_selectors, f'{t_ref} + {anno_start_off:.1f}', f'{t_ref} + {anno_end_off:.1f}', theme, t_ref=t_ref)

    # Reference image enters last
    if image:
        img_off = layer_end_off + (len(annotations) * 1.2 if annotations else 0) + 0.5
        anim += anim_drift_in(f'#{sid} .cs-ref', f'{t_ref} + {img_off:.1f}', 'bottom', 0.8)

    return html, css, anim


# 场景类型注册表 (backward compat: SCENE_TYPES exported for tests)
SCENE_TYPES = {
    'hero':      {'func': gen_scene_hero,      'required': ['title']},
    'cards':     {'func': gen_scene_cards,     'required': ['heading', 'items'], 'item_required': ['title', 'desc']},
    'stats':     {'func': gen_scene_stats,     'required': ['heading', 'items'], 'item_required': ['value', 'label']},
    'split':     {'func': gen_scene_split,     'required': ['heading']},
    'list':      {'func': gen_scene_list,      'required': ['heading', 'items'], 'item_required': ['title']},
    'closing':   {'func': gen_scene_closing,   'required': ['title']},
    'structure': {'func': gen_scene_structure, 'required': ['heading']},
}



# ═══════════════════════════════════════════════════════════════
#  ImageGen 清单生成器 (ImageGen Manifest)
# ═══════════════════════════════════════════════════════════════

def generate_imagegen_manifest(scenes_data, analyses, output_dir, theme_name):
    """为需要生成图片的场景创建 ImageGen 清单。

    触发规则（与 AGENTS.md 氛围背景规范对齐）:
      - 场景有 image 字段 → 不触发（使用真实照片）
      - split 场景无 image + 技术内容 → 触发（生成技术图解）
      - 封面/结尾场景 → 触发（生成氛围背景）
      - 纯数据/指标场景 → 不触发
    """
    manifest = {'scenes': [], 'generated': False}

    for i, (scene, analysis) in enumerate(zip(scenes_data, analyses)):
        idx = i + 1
        stype = scene.get('type', '')
        has_image = bool(scene.get('image'))
        atmosphere = analysis.get('atmosphere', '')

        # Determine if ImageGen should trigger
        should_generate = False
        gen_type = ''
        gen_prompt = ''

        if not has_image:
            if atmosphere == 'dark' and (i == 0 or i == len(scenes_data) - 1):
                # Cover or closing with dark atmosphere
                should_generate = True
                gen_type = 'atmosphere'
                gen_prompt = f'Professional {theme_name} themed atmospheric background, abstract gradient, subtle light effects, 1920x1080'
            elif stype == 'split' and analysis.get('style') == 'tech':
                # Technical scene without image
                should_generate = True
                gen_type = 'diagram'
                heading = scene.get('heading', '')
                gen_prompt = f'Technical diagram illustration: {heading}, clean minimal style, blueprint aesthetic, white background'

        if should_generate:
            manifest['scenes'].append({
                'scene_idx': idx,
                'type': gen_type,
                'prompt': gen_prompt,
                'output_path': f'generated_scene{idx}.png',
                'status': 'pending',
            })

    if manifest['scenes']:
        manifest_path = output_dir / 'imagegen_manifest.json'
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        return manifest_path
    return None


# ═══════════════════════════════════════════════════════════════
#  旁白分割工具 (Narration Split — inline to avoid import issues)
# ═══════════════════════════════════════════════════════════════

def _split_narration_segments(narration, group_limit=40, max_seg=60):
    """内联版旁白分割逻辑（避免导入 enhance_video_audio 的 stdout 问题）。"""
    raw = re.split(r'(?<=[。？！；])', narration)
    sents = [x.strip() for x in raw if x.strip()]
    groups = []
    buf = ""
    for s in sents:
        if buf and len(buf) + len(s) > group_limit:
            groups.append(buf); buf = s
        else:
            buf = buf + s
    if buf:
        groups.append(buf)
    segments = []
    for g in groups:
        if len(g) <= group_limit:
            segments.append(g)
        else:
            parts = re.split(r'(?<=[，、])', g)
            parts = [x for x in parts if x.strip()]
            sb = ""
            for p in parts:
                if sb and len(sb) + len(p) > group_limit:
                    segments.append(sb.strip()); sb = p
                else:
                    sb = sb + p
            if sb.strip():
                segments.append(sb.strip())
    return segments


# ═══════════════════════════════════════════════════════════════
#  SDL 增强层 (AI Enrichment Layer)
#  纯机制设计：无领域词表、无硬编码模式、无逐个补丁
# ═══════════════════════════════════════════════════════════════

def _extract_layers(text):
    """从技术文本中提取结构层数据（纯机制）。

    触发信号: 文本中 ≥3 个 Nmm 测量值（数据驱动，不依赖关键词表）
    名称提取: 测量值后连续中文，在下一个数字/厚/标点处停止
    名称清洗: 去尾部连接符 + 长度截断（结构性规则，非领域规则）

    返回: layers [{name, thickness, material}] 或 None
    """
    if not text:
        return None

    # Find all Nmm measurements with their clause context
    measurements = []
    for clause in re.split(r'[，,；;、。：]', text):
        clause = clause.strip()
        if not clause:
            continue
        for m in re.finditer(r'(\d+(?:\.\d+)?)\s*mm', clause):
            measurements.append({
                'value': float(m.group(1)),
                'after': clause[m.end():].strip(),
            })

    if len(measurements) < 3:
        return None

    layers = []
    for meas in measurements:
        name = _extract_layer_name(meas['after'])
        if name:
            thickness = meas['value']
            layers.append({
                'name': name,
                'thickness': int(thickness) if thickness == int(thickness) else thickness,
                'material': name,
            })

    return layers if len(layers) >= 3 else None


def _extract_layer_name(after_text):
    """从测量值后的文本中提取层名称（纯机制）。

    机制:
      1. 捕获连续非数字、非"厚"、非标点字符（最多15字）
      2. 迭代剥离尾部结构后缀（量词+连接符），直到名称稳定
         — 这是通用中文结构规则，不含任何领域词汇
      3. 验证：≥2字、无数字
      4. 长度截断：中文材料名通常 2-6 字，超过6字截断
    """
    # Capture: stop at digit, 厚(thickness suffix), punctuation
    m = re.match(r'([^\d厚，,；;。、:：()（）+\-]{1,15})', after_text)
    if not m:
        return None

    name = m.group(1).strip()

    # Iterative structural suffix stripping (universal Chinese patterns)
    # Quantity descriptors + connectors — NOT domain-specific terms
    structural_suffixes = ['双层', '单层', '与双', '与', '及', '和', '加']
    prev = None
    while prev != name and name:
        prev = name
        for suffix in structural_suffixes:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[:-len(suffix)]
                break

    # Validation
    if not name or len(name) < 2:
        return None
    if re.search(r'\d', name):
        return None

    # Length control: Chinese material names are typically 2-6 chars
    # Truncate to 6 if longer (structural, not domain-specific)
    if len(name) > 6:
        name = name[:6]

    return name


def _extract_annotations(text):
    """从文本中提取摘要性标注卡（通用机制）。

    机制: 匹配"N字中文标签 + ~达为 + 数值 + 单位"模式
    排除: 标签以层/面/侧/间开头（这些是结构描述，不是摘要指标）
    最多 3 个标注

    返回: annotations [{label, value, unit}]
    """
    if not text:
        return []

    annotations = []
    layer_prefixes = ('层', '面', '侧', '间')

    # General pattern: 2-4 Chinese chars + 约/达/为 + number + unit
    for m in re.finditer(r'([\u4e00-\u9fff]{2,4})[约达为]?\s*(\d+(?:\.\d+)?)\s*(mm|kg/m[²2³3]|dB|h|%)', text):
        label = m.group(1)
        value = m.group(2)
        unit = m.group(3)

        # Skip layer-level descriptions (structural filter, not domain-specific)
        if label.startswith(layer_prefixes):
            continue

        # Skip if value is too small (likely a sub-measurement, not a summary)
        if float(value) < 1:
            continue

        annotations.append({
            'label': f'{label}{value}{unit}',
            'desc': '',
        })

    return annotations[:3]


def enrich_sdl(sdl_data):
    """增强 SDL：从叙事文本中自动提取结构化信息。

    触发条件（全部满足才触发）:
      1. 场景 type == 'split'
      2. 场景无 layers 字段
      3. 成功提取 >= 3 个层（数据驱动，不依赖关键词表）

    修改（in-place，仅修改安全字段）:
      type: split → structure
      添加: layers, annotations
      不修改: narration, heading, image

    返回: 增强报告 [{scene_idx, action, layers_count}]
    """
    report = []

    for i, scene in enumerate(sdl_data.get('scenes', [])):
        stype = scene.get('type', '')

        # Only process split scenes without existing layers
        if stype != 'split' or scene.get('layers'):
            continue

        combined = (
            scene.get('heading', '') +
            scene.get('text', '') +
            scene.get('narration', '')
        )

        # Attempt layer extraction (data-driven: ≥3 Nmm measurements)
        text_for_extraction = scene.get('text', '') or scene.get('narration', '')
        layers = _extract_layers(text_for_extraction)

        if layers:
            scene['layers'] = layers

            # Attempt annotation extraction from full context
            annotations = _extract_annotations(combined)
            if annotations:
                scene['annotations'] = annotations

            # Change type to structure for cross_section routing
            scene['type'] = 'structure'

            report.append({
                'scene_idx': i,
                'action': 'split→structure',
                'layers_count': len(layers),
                'annotations_count': len(annotations),
            })
        else:
            # Only report if there were some measurements (near-miss)
            n_mm = len(re.findall(r'\d+\s*mm', text_for_extraction))
            if n_mm > 0:
                report.append({
                    'scene_idx': i,
                    'action': f'skipped ({n_mm} measurements, need ≥3 valid layers)',
                    'layers_count': 0,
                })

    return report


# ═══════════════════════════════════════════════════════════════
#  编译器主逻辑 v2 (Compiler Core)
# ═══════════════════════════════════════════════════════════════

def compile_sdl(sdl_data, output_dir, dry_run=False, auto_enrich=True):
    """
    编译 SDL → HTML + config (v2: 内容感知路由)。

    流程:
      0. 增强: 自动从叙事文本提取结构化信息 (auto_enrich)
      1. 解析 SDL + 选择主题
      2. 内容分析: 每个场景 → 信息类型 + style + atmosphere
      3. 验证: 字段完整性 + 旁白长度
      4. 路由: style → 对应组件生成函数
      5. 组装: HTML + CSS + GSAP 统一拼装
      6. 输出: index.html + config.json + imagegen_manifest.json
    """
    output_dir = Path(output_dir)  # Accept both str and Path

    # ── Step 0: Enrichment ──
    if auto_enrich:
        enrich_report = enrich_sdl(sdl_data)
        for er in enrich_report:
            action = er['action']
            idx = er['scene_idx']
            lc = er['layers_count']
            if '→' in action:
                print(f'  增强: scene{idx+1} {action} ({lc}层)')
            else:
                print(f'  增强: scene{idx+1} {action}')

    video = sdl_data.get('video', {})
    name = video.get('name', 'untitled')
    theme_name = video.get('theme', 'dark-tech')
    theme = THEMES.get(theme_name, THEMES['dark-tech'])
    cover = video.get('cover', {})
    scenes_data = sdl_data.get('scenes', [])

    # ── Step 1: Content Analysis ──
    analyses = []
    for i, s in enumerate(scenes_data):
        analysis = analyze_scene(s, i, len(scenes_data))
        analyses.append(analysis)

    # ── Step 2: Validation ──
    errors = []
    if not scenes_data and not cover:
        errors.append('SDL must have at least one cover or scenes')

    for i, s in enumerate(scenes_data):
        stype = s.get('type', '')
        if stype not in SCENE_TYPES:
            errors.append(f'scene {i+1}: unknown type "{stype}", available: {list(SCENE_TYPES.keys())}')
            continue
        type_info = SCENE_TYPES[stype]
        for field in type_info.get('required', []):
            if field not in s or (isinstance(s[field], str) and not s[field].strip()):
                errors.append(f'scene {i+1} ({stype}): missing required field "{field}"')
        if 'items' in s and 'item_required' in type_info:
            for j, item in enumerate(s['items']):
                for field in type_info['item_required']:
                    if field not in item:
                        errors.append(f'scene {i+1} ({stype}) item {j+1}: missing "{field}"')
        # Narration overflow check
        narration = s.get('narration', '')
        if narration:
            segs = _split_narration_segments(narration)
            for seg in segs:
                if len(seg) > 60:
                    errors.append(f'scene {i+1} ({stype}): subtitle segment {len(seg)} chars > 60: "{seg[:20]}..."')
                    break

    # Narration digit-normalization (AGENTS.md 数字规范).
    # Blocks the SDL compile path from producing HTML/config with Chinese
    # numerals for quantitative values — same rule as preflight_check.py.
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from _narration_lint import scan_sdl_scenes as _lint_scan_sdl
        _digit_findings = _lint_scan_sdl(scenes_data)
        for f in _digit_findings:
            errors.append(
                f'{f.scope}: 命中 “{f.matched}” — 上下文 “…{f.context}…” — {f.suggestion}'
            )
    except Exception as _e:
        print(f'  WARN: narration digit lint skipped: {_e}')

    if errors:
        for e in errors:
            print(f'  ERROR: {e}')
        return None

    # ── Step 3: Timing ──
    cover_dur = 3.0 if cover else 0.0
    default_scene_dur = video.get('default_scene_duration', 30.0)
    scene_durations = []
    for s in scenes_data:
        narration = s.get('narration', '')
        if narration:
            est_dur = max(15, len(narration) / 4.0 + 3.0)
            scene_durations.append(round(est_dur, 1))
        else:
            scene_durations.append(default_scene_dur)

    scene_starts = []
    t = 0.0
    for dur in scene_durations:
        scene_starts.append(round(t, 1))
        t += dur
    total_content_dur = round(t, 1)
    video_duration = round(total_content_dur + cover_dur, 1)

    if not dry_run:
        print(f'\n编译: {name}')
        print(f'  主题: {theme_name}')
        print(f'  场景: {len(scenes_data)} 个内容场景' + (f' + 封面' if cover else ''))
        print(f'  时长: {video_duration}s (内容 {total_content_dur}s + 封面 {cover_dur}s)')
        # Print routing decisions
        for i, (s, a) in enumerate(zip(scenes_data, analyses)):
            style = a.get('style', 'default')
            atmo = a.get('atmosphere', '')
            flags = []
            if style and style != 'default':
                flags.append(f'style={style}')
            if atmo:
                flags.append(f'atmo={atmo}')
            flag_str = f' [{", ".join(flags)}]' if flags else ''
            print(f'  scene{i+1} ({s.get("type","?")}): {a.get("info_type","?")}{flag_str}')

    # ── Step 4: Generate Cover ──
    cover_html = ''
    cover_css = ''
    cover_anim = ''
    if cover:
        title = cover.get('title', name)
        subtitle = cover.get('subtitle', '')
        keywords = cover.get('keywords', [])

        kw_html = ''
        if keywords:
            kw_items = ''.join(f'<span class="kw-tag">{k}</span>' for k in keywords)
            kw_html = f'<div class="kw-row">{kw_items}</div>'

        # Cover glow orbs
        glow_html = (
            f'<div style="position:absolute;width:800px;height:800px;'
            f'background:radial-gradient(circle,{theme["accent1"]}15 0%,transparent 70%);'
            f'top:-200px;right:-100px;border-radius:50%;pointer-events:none;"></div>\n'
            f'<div style="position:absolute;width:600px;height:600px;'
            f'background:radial-gradient(circle,{theme["accent2"]}10 0%,transparent 70%);'
            f'bottom:-150px;left:-100px;border-radius:50%;pointer-events:none;"></div>\n'
        )

        cover_html = (
            f'<div id="scene0" class="scene" style="z-index:999;" data-scene-entry="cover" data-scene-subtitle-safe="true">\n'
            f'  {glow_html}'
            f'  <div class="sc" style="display:flex;align-items:center;justify-content:center;">\n'
            f'    <div class="hero-wrap">\n'
            f'      <h1 class="hero-title">{title}</h1>\n'
            f'      <p class="hero-sub">{subtitle}</p>\n'
            f'      {kw_html}\n'
            f'    </div>\n'
            f'    <div class="subtitle-safe"></div>\n'
            f'  </div>\n</div>\n'
        )
        cover_css = (
            f'#scene0 .hero-title {{ font-size:96px; font-weight:700; color:{theme["text"]};\n'
            f'  text-align:center; line-height:1.15; letter-spacing:-2px;\n'
            f'  font-family:{theme["font_heading"]}; text-shadow:{theme["glow"]}; }}\n'
            f'#scene0 .hero-sub {{ font-size:44px; color:{theme["text_dim"]}; text-align:center;\n'
            f'  margin-top:24px; font-family:{theme["font_body"]}; }}\n'
        )
        if keywords:
            cover_css += (
                f'#scene0 .kw-row {{ display:flex; gap:16px; justify-content:center; margin-top:40px; flex-wrap:wrap; }}\n'
                f'#scene0 .kw-tag {{ padding:10px 28px; background:{theme["surface_alt"]}; color:{theme["accent2"]};\n'
                f'  border-radius:30px; font-size:28px; font-weight:500; border:1px solid {theme["accent1"]}40; }}\n'
            )
        cover_anim = (
            f'  // Cover fade-out at {cover_dur}s\n'
            f'  tl.to("#scene0", {{ opacity: 0, duration: 0.3 }}, {cover_dur - 0.3});\n'
            f'  tl.set("#scene0", {{ visibility: "hidden" }}, {cover_dur});\n'
            f'  tl.set("#scene1", {{ opacity: 1 }}, {cover_dur});\n'
        )

    # ── Step 5: Generate Content Scenes (routed) ──
    all_html = ''
    all_css = cover_css
    all_anim = cover_anim

    # Build T-block: structured timeline mapping (scene index → absolute start time)
    # When adjust_timeline rewrites this block, all GSAP positions auto-follow.
    t_block_entries = []
    for i in range(len(scenes_data)):
        abs_start = scene_starts[i] + cover_dur
        t_block_entries.append(f's{i + 1}: {abs_start:.1f}')
    t_block_js = 'var T = { ' + ', '.join(t_block_entries) + ' };'

    # Build S-block: authoritative scene metadata (single source of truth)
    s_block_entries = []
    if cover:
        s_block_entries.append(json.dumps(
            {"start": 0, "end": cover_dur, "dur": cover_dur, "type": "cover", "cover": True}
        ))
    for i in range(len(scenes_data)):
        abs_start = scene_starts[i] + cover_dur
        abs_end = abs_start + scene_durations[i]
        s_block_entries.append(json.dumps(
            {"start": abs_start, "end": abs_end, "dur": scene_durations[i],
             "type": scenes_data[i].get("type", "hero"), "cover": False}
        ))
    s_block_js = 'var S = [' + ',\n    '.join(s_block_entries) + '];'

    for i, sdata in enumerate(scenes_data):
        idx = i + 1
        stype = sdata.get('type', 'hero')
        analysis = analyses[i]
        t_start = scene_starts[i] + cover_dur
        t_end = t_start + scene_durations[i]
        t_ref = f'T.s{idx}'

        # Route to the correct generator with analysis context
        gen_func = SCENE_TYPES[stype]['func']
        html, css, anim = gen_func(sdata, idx, theme, t_start, t_end, analysis=analysis)

        # Apply atmosphere overlay
        atmo_css, atmo_html = gen_atmosphere_bg(idx, analysis.get('atmosphere', ''), theme)
        if atmo_css:
            css = atmo_css + css
        if atmo_html:
            html = html.replace(f'<div id="scene{idx}"', atmo_html + f'<div id="scene{idx}"', 1)

        # Inject decoration layer (grid + ambient + glows + scan + dots + ring)
        deco_html, deco_anim = gen_scene_decorations(idx, theme, t_start, t_end, t_ref=t_ref)
        html = html.replace(
            f'<div id="scene{idx}"',
            deco_html + f'<div id="scene{idx}"',
            1
        )
        all_anim += f'\n{deco_anim}'

        all_html += f'\n{html}'
        all_css += f'\n/* Scene {idx}: {stype} [{analysis.get("style","default")}] */\n{css}'
        all_anim += f'\n  // Scene {idx} animations ({t_start:.1f}s - {t_end:.1f}s)\n{anim}'

        # Transition (not last scene) — use T-block reference for structured timeline
        if i < len(scenes_data) - 1:
            next_idx = idx + 1
            trans_t = f'T.s{next_idx} - 0.8'
            direction = _pick_transition(idx)
            all_anim += f'\n{gen_transition(idx, next_idx, trans_t, direction)}'

    # ── End fade ── (structural, not tied to scene content — absolute time is correct)
    end_fade_t = video_duration - 1.5
    all_html += (
        f'\n<div id="scene-end-fade" style="position:absolute;top:0;left:0;width:1920px;height:1080px;\n'
        f'  background:#000;opacity:0;z-index:998;pointer-events:none;"></div>\n'
    )
    all_anim += f'\n  // End fade\n  tl.to("#scene-end-fade", {{ opacity: 1, duration: 1.0 }}, {end_fade_t:.1f});\n'

    # ── Step 6: Assemble Full HTML ──
    full_html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=1920,height=1080">
  <title>{name}</title>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    /* ── SAFE ZONE: 修改此变量即可全局调整字幕安全区高度 ── */
    :root {{ --safe-zone-height: 325px; }}
    body {{ width:1920px; height:1080px; overflow:hidden;
      background:{theme['bg']}; font-family:{theme['font_body']}; }}
    .scene {{ position:absolute; top:0; left:0; width:1920px; height:1080px; overflow:hidden; }}
    .sc {{ position:relative; width:100%; height:100%;
      padding:60px 100px calc(var(--safe-zone-height) + 60px); overflow:hidden;
      background:{theme['bg_gradient']}; display:flex; flex-direction:column; gap:16px; }}
    .subtitle-safe {{ position:absolute; bottom:0; left:0; width:100%;
      height:var(--safe-zone-height); z-index:100; pointer-events:none;
      background:linear-gradient(to bottom,rgba(10,15,30,0) 0%,rgba(10,15,30,0.88) 40%,rgba(10,15,30,0.96) 100%); }}
{all_css}
  </style>
</head>
<body>
<div id="root" data-composition-id="main" data-start="0"
     data-duration="{video_duration}" data-cover-duration="{cover_dur}"
     data-width="1920" data-height="1080">

{cover_html}
{all_html}

</div>

<script>
  window.__timelines = window.__timelines || {{}};
  var tl = gsap.timeline({{ paused: true }});

  // ── T-block: structured timeline mapping (adjust_timeline rewrites this block) ──
  {t_block_js}

  // ── S-block: authoritative scene metadata (single source of truth) ──
  {s_block_js}

{all_anim}

  window.__timelines["main"] = tl;
</script>
</body>
</html>'''

    # ── Step 7: Generate Pipeline Config ──
    # TTS 默认对齐全仓主引擎 Qwen（AGENTS.md TTS 引擎纪律）：
    # 旧默认 zh-CN-YunxiNeural 属 Edge-TTS，与 qwen 引擎搭配会被一致性门禁拒收。
    config = {
        'video_duration': video_duration,
        'cover_duration': cover_dur,
        'tts_engine': video.get('tts_engine', 'qwen'),
        'voice': video.get('voice', 'longanling_v3'),
        'rate': video.get('rate', '+5%'),
        'pitch': video.get('pitch', '+0Hz'),
        'scenes': [],
        'paths': {
            'video_name': f'{name}.mp4',
            'subtitle_name': f'{name}.srt',
            'temp_subdir': f'{name}_audio',
            'html_project': output_dir.name,
        }
    }
    for i, sdata in enumerate(scenes_data):
        config['scenes'].append({
            'scene_id': i + 1,
            'start': scene_starts[i],
            'end': round(scene_starts[i] + scene_durations[i], 1),
            'narration': sdata.get('narration', ''),
        })

    if dry_run:
        print(f'  [DRY RUN] validation passed')
        print(f'  Routing: {[(s.get("type"), analyses[i].get("style","default")) for i, s in enumerate(scenes_data)]}')
        return None

    # ── Step 8: Output Files ──
    output_dir.mkdir(parents=True, exist_ok=True)

    html_path = output_dir / 'index.html'
    html_path.write_text(full_html, encoding='utf-8')
    print(f'  HTML: {html_path}')

    bak_path = output_dir / 'index.html.bak'
    shutil.copy2(str(html_path), str(bak_path))
    print(f'  BAK:  {bak_path}')

    config_path = output_dir / f'{name}_config.json'
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    std_config_dir = ROOT / '程序文件' / '配置' / 'config' / 'pipelines'
    std_config_dir.mkdir(parents=True, exist_ok=True)
    std_config_path = std_config_dir / f'{name}_config.json'
    shutil.copy2(str(config_path), str(std_config_path))
    print(f'  JSON: {config_path} -> {std_config_path}')

    # ImageGen manifest
    manifest_path = generate_imagegen_manifest(scenes_data, analyses, output_dir, theme_name)
    if manifest_path:
        print(f'  IMG:  {manifest_path} ({len(json.loads(manifest_path.read_text(encoding="utf-8"))["scenes"])} scenes need images)')

    # ── Compile Report ──
    report_lines = [
        f'=== Compile Report ===',
        f'Project: {name}',
        f'Theme: {theme_name}',
        f'Scenes: {len(scenes_data)}' + (f' + cover' if cover else ''),
        f'Duration: {video_duration}s',
        f'',
        f'Routing decisions:',
    ]
    for i, (sdata, analysis) in enumerate(zip(scenes_data, analyses)):
        idx = i + 1
        stype = sdata.get('type', '?')
        style = analysis.get('style', 'default')
        atmo = analysis.get('atmosphere', '')
        t_s = scene_starts[i] + cover_dur
        t_e = t_s + scene_durations[i]
        narr_preview = sdata.get('narration', '')[:40]
        report_lines.append(
            f'  scene{idx} [{stype}/{style}] {t_s:.1f}s-{t_e:.1f}s'
            + (f' atmo={atmo}' if atmo else '')
            + f' | {narr_preview}...'
        )
    print('\n'.join(report_lines))

    return html_path, config_path


# ═══════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Scene Compiler v2: SDL -> HTML+GSAP+Config')
    parser.add_argument('sdl', help='SDL file path (YAML)')
    parser.add_argument('--output-dir', '-o', help='Output directory')
    parser.add_argument('--dry-run', action='store_true', help='Validate only')
    parser.add_argument('--theme', help='Override theme')
    parser.add_argument('--no-enrich', action='store_true', help='Skip auto-enrichment')
    parser.add_argument('--enrich-only', action='store_true', help='Show enrichment results without compiling')
    args = parser.parse_args()

    sdl_path = Path(args.sdl)
    if not sdl_path.exists():
        print(f'ERROR: SDL not found: {sdl_path}'); sys.exit(1)

    with open(sdl_path, 'r', encoding='utf-8') as f:
        sdl_data = yaml.safe_load(f)

    if args.theme:
        sdl_data.setdefault('video', {})['theme'] = args.theme

    # Enrich-only mode: show what would be extracted
    if args.enrich_only:
        report = enrich_sdl(sdl_data)
        print(f'增强报告 ({len(report)} 场景):')
        for r in report:
            idx = r['scene_idx']
            scene = sdl_data['scenes'][idx]
            print(f'  scene{idx+1}: {r["action"]}')
            if 'layers' in scene:
                for l in scene['layers']:
                    print(f'    - {l["name"]} {l["thickness"]}mm')
            if 'annotations' in scene:
                for a in scene['annotations']:
                    print(f'    标注: {a["label"]} — {a["desc"]}')
        return

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        name = sdl_data.get('video', {}).get('name', 'untitled')
        output_dir = ROOT / '程序文件' / '源码' / 'hyperframes' / name

    result = compile_sdl(sdl_data, output_dir, dry_run=args.dry_run,
                         auto_enrich=not args.no_enrich)
    if result is None and not args.dry_run:
        print('\nCompile failed.'); sys.exit(1)
    elif result:
        print(f'\nDone! Run pipeline:')
        print(f'  python 程序文件\\脚本\\pipeline_runner.py --config {result[1].name}')


if __name__ == '__main__':
    main()
