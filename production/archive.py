"""Portable media archive support for local Xingmu workbench projects."""
import io
import json
import math
import re
import shutil
import zipfile
from pathlib import Path

MAX_ARCHIVE = 220 * 1024 * 1024
MAX_TOTAL = 200 * 1024 * 1024
MAX_FILE = 100 * 1024 * 1024
MAX_MANIFEST = 256 * 1024


def image_settings(shot):
    duration = shot.get('image_seconds')
    motion = shot.get('image_motion', 'still')
    if 'image_seconds' not in shot:
        if 'image_motion' in shot:
            raise ValueError('請指定圖片鏡頭秒數')
        return {}
    if type(duration) not in (int, float) or not 1 <= duration <= 120:
        raise ValueError('圖片鏡頭長度須介於 1 至 120 秒')
    if motion not in ('still', 'push', 'talk'):
        raise ValueError('圖片運鏡設定不正確')
    return {'image_seconds': duration, 'image_motion': motion}


def validate_manifest(raw):
    if not isinstance(raw, dict) or raw.get('version') not in (1, 2) or raw.get('ratio') not in ('9:16', '16:9'):
        raise ValueError('備份版本或畫面比例不正確')
    shots = raw.get('shots')
    if not isinstance(shots, list) or not 1 <= len(shots) <= 50:
        raise ValueError('備份中的鏡頭數量不正確')
    result = []
    for i, item in enumerate(shots):
        if not isinstance(item, dict) or not isinstance(item.get('dialogue'), str) or len(item['dialogue']) > 2000:
            raise ValueError('備份中的鏡頭資料不正確')
        video = item.get('video')
        if video != f'clip-{i}.bin' and not (raw['version'] == 2 and 'video' in item and video is None):
            raise ValueError('備份中的鏡頭資料不正確')
        shot = {'video': video, 'dialogue': item['dialogue'], **image_settings(item)}
        voice = item.get('voice')
        offset = item.get('voice_offset', 0)
        if voice is not None:
            if voice != f'voice-{i}.bin' or isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or not 0 <= offset <= 3600:
                raise ValueError('備份中的配音資料不正確')
            shot['voice'], shot['voice_offset'] = voice, offset
        result.append(shot)
    manifest = {'ratio': raw['ratio'], 'shots': result}
    project = raw.get('project')
    if project is not None:
        if not isinstance(project, dict) or len(json.dumps(project, ensure_ascii=False).encode('utf8')) > 128 * 1024:
            raise ValueError('作品文字資料格式錯誤或過大')
        manifest['project'] = project
    transition = raw.get('transition', 'cut')
    if transition not in ('cut', 'fade'):
        raise ValueError('轉場設定不正確')
    if transition != 'cut':
        manifest['transition'] = transition
    for track in ('music', 'ambience'):
        filename = raw.get(track)
        if filename is not None:
            if filename != f'{track}.bin':
                raise ValueError('備份中的背景音軌名稱不正確')
            manifest[track] = filename
    return manifest


def create_backup(job):
    folder = Path(job['dir'])
    manifest = dict(job['manifest'])
    manifest['shots'] = [
        {**shot, 'video': shot['video'] if (folder / shot['video']).is_file() else None}
        for shot in manifest['shots']
    ]
    manifest['version'] = 1 if all(shot['video'] for shot in manifest['shots']) else 2
    validate_manifest(manifest)
    files = [x['video'] for x in manifest['shots'] if x['video']] + [x['voice'] for x in manifest['shots'] if x.get('voice')]
    files.extend(manifest[track] for track in ('music', 'ambience') if track in manifest)
    if any(not (folder / name).is_file() for name in files):
        raise ValueError('備份指定的音訊素材不存在')
    sizes = [(folder / name).stat().st_size for name in files]
    if any(size <= 0 or size > MAX_FILE for size in sizes) or sum(sizes) > MAX_TOTAL:
        raise ValueError('備份素材總量須在 200 MB 以內，每個檔案不得超過 100 MB')
    destination = folder / 'backup.zip'
    temporary = folder / 'backup.zip.tmp'
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('project.json', json.dumps(manifest, ensure_ascii=False).encode('utf8'))
            for name in files:
                archive.write(folder / name, name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def restore_backup(data, target):
    if not data or len(data) > MAX_ARCHIVE:
        raise ValueError('備份檔案過大或是空的')
    target = Path(target)
    created = False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [entry.filename for entry in infos]
            if len(names) != len(set(names)) or names.count('project.json') != 1:
                raise ValueError('備份中有重複檔案或缺少專案資料')
            project_info = archive.getinfo('project.json')
            if project_info.file_size > MAX_MANIFEST:
                raise ValueError('專案資料過大')
            manifest = validate_manifest(json.loads(archive.read(project_info).decode('utf8')))
            expected = {'project.json'} | {shot['video'] for shot in manifest['shots'] if shot['video']} | {shot['voice'] for shot in manifest['shots'] if shot.get('voice')}
            expected.update(manifest[track] for track in ('music', 'ambience') if track in manifest)
            if set(names) != expected:
                raise ValueError('備份檔案內容不完整或含有未預期的檔案')
            media = [entry for entry in infos if entry.filename != 'project.json']
            if any(x.is_dir() or x.file_size <= 0 or x.file_size > MAX_FILE or x.flag_bits & 1 for x in media) or sum(x.file_size for x in media) > MAX_TOTAL:
                raise ValueError('備份素材大小不符合限制')
            target.mkdir(parents=True, exist_ok=False)
            created = True
            for entry in media:
                with archive.open(entry) as src, (target / entry.filename).open('wb') as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
        return manifest
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, EOFError, RuntimeError) as exc:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise ValueError('備份檔案損壞或格式不正確') from exc
    except (ValueError, OSError):
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise
