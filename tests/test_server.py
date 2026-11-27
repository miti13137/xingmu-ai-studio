import http.client
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'production'))
import server


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def request(self, method, path, body=b'', token=True, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=30)
        headers = {'X-Xingmu-Token': server.TOKEN} if token else {}
        headers.update(extra or {})
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    def test_lip_sync_status_reports_missing_configuration(self):
        from unittest.mock import patch

        with patch.dict('os.environ', {'XINGMU_SADTALKER_DIR': ''}):
            status, payload = self.request('GET', '/api/lip-sync/status', token=False)
        self.assertEqual(status, 200)
        result = json.loads(payload)
        self.assertFalse(result['configured'])
        self.assertIn('尚未設定本機 SadTalker', result['error'])

    def test_speech_delivers_wav_and_reports_absent_engine(self):
        from unittest.mock import patch

        request = json.dumps({'dialogue': '曼菁，你回來了。'}).encode()
        wav = subprocess.check_output([
            'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.2',
            '-f', 'wav', '-',
        ])
        with patch.object(server.speech, 'synthesize', return_value=wav) as synthesize:
            self.assertEqual(self.request('POST', '/api/speech', request, token=False)[0], 403)
            self.assertEqual(self.request('POST', '/api/speech', request), (200, wav))
            synthesize.assert_called_once_with('曼菁，你回來了。', 0)
        with patch.object(server.speech.shutil, 'which', return_value=None):
            status, payload = self.request('POST', '/api/speech', request)
        self.assertEqual(status, 400)
        self.assertIn('Windows', json.loads(payload)['error'])

    def test_neural_speech_voice_selects_local_engine(self):
        from unittest.mock import patch

        with patch.object(server.speech.neural_speech, 'synthesize', return_value=b'WAV') as engine:
            valid = json.dumps({'dialogue': ' 這張票，我留了三年。 ', 'rate': -1,
                                'voice': 'mandarin-female'}).encode()
            invalid = json.dumps({'dialogue': '你好', 'voice': []}).encode()
            self.assertEqual(self.request('POST', '/api/speech', valid), (200, b'WAV'))
            self.assertEqual(self.request('POST', '/api/speech', invalid)[0], 400)
            engine.assert_called_once_with('這張票，我留了三年。', -1, 'mandarin-female')

    def test_full_render_download_and_forbidden_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / 'clip.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=25:d=0.4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)], check=True)
            picture = subprocess.check_output([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90',
                '-frames:v', '1', '-f', 'image2pipe', '-c:v', 'png', '-',
            ])
            voice = Path(temp) / 'voice.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2', str(voice)], check=True)
            config = json.dumps({'ratio': '9:16', 'transition': 'fade', 'shots': [{'dialogue': '曼菁：你回來了。', 'voice_offset': 0.1}, {'image_seconds': 2, 'image_motion': 'push'}]}).encode()
            status, _ = self.request('POST', '/api/jobs', config, token=False)
            self.assertEqual(status, 403)
            status, _ = self.request('POST', '/api/jobs', config, extra={'Origin': 'https://attacker.invalid'})
            self.assertEqual(status, 403)
            status, payload = self.request('POST', '/api/jobs', config)
            self.assertEqual(status, 201)
            base = '/api/jobs/' + json.loads(payload)['id']
            self.assertEqual(self.request('POST', base + '/render')[0], 400)
            self.assertEqual(self.request('POST', base + '/clip/0', clip.read_bytes())[0], 200)
            self.assertEqual(self.request('POST', base + '/voice/0', voice.read_bytes())[0], 200)
            self.assertEqual(self.request('POST', base + '/ambience', voice.read_bytes())[0], 200)
            self.assertEqual(self.request('POST', base + '/clip/1', picture)[0], 200)
            self.assertEqual(self.request('POST', base + '/backup')[0], 200)
            status, archive = self.request('GET', base + '/backup-file')
            self.assertEqual(status, 200)
            status, payload = self.request('POST', '/api/restore', archive)
            self.assertEqual(status, 201)
            restored = json.loads(payload)
            self.assertEqual(restored['manifest']['transition'], 'fade')
            self.assertEqual(restored['manifest']['ambience'], 'ambience.bin')
            self.assertEqual(restored['manifest']['shots'][1]['image_seconds'], 2)
            self.assertEqual(restored['manifest']['shots'][1]['image_motion'], 'push')
            base = '/api/jobs/' + restored['id']
            self.assertEqual(self.request('GET', base + '/asset/ambience')[1], voice.read_bytes())
            self.assertEqual(self.request('POST', base + '/render')[0], 202)
            for _ in range(100):
                _, payload = self.request('GET', base)
                state = json.loads(payload)
                if state['status'] in ('done', 'error'):
                    break
                time.sleep(.1)
            self.assertEqual(state['status'], 'done', state.get('error'))
            status, result = self.request('GET', base + '/download')
            self.assertEqual(status, 200)
            film = Path(temp) / 'film.mp4'
            film.write_bytes(result)
            info = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,duration,nb_frames', '-of', 'json', str(film),
            ]))['streams'][0]
            self.assertEqual((info['width'], info['height']), (540, 960))
            self.assertEqual(int(info['nb_frames']), 60)
            self.assertAlmostEqual(float(info['duration']), 2.4, delta=0.08)
            frames = []
            for timestamp in (0.6, 2.2):
                frames.append(subprocess.check_output([
                    'ffmpeg', '-v', 'error', '-ss', str(timestamp), '-i', str(film),
                    '-map', '0:v:0', '-frames:v', '1', '-f', 'framemd5', '-',
                ]).splitlines()[-1].split(b',')[-1])
            self.assertNotEqual(*frames)
            pixels = subprocess.check_output([
                'ffmpeg', '-v', 'error', '-i', str(film), '-map', '0:v:0',
                '-frames:v', '1', '-vf', 'format=gray', '-f', 'rawvideo', '-',
            ])
            self.assertLessEqual(max(pixels), 2)  # The first shot starts on black.

            self.assertEqual(self.request('POST', base + '/render')[0], 409)

    def test_backup_restore_roundtrip_and_malicious_zip(self):
        import io
        import zipfile
        config = {'ratio': '16:9', 'project': {'name': '霓虹舊夢', 'script': '原始劇本', 'characters': [{'name': '曼菁'}]}, 'shots': [{'dialogue': '第一鏡', 'voice_offset': 0.5}, {'dialogue': '第二鏡', 'image_seconds': 3, 'image_motion': 'talk'}]}
        status, payload = self.request('POST', '/api/jobs', json.dumps(config, ensure_ascii=False).encode())
        self.assertEqual(status, 201)
        base = '/api/jobs/' + json.loads(payload)['id']
        for i, data in enumerate((b'video-one', b'video-two')):
            self.assertEqual(self.request('POST', f'{base}/clip/{i}', data)[0], 200)
        self.assertEqual(self.request('POST', base + '/music', b'music-bytes')[0], 200)
        self.assertEqual(self.request('POST', base + '/voice/0', b'voice-bytes')[0], 200)
        self.assertEqual(self.request('POST', base + '/backup')[0], 200)
        status, archive = self.request('GET', base + '/backup-file')
        self.assertEqual(status, 200)
        status, payload = self.request('POST', '/api/restore', archive)
        self.assertEqual(status, 201)
        restored = json.loads(payload)
        self.assertEqual(restored['manifest']['project'], config['project'])
        self.assertEqual([s['dialogue'] for s in restored['manifest']['shots']], ['第一鏡', '第二鏡'])
        restored_base = '/api/jobs/' + restored['id']
        self.assertEqual(self.request('GET', restored_base + '/asset/clip-0')[1], b'video-one')
        self.assertEqual(self.request('GET', restored_base + '/asset/clip-1')[1], b'video-two')
        self.assertEqual(self.request('GET', restored_base + '/asset/music')[1], b'music-bytes')
        self.assertEqual(self.request('GET', restored_base + '/asset/voice-0')[1], b'voice-bytes')
        self.assertEqual(restored['manifest']['shots'][0]['voice_offset'], 0.5)
        self.assertEqual(restored['manifest']['shots'][1]['image_motion'], 'talk')
        mal = io.BytesIO()
        with zipfile.ZipFile(mal, 'w') as archive_file:
            archive_file.writestr('project.json', json.dumps({'version': 1, 'ratio': '9:16', 'shots': [{'video': 'clip-0.bin', 'dialogue': ''}]}))
            archive_file.writestr('clip-0.bin', b'video')
            archive_file.writestr('../escape', b'attack')
        self.assertEqual(self.request('POST', '/api/restore', mal.getvalue())[0], 400)
        self.assertEqual(self.request('POST', '/api/restore', b'not zip')[0], 400)

if __name__ == '__main__':
    unittest.main()
