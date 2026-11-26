#!/usr/bin/env python3
"""Development-only local MP4 workbench; no cloud services or AI generation."""
import json
import re
import secrets
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from render import render
import story_ai
import speech
import archive as project_archive

ROOT = Path(__file__).resolve().parent
MAX_FILE = 100 * 1024 * 1024
TOKEN = secrets.token_urlsafe(32)
JOBS = {}
LOCK = threading.Lock()
DATA = tempfile.TemporaryDirectory(prefix='xingmu-studio-')


class Handler(BaseHTTPRequestHandler):
    server_version = 'XingmuLocal/0.1'

    def log_message(self, *args):
        pass

    def headers_ok(self, mutation=False):
        host = self.headers.get('Host', '')
        allowed = f'127.0.0.1:{self.server.server_port}'
        origin = self.headers.get('Origin')
        return host == allowed and (not origin or origin == f'http://{allowed}') and (not mutation or secrets.compare_digest(self.headers.get('X-Xingmu-Token', ''), TOKEN))

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, limit):
        try:
            length = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            raise ValueError('檔案大小無效')
        if length < 0 or length > limit:
            raise ValueError('檔案過大或未指定長度')
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError('資料傳輸不完整')
        return data

    def do_GET(self):
        if not self.headers_ok():
            return self.reply(403, {'error': '連線來源不符'})
        path = urlsplit(self.path).path
        if path == '/api/models':
            try:
                return self.reply(200, {'models': story_ai.models()})
            except ValueError as exc:
                return self.reply(503, {'error': str(exc)})
        if path == '/':
            data = (ROOT / 'workbench.html').read_text(encoding='utf8').replace('__SESSION_TOKEN__', TOKEN).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            return self.wfile.write(data)
        match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(/download|/backup-file|/asset/(?:clip-[0-9]+|voice-[0-9]+|music))?', path)
        if not match:
            return self.reply(404, {'error': '找不到頁面'})
        with LOCK:
            job = JOBS.get(match.group(1))
            state = {k: job[k] for k in ('status', 'error')} if job else None
        if not job:
            return self.reply(404, {'error': '找不到製片工作'})
        if not match.group(2):
            return self.reply(200, state)
        operation = match.group(2)
        if operation == '/download':
            if state['status'] != 'done':
                return self.reply(409, {'error': '影片尚未完成'})
            output = job['dir'] / 'result.mp4'
            content_type, filename = 'video/mp4', 'xingmu-film.mp4'
        elif operation == '/backup-file':
            output = job['dir'] / 'backup.zip'
            content_type, filename = 'application/zip', 'xingmu-project.zip'
        else:
            asset = operation.removeprefix('/asset/')
            if asset == 'music':
                name = 'music.bin'
                if job['manifest'].get('music') != name:
                    return self.reply(404, {'error': '找不到音樂'})
            else:
                kind, raw_index = asset.split('-', 1)
                index = int(raw_index)
                if index >= len(job['manifest']['shots']):
                    return self.reply(404, {'error': '找不到鏡頭'})
                name = f'{kind}-{index}.bin'
                if kind == 'voice' and job['manifest']['shots'][index].get('voice') != name:
                    return self.reply(404, {'error': '找不到配音'})
            output = job['dir'] / name
            content_type, filename = 'application/octet-stream', name
        if not output.is_file():
            return self.reply(404, {'error': '找不到檔案'})
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', str(output.stat().st_size))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        with output.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_POST(self):
        if not self.headers_ok(True):
            return self.reply(403, {'error': '連線來源不符'})
        path = urlsplit(self.path).path
        try:
            if path == '/api/speech':
                config = json.loads(self.read_body(8192).decode('utf8'))
                if not isinstance(config, dict):
                    raise ValueError('配音資料格式錯誤')
                wav = speech.synthesize(config.get('dialogue'), config.get('rate', 0))
                self.send_response(200)
                self.send_header('Content-Type', 'audio/wav')
                self.send_header('Content-Length', str(len(wav)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return self.wfile.write(wav)
            if path == '/api/restore':
                payload = self.read_body(project_archive.MAX_ARCHIVE)
                ident = uuid.uuid4().hex
                folder = Path(DATA.name) / ident
                manifest = project_archive.restore_backup(payload, folder)
                with LOCK:
                    JOBS[ident] = {'dir': folder, 'manifest': manifest, 'status': 'uploading', 'error': ''}
                return self.reply(201, {'id': ident, 'manifest': manifest})
            if path == '/api/story/generate':
                config = json.loads(self.read_body(16 * 1024).decode('utf8'))
                if not isinstance(config, dict):
                    raise ValueError('作品資料格式錯誤')
                project = story_ai.generate(config.get('model'), config.get('name'), config.get('idea'), config.get('ratio'), config.get('type'))
                return self.reply(200, {'project': project})
            if path == '/api/jobs':
                config = json.loads(self.read_body(256 * 1024).decode('utf8'))
                if not isinstance(config, dict):
                    raise ValueError('作品格式錯誤')
                shots = config.get('shots')
                if config.get('ratio') not in ('9:16', '16:9') or not isinstance(shots, list) or not 1 <= len(shots) <= 50:
                    raise ValueError('請提供畫面比例與 1 至 50 個鏡頭')
                if not all(isinstance(x, dict) and isinstance(x.get('dialogue', ''), str) and len(x.get('dialogue', '')) <= 2000 for x in shots):
                    raise ValueError('字幕格式錯誤')
                ident = uuid.uuid4().hex
                folder = Path(DATA.name) / ident
                folder.mkdir()
                manifest = {'ratio': config['ratio'], 'shots': [{'video': f'clip-{i}.bin', 'dialogue': shot.get('dialogue', '')} for i, shot in enumerate(shots)]}
                for index, item in enumerate(shots):
                    offset = item.get('voice_offset', 0)
                    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not 0 <= offset <= 3600:
                        folder.rmdir()
                        raise ValueError('配音開始時間不正確')
                    manifest['shots'][index]['voice_offset'] = offset
                if config.get('project') is not None:
                    manifest['project'] = config['project']
                try:
                    project_archive.validate_manifest({**manifest, 'version': 1})
                except ValueError:
                    folder.rmdir()
                    raise
                with LOCK:
                    JOBS[ident] = {'dir': folder, 'manifest': manifest, 'status': 'uploading', 'error': ''}
                return self.reply(201, {'id': ident})
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(clip/([0-9]+)|voice/([0-9]+)|music|render|backup)', path)
            if not match:
                return self.reply(404, {'error': '找不到操作'})
            ident, operation, clip_index, voice_index = match.groups()
            with LOCK:
                job = JOBS.get(ident)
                if not job:
                    return self.reply(404, {'error': '找不到製片工作'})
                if job['status'] != 'uploading':
                    return self.reply(409, {'error': '工作已開始，不能修改'})
            if operation == 'backup':
                self.read_body(0)
                with LOCK:
                    if job['status'] != 'uploading':
                        return self.reply(409, {'error': '工作已開始，不能修改'})
                    project_archive.create_backup(job)
                return self.reply(200, {'ok': True})
            if operation == 'render':
                self.read_body(0)
                if any(not (job['dir'] / shot['video']).is_file() for shot in job['manifest']['shots']):
                    raise ValueError('尚未上傳所有鏡頭影片')
                with LOCK:
                    if job['status'] != 'uploading':
                        return self.reply(409, {'error': '工作已開始'})
                    job['status'] = 'rendering'
                threading.Thread(target=self.run_job, args=(job,), daemon=True).start()
                return self.reply(202, {'status': 'rendering'})
            if operation == 'music':
                name = 'music.bin'
            else:
                i = int(clip_index if clip_index is not None else voice_index)
                if i >= len(job['manifest']['shots']):
                    raise ValueError('鏡頭編號錯誤')
                name = f'{"clip" if clip_index is not None else "voice"}-{i}.bin'
            payload = self.read_body(MAX_FILE)
            if not payload:
                raise ValueError('檔案是空的')
            with LOCK:
                if job['status'] != 'uploading':
                    return self.reply(409, {'error': '工作已開始，不能修改'})
                (job['dir'] / name).write_bytes(payload)
                if operation == 'music':
                    job['manifest']['music'] = name
                elif voice_index is not None:
                    job['manifest']['shots'][i]['voice'] = name
            return self.reply(200, {'ok': True})
        except (ValueError, UnicodeError, json.JSONDecodeError, OSError) as exc:
            return self.reply(400, {'error': str(exc)})

    @staticmethod
    def run_job(job):
        try:
            manifest_file = job['dir'] / 'manifest.json'
            manifest_file.write_text(json.dumps(job['manifest'], ensure_ascii=False), encoding='utf8')
            render(manifest_file, job['dir'] / 'result.mp4')
            with LOCK:
                job['status'] = 'done'
        except Exception as exc:
            error = getattr(exc, 'stderr', None)
            message = error.decode(errors='replace')[-600:] if isinstance(error, bytes) else str(exc)
            with LOCK:
                job['status'] = 'error'
                job['error'] = message


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}/'
    print('星幕本機測試版：' + url, flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        DATA.cleanup()


if __name__ == '__main__':
    main()
