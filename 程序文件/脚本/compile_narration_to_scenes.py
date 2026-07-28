#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编译旁白到规范化场景模型（_compiled_scenes.json）

功能：
  从 narration.json 编译生成规范化场景数据模型
  该文件作为所有消费者（TTS、Timeline、Render、Verify）的唯一权威源

用法：
  python compile_narration_to_scenes.py \
    --narration-file narration.json \
    --output-file _compiled_scenes.json \
    --project-name "项目名称" \
    --total-duration 313
"""

import json
import os
import sys
import hashlib
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple
from pathlib import Path

def get_git_commit_hash():
    """获取当前 Git commit hash"""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()[:8]  # 缩短为 8 位
    except Exception:
        pass
    return "unknown"

def compute_file_hash(filepath: str) -> str:
    """计算文件的 SHA256 哈希"""
    try:
        with open(filepath, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception as e:
        return f"error:{str(e)}"

def validate_scenes(scenes: List[Dict], total_duration: float) -> Tuple[bool, List[str]]:
    """
    验证场景列表的有效性
    
    返回：(is_valid, error_messages)
    """
    errors = []
    
    if not scenes:
        errors.append("场景列表不能为空")
        return False, errors
    
    # 检查至少有一个非封面场景
    non_cover_scenes = [s for s in scenes if s.get('type') != 'cover']
    if not non_cover_scenes:
        errors.append("必须至少包含一个非封面场景")
    
    # 检查场景 ID 唯一性
    scene_ids = [s.get('scene_id') for s in scenes]
    if len(scene_ids) != len(set(scene_ids)):
        errors.append("场景 ID 不唯一")
    
    # 检查时间连续性和不重叠
    sorted_scenes = sorted(scenes, key=lambda s: s.get('start', 0))
    prev_end = 0.0
    for i, scene in enumerate(sorted_scenes):
        start = scene.get('start', 0)
        end = scene.get('end', 0)
        duration = scene.get('duration', 0)
        
        # 检查时间顺序
        if start < prev_end:
            errors.append(f"场景 {scene.get('scene_id')} 时间重叠：{start} < {prev_end}")
        
        # 检查 duration 计算正确性
        expected_duration = end - start
        if abs(duration - expected_duration) > 0.01:  # 允许浮点误差
            errors.append(f"场景 {scene.get('scene_id')} duration 计算错误：{duration} != {end} - {start}")
        
        # 检查需要旁白的场景是否有旁白
        narration_required = scene.get('narration_required', True)
        scene_type = scene.get('type', 'content')
        narration = scene.get('narration', '').strip()
        
        # cover 和 transition 类型通常不需要旁白
        if scene_type in ['cover', 'transition']:
            narration_required = False
        
        if narration_required and not narration:
            errors.append(f"Scene {scene.get('scene_id')} requires narration but is empty")
        
        prev_end = end
    
    # 检查最后一个场景是否覆盖到 total_duration
    if sorted_scenes:
        last_end = sorted_scenes[-1].get('end', 0)
        if abs(last_end - total_duration) > 1.0:  # 允许 1 秒误差
            errors.append(f"最后场景 end={last_end} 不匹配 total_duration={total_duration}")
    
    return len(errors) == 0, errors

def compile_narration_to_scenes(
    narration_file: str,
    output_file: str = None,
    project_name: str = None,
    total_duration: float = None
) -> Dict[str, Any]:
    """
    编译 narration.json 到规范化场景模型
    
    参数：
      narration_file: narration.json 源文件路径
      output_file: 输出文件路径（默认与 narration 同目录，名为 _compiled_scenes.json）
      project_name: 项目名称（优先用此值，否则从 narration 读取）
      total_duration: 总时长（优先用此值，否则计算最后场景的 end 时间）
    
    返回：编译后的场景模型对象
    """
    
    # 读取源文件
    if not os.path.exists(narration_file):
        raise FileNotFoundError(f"narration.json 不存在: {narration_file}")
    
    with open(narration_file, 'r', encoding='utf-8') as f:
        narration_data = json.load(f)
    
    # 提取数据
    scenes_input = narration_data.get('scenes', [])
    project_name = project_name or narration_data.get('project', 'Untitled')
    
    # 计算 total_duration
    if total_duration is None:
        if scenes_input:
            total_duration = max(s.get('end', 0) for s in scenes_input)
        else:
            total_duration = 0
    
    # 规范化场景数据
    compiled_scenes = []
    for i, scene in enumerate(scenes_input):
        scene_type = scene.get('type', 'content')
        narration_text = scene.get('narration', '')
        
        # cover 和 transition 类型默认不需要旁白
        narration_required = scene.get('narration_required', True)
        if scene_type in ['cover', 'transition']:
            narration_required = False
        
        compiled_scene = {
            "scene_id": f"s{scene.get('scene_id', i)}",
            "type": scene_type,
            "title": scene.get('title', f'Scene {i}'),
            "start": float(scene.get('start', 0)),
            "end": float(scene.get('end', 0)),
            "duration": float(scene.get('end', 0) - scene.get('start', 0)),
            "narration": narration_text,
            "narration_required": narration_required,
            "subtitle_required": scene.get('subtitle_required', True),
            "assets": scene.get('assets', []),
            "validation_status": "pending"
        }
        compiled_scenes.append(compiled_scene)
    
    # 验证场景
    is_valid, errors = validate_scenes(compiled_scenes, total_duration)
    if not is_valid:
        print("[ERROR] Scene validation failed:")
        for error in errors:
            print(f"  - {error}")
        raise ValueError("Scene validation failed")
    
    # 计算元数据
    tts_required_count = sum(
        1 for s in compiled_scenes
        if s.get('narration_required', True) and s.get('narration', '').strip()
    )
    
    # 构建输出对象
    output = {
        "project_name": project_name,
        "total_duration": float(total_duration),
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source_narration_hash": compute_file_hash(narration_file),
        "compiler_version": get_git_commit_hash(),
        "validation_status": "valid" if is_valid else "invalid",
        "scenes": compiled_scenes,
        "validation_metadata": {
            "total_scenes": len(compiled_scenes),
            "content_scenes": len([s for s in compiled_scenes if s['type'] != 'cover']),
            "tts_expected_count": tts_required_count,
            "tts_actual_count": 0,  # TTS 后填充
            "all_times_continuous": True,
            "final_time_matches_duration": abs(compiled_scenes[-1]['end'] - total_duration) < 1.0 if compiled_scenes else False
        }
    }
    
    # 写入输出文件
    if output_file is None:
        output_file = os.path.join(
            os.path.dirname(narration_file),
            '_compiled_scenes.json'
        )
    
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print("[OK] Compilation successful")
    print(f"  Source: {narration_file}")
    print(f"  Output: {output_file}")
    print(f"  Project: {project_name}")
    print(f"  Scenes: {len(compiled_scenes)}")
    print(f"  TTS required: {tts_required_count}")
    print(f"  Duration: {total_duration} sec")
    
    return output

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='编译 narration.json 到规范化场景模型'
    )
    parser.add_argument(
        '--narration-file',
        required=True,
        help='narration.json 源文件路径'
    )
    parser.add_argument(
        '--output-file',
        help='输出文件路径（默认为同目录的 _compiled_scenes.json）'
    )
    parser.add_argument(
        '--project-name',
        help='项目名称（覆盖 narration.json 中的值）'
    )
    parser.add_argument(
        '--total-duration',
        type=float,
        help='总时长秒数（覆盖计算值）'
    )
    
    args = parser.parse_args()
    
    try:
        compile_narration_to_scenes(
            narration_file=args.narration_file,
            output_file=args.output_file,
            project_name=args.project_name,
            total_duration=args.total_duration
        )
    except Exception as e:
        print(f"[ERROR] Compilation failed: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
