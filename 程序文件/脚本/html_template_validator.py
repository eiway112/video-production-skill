#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HTML 模板校验器 (HTML Template Validator)

对照踩坑清单对 HyperFrames HTML 做视觉基准合规校验。
独立于 Codex 维护的 preflight_check.py（技术预检），本脚本专注 AI 导演模式的视觉规范。

检查项（来源：新路径实施_踩坑清单与防范检查表.md 阶段C）：
  1. 占位符残留 — [Image]、placeholder、src=""
  2. 图片引用存在性 — 所有 <img src> 引用真实文件
  3. subtitle-safe 高度 — 1080 基准 120-150px（成功项目基准 130-140px）
  4. .sc padding-bottom — ≥ safe-height + 20px（1080 基准推荐 150-170px）
  5. 装饰层 opacity — 主光晕 0.08-0.15，最低 ≥ 0.06
  6. 装饰元素 y 坐标 — 底边 ≤ 1080 基准 840px（远离字幕安全线）
     以上 px 阈值均随 HTML 声明的画布高度等比换算，竖版不沿用 1080 数值
  7. 封面时长 — data-cover-duration 应为 3
  8. config.json 场景数与 HTML scene 数一致

Usage:
  python html_template_validator.py --project hospital-partition-wall
  python html_template_validator.py --project hospital-partition-wall --log
  python html_template_validator.py --project hospital-partition-wall --json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

# Fix GBK encoding in PowerShell（import 即生效）+ 公共路径/日志
from _script_env import ROOT, HTML_BASE, LOG_PATH, append_audit_log  # noqa: E402, F401

# ===== 成功项目参数基准（踩坑清单 §八） =====
# 基准值为 1080 高画布的实测 px，实际判据按 HTML 声明的画布高度等比换算
# （见 canvas_px / extract_canvas_height）：竖版 1920 高沿用 1080 常量会把
# 合规版面判成越界。h=1080 时换算结果与基准值逐位一致。
BASELINE_FRAME_H = 1080
SAFE_HEIGHT_RANGE = (120, 150)          # subtitle-safe height px
PADDING_BOTTOM_MIN_OFFSET = 20          # padding-bottom ≥ safe-height + 此值
PADDING_BOTTOM_RECOMMENDED = (150, 170) # 推荐范围
PADDING_BOTTOM_TOLERANCE = 30           # 超过推荐上限此值才判"过大"
GLOW_OPACITY_MIN = 0.06                # 装饰层最低 opacity
GLOW_OPACITY_RECOMMENDED = (0.08, 0.15)  # 主光晕推荐
DECOR_MAX_Y = 840                       # 装饰元素底边 y 上限
COVER_DURATION_EXPECTED = "3"           # 封面时长秒


def canvas_px(baseline_px, frame_h):
    """1080 基准 px → 该画布高度的等比值。"""
    return round(baseline_px * frame_h / BASELINE_FRAME_H)


def extract_canvas_height(content: str) -> int:
    """HTML 声明的画布高度（data-height 或 body{height}），缺失回退 1080 基准。"""
    m = re.search(r'data-width="\d+"[^>]*data-height="(\d+)"', content)
    if not m:
        m = re.search(r'body\s*\{[^}]*height\s*:\s*(\d+)px', content)
    if m and int(m.group(1)) > 0:
        return int(m.group(1))
    return BASELINE_FRAME_H


def check_placeholders(content: str) -> list:
    """检查占位符残留。"""
    issues = []
    patterns = [
        (r'\[Image\]', '[Image] 占位符'),
        (r'placeholder', 'placeholder 标记'),
        (r'src=""', '空 src 属性'),
        (r"src=''", '空 src 属性'),
    ]
    for pat, desc in patterns:
        matches = re.findall(pat, content, re.IGNORECASE)
        if matches:
            issues.append(f"发现 {len(matches)} 处{desc}")
    return issues


def check_image_refs(content: str, project_dir: Path) -> list:
    """检查图片引用存在性。"""
    issues = []
    refs = re.findall(r'src="([^"]+\.(?:jpg|jpeg|png|gif|webp))"', content, re.IGNORECASE)
    refs += re.findall(r"url\(['\"]?([^)]+\.(?:jpg|jpeg|png|gif|webp))['\"]?\)", content, re.IGNORECASE)
    # 过滤 http
    local_refs = [r for r in refs if not r.startswith('http')]

    for ref in local_refs:
        # 正确解析相对路径（含 ../ 序列）
        img_path = (project_dir / ref).resolve()
        if not img_path.exists():
            issues.append(f"图片不存在: {ref}")
    return issues


def extract_safe_height(content: str) -> int | None:
    """提取 .subtitle-safe 的 height 值。"""
    # CSS 类定义: .subtitle-safe{...height:NNNpx...}
    m = re.search(r'\.subtitle-safe\s*\{[^}]*height\s*:\s*(\d+)\s*px', content)
    if m:
        return int(m.group(1))
    # 内联 style
    m = re.search(r'class="subtitle-safe"[^>]*style="[^"]*height\s*:\s*(\d+)\s*px', content)
    if m:
        return int(m.group(1))
    return None


def extract_padding_bottom(content: str) -> list:
    """提取所有 .sc 的 padding-bottom 值。"""
    values = []
    # CSS 类: .sc{...padding:TOP RIGHT BOTTOM...} 或 padding-bottom:NNNpx
    m = re.search(r'\.sc\s*\{[^}]*padding\s*:\s*(\d+)\s*px\s+(\d+)\s*px\s+(\d+)\s*px', content)
    if m:
        values.append(('CSS .sc', int(m.group(3))))
    # 内联 padding-bottom
    for m in re.finditer(r'padding-bottom\s*:\s*(\d+)\s*px', content):
        values.append(('inline', int(m.group(1))))
    return values


def check_safe_zone(content: str, frame_h: int = BASELINE_FRAME_H) -> list:
    """校验字幕安全区参数（阈值按画布高度等比换算）。"""
    issues = []
    safe_h = extract_safe_height(content)
    safe_min = canvas_px(SAFE_HEIGHT_RANGE[0], frame_h)
    safe_max = canvas_px(SAFE_HEIGHT_RANGE[1], frame_h)
    pad_min_offset = canvas_px(PADDING_BOTTOM_MIN_OFFSET, frame_h)
    pad_rec = (canvas_px(PADDING_BOTTOM_RECOMMENDED[0], frame_h),
               canvas_px(PADDING_BOTTOM_RECOMMENDED[1], frame_h))

    if safe_h is None:
        issues.append("未找到 .subtitle-safe height 定义")
    else:
        if safe_h < safe_min:
            issues.append(
                f"subtitle-safe height={safe_h}px < {safe_min}px"
                f"（画布高{frame_h}，过低，字幕可能溢出）")
        elif safe_h > safe_max:
            issues.append(
                f"subtitle-safe height={safe_h}px > {safe_max}px"
                f"（画布高{frame_h}，过高，画面底部被遮挡）")

    # padding-bottom 检查
    paddings = extract_padding_bottom(content)
    for source, pb_val in paddings:
        if source == 'CSS .sc':
            # 全局 .sc 定义：严格校验
            if safe_h and pb_val < safe_h + pad_min_offset:
                issues.append(
                    f"CSS .sc padding-bottom={pb_val}px < safe-height({safe_h})+{pad_min_offset}"
                    f"（内容可能侵入字幕区）")
            elif pb_val > pad_rec[1] + canvas_px(PADDING_BOTTOM_TOLERANCE, frame_h):
                issues.append(
                    f"CSS .sc padding-bottom={pb_val}px 过大"
                    f"（画布高{frame_h}，推荐 {pad_rec[0]}-{pad_rec[1]}px）")
        else:
            # inline 覆盖：可能是封面场景居中布局，放宽阈值
            if safe_h and pb_val < safe_h:
                issues.append(
                    f"inline padding-bottom={pb_val}px < safe-height({safe_h})"
                    f"（即使是封面也不应小于安全区高度）")

    return issues


def check_glow_opacity(content: str) -> list:
    """校验装饰层 opacity。"""
    issues = []
    # 提取 .glow 元素中的 rgba opacity
    glow_matches = re.findall(
        r'class="glow"[^>]*style="[^"]*rgba\([^)]*,\s*([\d.]+)\)',
        content
    )
    if glow_matches:
        opacities = [float(v) for v in glow_matches]
        too_low = [v for v in opacities if v < GLOW_OPACITY_MIN]
        if too_low:
            issues.append(
                f"{len(too_low)}/{len(opacities)} 个 glow 元素 opacity < {GLOW_OPACITY_MIN}"
                f"（最低 {min(too_low):.2f}，几乎不可见）")
    return issues


def check_decoration_y(content: str, frame_h: int = BASELINE_FRAME_H) -> list:
    """校验装饰元素 y 坐标（底边 ≤ 等比换算后的上限，1080 基准 840px）。"""
    issues = []
    decor_max_y = canvas_px(DECOR_MAX_Y, frame_h)
    # 提取 .glow/.ghost 等装饰元素的 top 值
    decor_patterns = re.findall(
        r'class="(?:glow|ghost)"[^>]*style="[^"]*?(?:top|bottom)\s*:\s*(-?\d+)\s*px[^"]*?'
        r'(?:height\s*:\s*(\d+)\s*px)?',
        content
    )
    for top_str, height_str in decor_patterns:
        top = int(top_str)
        height = int(height_str) if height_str else 0
        # 估算底边 = top + height（仅对正 top 值有效）
        if top >= 0:
            bottom = top + height
            if bottom > decor_max_y and height > 0:
                issues.append(
                    f"装饰元素 bottom≈{bottom}px > {decor_max_y}px"
                    f"（画布高{frame_h}，侵入字幕安全线）")
    return issues


def check_cover_duration(content: str) -> list:
    """校验封面时长。"""
    issues = []
    m = re.search(r'data-cover-duration="([^"]*)"', content)
    if m:
        val = m.group(1)
        if val != COVER_DURATION_EXPECTED:
            issues.append(
                f"data-cover-duration={val}（期望 {COVER_DURATION_EXPECTED}s）")
    else:
        issues.append("未找到 data-cover-duration 属性")
    return issues


def check_scene_count(content: str, project_dir: Path) -> list:
    """校验 config.json 与 HTML 的场景数一致性。"""
    issues = []
    config_path = project_dir / "config.json"
    if not config_path.exists():
        return issues  # config.json 不存在时跳过

    # HTML 场景数
    html_scenes = set(re.findall(r'id="(scene\d+)"', content))
    html_count = len(html_scenes)

    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        config_scenes = config.get('scenes', [])
        config_count = len(config_scenes)

        if html_count != config_count:
            issues.append(
                f"HTML 场景数({html_count}) ≠ config.json 场景数({config_count})")
    except (json.JSONDecodeError, KeyError):
        issues.append("config.json 解析失败")

    return issues


def run_validation(project: str) -> dict:
    """执行全部校验。"""
    project_dir = HTML_BASE / project
    html_path = project_dir / "index.html"

    result = {
        'project': project,
        'check_time': datetime.now().isoformat(),
        'checks': [],
        'pass': True,
    }

    if not html_path.exists():
        result['checks'].append({
            'name': 'HTML 文件',
            'status': 'FAIL',
            'detail': f'index.html 不存在: {html_path}',
        })
        result['pass'] = False
        return result

    content = html_path.read_text(encoding='utf-8')
    frame_h = extract_canvas_height(content)

    # 1. 占位符
    ph = check_placeholders(content)
    result['checks'].append({
        'name': '占位符残留',
        'status': 'FAIL' if ph else 'PASS',
        'detail': '; '.join(ph) if ph else '无占位符',
    })
    if ph:
        result['pass'] = False

    # 2. 图片引用
    img_issues = check_image_refs(content, project_dir)
    result['checks'].append({
        'name': '图片引用存在性',
        'status': 'FAIL' if img_issues else 'PASS',
        'detail': '; '.join(img_issues) if img_issues else '全部图片引用有效',
    })
    if img_issues:
        result['pass'] = False

    # 3. 字幕安全区
    sz_issues = check_safe_zone(content, frame_h)
    result['checks'].append({
        'name': '字幕安全区',
        'status': 'FAIL' if sz_issues else 'PASS',
        'detail': '; '.join(sz_issues) if sz_issues else f'safe-height 和 padding-bottom 在规范范围内',
    })
    if sz_issues:
        result['pass'] = False

    # 4. 装饰层 opacity
    glow_issues = check_glow_opacity(content)
    result['checks'].append({
        'name': '装饰层 opacity',
        'status': 'WARN' if glow_issues else 'PASS',
        'detail': '; '.join(glow_issues) if glow_issues else 'glow opacity 在推荐范围内',
    })

    # 5. 装饰元素 y 坐标
    y_issues = check_decoration_y(content, frame_h)
    result['checks'].append({
        'name': '装饰元素 y 坐标',
        'status': 'WARN' if y_issues else 'PASS',
        'detail': '; '.join(y_issues) if y_issues
        else f'装饰底边 ≤ {canvas_px(DECOR_MAX_Y, frame_h)}px（画布高{frame_h}）',
    })

    # 6. 封面时长
    cover_issues = check_cover_duration(content)
    result['checks'].append({
        'name': '封面时长',
        'status': 'FAIL' if cover_issues else 'PASS',
        'detail': '; '.join(cover_issues) if cover_issues else f'cover-duration = {COVER_DURATION_EXPECTED}s',
    })
    if cover_issues:
        result['pass'] = False

    # 7. 场景数一致性
    scene_issues = check_scene_count(content, project_dir)
    result['checks'].append({
        'name': '场景数一致性',
        'status': 'FAIL' if scene_issues else 'PASS',
        'detail': '; '.join(scene_issues) if scene_issues else 'HTML 与 config.json 场景数一致',
    })
    if scene_issues:
        result['pass'] = False

    # 统计
    scene_ids = re.findall(r'id="(scene\d+)"', content)
    result['stats'] = {
        'scene_count': len(set(scene_ids)),
        'html_lines': content.count('\n') + 1,
        'safe_height': extract_safe_height(content),
        'canvas_height': frame_h,
    }

    return result


def write_audit_log(result: dict):
    """将校验结果追加到结构化日志。"""
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'tool': 'html_template_validator',
        'project': result.get('project', ''),
        'pass': result.get('pass', False),
        'checks': [
            {'name': c['name'], 'status': c['status'], 'detail': c['detail']}
            for c in result.get('checks', [])
        ],
        'stats': result.get('stats', {}),
    }
    append_audit_log(log_entry)


def print_report(result: dict):
    """打印校验报告。"""
    print("=" * 60)
    print("HTML 模板视觉基准校验报告")
    print(f"项目: {result['project']}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    stats = result.get('stats', {})
    print(f"\n场景数: {stats.get('scene_count', '?')}")
    print(f"HTML 行数: {stats.get('html_lines', '?')}")
    print(f"safe-height: {stats.get('safe_height', '?')}px")

    for check in result['checks']:
        status = check['status']
        icon = {'PASS': '+', 'FAIL': 'X', 'WARN': '!', 'SKIP': '-'}.get(status, '?')
        print(f"\n  [{icon}] {check['name']}: {status}")
        print(f"      {check['detail']}")

    fail_count = sum(1 for c in result['checks'] if c['status'] == 'FAIL')
    warn_count = sum(1 for c in result['checks'] if c['status'] == 'WARN')

    print(f"\n{'=' * 60}")
    if result['pass']:
        print(f"结论: PASS  (警告: {warn_count})")
    else:
        print(f"结论: FAIL  ({fail_count} 项未通过，{warn_count} 项警告)")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description='HTML 模板视觉基准校验器')
    parser.add_argument('--project', required=True, help='项目名称')
    parser.add_argument('--json', action='store_true', help='JSON 输出')
    parser.add_argument('--log', action='store_true', help='将校验结果写入结构化日志')
    args = parser.parse_args()

    result = run_validation(args.project)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)

    if args.log:
        write_audit_log(result)
        if not args.json:
            print(f"\n[LOG] 校验结果已写入: {LOG_PATH}")

    return 0 if result['pass'] else 1


if __name__ == "__main__":
    sys.exit(main())
