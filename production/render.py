#!/usr/bin/env python3
"""Render supplied video clips to MP4. This module does not generate AI media."""
import argparse
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type:format=duration', '-of', 'json', str(path)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    h, milliseconds = divmod(milliseconds, 3600000)
    m, milliseconds = divmod(milliseconds, 60000)
    s, ms = divmod(milliseconds, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def render(manifest_path, output):
    manifest_path = Path(manifest_path).resolve()
    project = json.loads(manifest_path.read_text(encoding='utf-8'))
    ratio = project.get('ratio', '9:16')
    if ratio not in ('9:16', '16:9'):
        raise ValueError('畫面比例必須是 9:16 或 16:9')
    shots = project.get('shots')
    if not isinstance(shots, list) or not shots:
        raise ValueError('請提供至少一個鏡頭')
    if not all(shutil.which(x) for x in ('ffmpeg', 'ffprobe')):
        raise ValueError('需要安裝 FFmpeg 和 ffprobe')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('輸出檔案已存在，請選擇其他檔名，避免覆蓋')
    if output.suffix.lower() != '.mp4':
        raise ValueError('輸出檔案必須是 MP4')
    width, height = (540, 960) if ratio == '9:16' else (960, 540)
    basedir = manifest_path.parent
    with tempfile.TemporaryDirectory(prefix='xingmu-') as temp:
        temp = Path(temp)
        durations, segments = [], []
        for i, shot in enumerate(shots):
            if not isinstance(shot, dict) or not isinstance(shot.get('video'), str) or not shot['video'].strip():
                raise ValueError(f'鏡頭 {i+1} 未提供影片路徑')
            video = (basedir / shot['video']).resolve()
            if not video.is_file():
                raise ValueError(f'找不到鏡頭 {i+1} 影片：{video}')
            info = probe(video)
            if not any(s['codec_type'] == 'video' for s in info.get('streams', [])):
                raise ValueError(f'鏡頭 {i+1} 沒有影像')
            duration = float(info['format']['duration'])
            if not 0 < duration < 3600:
                raise ValueError(f'鏡頭 {i+1} 長度無效')
            has_audio = any(s['codec_type'] == 'audio' for s in info.get('streams', []))
            segment = temp / f'{i:05}.mp4'
            vf = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,format=yuv420p'
            args = ['ffmpeg', '-v', 'error', '-y', '-i', str(video)]
            if not has_audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            voice_value = shot.get('voice')
            base_audio = '0:a:0' if has_audio else '1:a:0'
            if voice_value:
                voice = (basedir / voice_value).resolve()
                if not voice.is_file():
                    raise ValueError(f'找不到鏡頭 {i+1} 的配音')
                voice_info = probe(voice)
                if not any(x['codec_type'] == 'audio' for x in voice_info.get('streams', [])):
                    raise ValueError(f'鏡頭 {i+1} 的配音沒有音訊')
                voice_duration = float(voice_info['format']['duration'])
                offset = shot.get('voice_offset', 0)
                if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0 or offset >= duration:
                    raise ValueError(f'鏡頭 {i+1} 的配音開始時間不正確')
                if not math.isfinite(voice_duration) or voice_duration <= 0 or offset + voice_duration > duration + 0.05:
                    raise ValueError(f'鏡頭 {i+1} 的配音長度超過影片；請縮短配音或更換較長鏡頭')
                voice_index = 1 if has_audio else 2
                args += ['-i', str(voice)]
                delay = round(offset * 1000)
                filters = f'[{base_audio}]apad=whole_dur={duration}[base];[{voice_index}:a:0]adelay={delay}:all=1,apad=whole_dur={duration}[voice];[base][voice]amix=inputs=2:duration=first:normalize=0[a]'
                args += ['-filter_complex', filters, '-map', '0:v:0', '-map', '[a]']
                shot['_voice_duration'] = voice_duration
            else:
                args += ['-map', '0:v:0', '-map', base_audio]
            args += ['-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-t', str(duration), '-movflags', '+faststart', str(segment)]
            run(args)
            segments.append(segment)
            durations.append(duration)
        concat = temp / 'concat.txt'
        concat.write_text(''.join("file '" + str(s).replace("'", "'\\''") + "'\n" for s in segments), encoding='utf-8')
        joined = temp / 'joined.mp4'
        run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-c', 'copy', str(joined)])
        cues = []
        start = 0.0
        for i, (shot, duration) in enumerate(zip(shots, durations)):
            text = str(shot.get('dialogue', '')).strip().replace('\r', '')
            if text:
                cue_start = start + (shot.get('voice_offset', 0) if shot.get('_voice_duration') else 0)
                cue_end = min(start + duration, cue_start + shot.get('_voice_duration', duration))
                cues.append(f'{len(cues)+1}\n{timestamp(cue_start)} --> {timestamp(cue_end)}\n{text}\n')
            start += duration
        srt = temp / 'captions.srt'
        srt.write_text('\n'.join(cues), encoding='utf-8')
        music_value = project.get('music')
        music = (basedir / music_value).resolve() if isinstance(music_value, str) and music_value.strip() else None
        if music and not music.is_file():
            raise ValueError(f'找不到背景音樂：{music}')
        args = ['ffmpeg', '-v', 'error', '-y', '-i', str(joined)]
        if music:
            args += ['-stream_loop', '-1', '-i', str(music), '-filter_complex', '[1:a]volume=0.15[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[a]', '-map', '0:v:0', '-map', '[a]']
        else:
            args += ['-map', '0:v:0', '-map', '0:a:0']
        if cues:
            # The temporary name is fixed and relative, avoiding filter path escaping issues.
            args += ['-vf', "subtitles=captions.srt:force_style='FontName=Noto Sans CJK TC,FontSize=16,Outline=1,Alignment=2,MarginV=30'", '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22']
        else:
            args += ['-c:v', 'copy']
        args += ['-c:a', 'aac', '-movflags', '+faststart', str(output)]
        # ffmpeg subtitles resolve relative to the temporary working directory.
        try:
            subprocess.run(args, cwd=temp, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            output.unlink(missing_ok=True)
            raise
    return output


def main():
    parser = argparse.ArgumentParser(description='將已提供的鏡頭影片及字幕合成 MP4')
    parser.add_argument('manifest', help='製片 JSON 路徑')
    parser.add_argument('output', help='輸出 MP4 路徑（不得覆蓋現有檔）')
    args = parser.parse_args()
    try:
        print('完成：', render(args.manifest, args.output))
    except (ValueError, OSError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        message = exc.stderr.decode(errors='replace')[-1500:] if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        parser.exit(1, '合成失敗：' + message + '\n')


if __name__ == '__main__':
    main()
