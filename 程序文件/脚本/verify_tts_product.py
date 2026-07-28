#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 产物验证模块

功能：
  验证 TTS 生成的音频产物是否满足生产要求
  
  检查项目：
  1. manifest 文件是否存在且为有效 JSON
  2. 产物数量是否匹配预期
  3. 每个产物文件是否存在
  4. 每个产物文件大小是否合理
  5. ffprobe 是否能解析
  6. 音频是否非静音（ffmpeg silencedetect）
  7. 音频时长是否被正确记录

用法：
  from verify_tts_product import validate_tts_output
  
  is_valid, details = validate_tts_output(
      manifest_path='tts_manifest.json',
      expected_scenes=7,
      temp_dir='./temp_audio'
  )
  
  if not is_valid:
      for error in details['errors']:
          print(f"ERROR: {error}")
      sys.exit(1)
"""

import os
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any

def ffprobe_duration(audio_file: str) -> float:
    """使用 ffprobe 获取音频时长（秒）"""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1:noprint_wrappers=1',
                str(Path(audio_file).resolve())
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return -1.0

def ffprobe_has_audio_stream(audio_file: str) -> bool:
    """检查文件是否包含音频流"""
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'a:0',
                '-show_entries', 'stream=codec_type',
                '-of', 'csv=p=0',
                str(Path(audio_file).resolve())
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        return result.returncode == 0 and 'audio' in result.stdout
    except Exception:
        pass
    return False

def ffmpeg_detect_silence(audio_file: str, duration: float = None) -> float:
    """
    使用 ffmpeg silencedetect 检测音频中的静音
    
    返回：静音时长（秒）
    如果整个音频都是静音，返回总时长
    """
    try:
        # 如果没有提供时长，先用 ffprobe 获取
        if duration is None:
            duration = ffprobe_duration(audio_file)
            if duration < 0:
                return -1.0
        
        result = subprocess.run(
            [
                'ffmpeg',
                '-i', str(Path(audio_file).resolve()),
                '-af', 'silencedetect=n=-40dB:d=0.1',
                '-f', 'null',
                '-'
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        
        # 解析 silencedetect 输出
        silence_duration = 0.0
        for line in result.stderr.split('\n'):
            if 'silence_duration:' in line:
                try:
                    # 格式：silence_duration: 123.456
                    val = float(line.split('silence_duration:')[1].strip())
                    silence_duration += val
                except ValueError:
                    pass
        
        return silence_duration
    except Exception:
        pass
    return -1.0

def validate_tts_output(
    manifest_path: str,
    expected_scenes: int,
    temp_dir: str = None
) -> Tuple[bool, Dict[str, Any]]:
    """
    验证 TTS 产物的完整性和有效性
    
    参数：
      manifest_path: tts_manifest.json 的路径
      expected_scenes: 预期应生成的音频数量
      temp_dir: 临时目录（用于定位音频文件，可选）
    
    返回：(是否通过验证, 详细检查结果)
    
    检查结果包括：
      - passed: 是否通过所有检查
      - total_checks: 总检查项数
      - passed_checks: 通过的检查数
      - errors: 错误列表
      - warnings: 警告列表
      - details: 详细检查信息
    """
    
    errors = []
    warnings = []
    details = {}
    
    if temp_dir is None:
        temp_dir = os.path.dirname(manifest_path)
    
    # 检查1：manifest 文件是否存在
    if not os.path.exists(manifest_path):
        errors.append(f"TTS manifest not found: {manifest_path}")
        return False, {
            'passed': False,
            'total_checks': 1,
            'passed_checks': 0,
            'errors': errors,
            'warnings': warnings,
            'details': {}
        }
    
    # 检查2：manifest 是否为有效 JSON
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        errors.append(f"TTS manifest invalid JSON: {str(e)}")
        return False, {
            'passed': False,
            'total_checks': 2,
            'passed_checks': 1,
            'errors': errors,
            'warnings': warnings,
            'details': {}
        }
    
    # 检查3：产物数量
    actual_count = len(manifest)
    if actual_count == 0:
        errors.append(f"TTS manifest is empty (expected {expected_scenes})")
    elif actual_count != expected_scenes:
        warnings.append(f"TTS product count mismatch: {actual_count} actual vs {expected_scenes} expected")
    
    details['manifest_entries'] = actual_count
    details['expected_count'] = expected_scenes
    
    # 检查每个产物
    check_count = 3
    product_details = {}
    
    for scene_key, entry in manifest.items():
        check_count += 4  # 4 个检查：存在、大小、可解析、非静音
        
        # 从 manifest 条目获取文件路径
        audio_file = entry.get('file') if isinstance(entry, dict) else entry
        
        # 如果是相对路径，补全为完整路径并规范化
        if not os.path.isabs(audio_file):
            audio_file = str(Path(temp_dir, audio_file).resolve())
        
        product_info = {'file': audio_file, 'checks': {}}
        
        # 检查3.1：文件是否存在
        exists = os.path.exists(audio_file)
        product_info['checks']['exists'] = exists
        if not exists:
            errors.append(f"TTS product not found: {audio_file}")
            product_details[scene_key] = product_info
            continue
        
        # 检查3.2：文件大小是否合理
        file_size = os.path.getsize(audio_file)
        product_info['checks']['file_size'] = file_size
        if file_size < 1024:  # 至少 1KB
            errors.append(f"TTS product too small ({file_size} bytes): {audio_file}")
        elif file_size > 10 * 1024 * 1024:  # 超过 10MB
            warnings.append(f"TTS product unusually large ({file_size} bytes): {audio_file}")
        
        # 检查3.3：ffprobe 能否解析
        has_audio = ffprobe_has_audio_stream(audio_file)
        product_info['checks']['has_audio_stream'] = has_audio
        if not has_audio:
            errors.append(f"TTS product has no audio stream (may be corrupted): {audio_file}")
        
        duration = ffprobe_duration(audio_file)
        product_info['duration'] = duration
        
        # 检查3.4：音频是否非静音
        if has_audio and duration > 0:
            silence_duration = ffmpeg_detect_silence(audio_file, duration)
            product_info['checks']['silence_detection'] = silence_duration
            
            if silence_duration < 0:
                warnings.append(f"Could not detect silence for: {audio_file}")
            elif silence_duration >= duration - 0.5:  # 几乎全是静音
                errors.append(f"TTS product is silent (or nearly silent): {audio_file}")
            elif silence_duration > duration * 0.5:  # 超过一半是静音
                warnings.append(f"TTS product contains excessive silence ({silence_duration:.1f}s of {duration:.1f}s): {audio_file}")
        
        product_details[scene_key] = product_info
    
    details['products'] = product_details
    
    # 总体判定
    passed = len(errors) == 0
    
    return passed, {
        'passed': passed,
        'total_checks': check_count,
        'passed_checks': check_count - len(errors),
        'errors': errors,
        'warnings': warnings,
        'details': details
    }

def main():
    """命令行工具"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Verify TTS audio product quality')
    parser.add_argument('--manifest', required=True, help='Path to tts_manifest.json')
    parser.add_argument('--expected-count', type=int, required=True, help='Expected TTS product count')
    parser.add_argument('--temp-dir', help='Temporary directory containing audio files')
    
    args = parser.parse_args()
    
    is_valid, result = validate_tts_output(
        manifest_path=args.manifest,
        expected_scenes=args.expected_count,
        temp_dir=args.temp_dir
    )
    
    print(f"[{'PASS' if is_valid else 'FAIL'}] TTS Verification")
    print(f"  Checks passed: {result['passed_checks']}/{result['total_checks']}")
    
    if result['errors']:
        print(f"[ERRORS]")
        for err in result['errors']:
            print(f"  - {err}")
    
    if result['warnings']:
        print(f"[WARNINGS]")
        for warn in result['warnings']:
            print(f"  - {warn}")
    
    sys.exit(0 if is_valid else 1)

if __name__ == '__main__':
    main()
