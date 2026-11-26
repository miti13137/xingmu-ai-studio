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

    def test_full_render_download_and_forbidden_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            clip = Path(temp) / 'clip.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=25:d=0.4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)], check=True)
            config = json.dumps({'ratio': '9:16', 'shots': [{'dialogue': '曼菁：你回來了。'}]}).encode()
            status, _ = self.request('POST', '/api/jobs', config, token=False)
            self.assertEqual(status, 403)
            status, _ = self.request('POST', '/api/jobs', config, extra={'Origin': 'https://attacker.invalid'})
            self.assertEqual(status, 403)
            status, payload = self.request('POST', '/api/jobs', config)
            self.assertEqual(status, 201)
            base = '/api/jobs/' + json.loads(payload)['id']
            self.assertEqual(self.request('POST', base + '/render')[0], 400)
            self.assertEqual(self.request('POST', base + '/clip/0', clip.read_bytes())[0], 200)
            self.assertEqual(self.request('POST', base + '/backup')[0], 200)
            status, archive = self.request('GET', base + '/backup-file')
            self.assertEqual(status, 200)
            status, payload = self.request('POST', '/api/restore', archive)
            self.assertEqual(status, 201)
            base = '/api/jobs/' + json.loads(payload)['id']
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
            self.assertIn(b'ftyp', result[:32])
            self.assertEqual(self.request('POST', base + '/render')[0], 409)

    def test_backup_restore_roundtrip_and_malicious_zip(self):
        import io
        import zipfile
        config = {'ratio': '16:9', 'project': {'name': '霓虹舊夢', 'script': '原始劇本', 'characters': [{'name': '曼菁'}]}, 'shots': [{'dialogue': '第一鏡'}, {'dialogue': '第二鏡'}]}
        status, payload = self.request('POST', '/api/jobs', json.dumps(config, ensure_ascii=False).encode())
        self.assertEqual(status, 201)
        base = '/api/jobs/' + json.loads(payload)['id']
        for i, data in enumerate((b'video-one', b'video-two')):
            self.assertEqual(self.request('POST', f'{base}/clip/{i}', data)[0], 200)
        self.assertEqual(self.request('POST', base + '/music', b'music-bytes')[0], 200)
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
        mal = io.BytesIO()
        with zipfile.ZipFile(mal, 'w') as archive_file:
            archive_file.writestr('project.json', json.dumps({'version': 1, 'ratio': '9:16', 'shots': [{'video': 'clip-0.bin', 'dialogue': ''}]}))
            archive_file.writestr('clip-0.bin', b'video')
            archive_file.writestr('../escape', b'attack')
        self.assertEqual(self.request('POST', '/api/restore', mal.getvalue())[0], 400)
        self.assertEqual(self.request('POST', '/api/restore', b'not zip')[0], 400)

if __name__ == '__main__':
    unittest.main()
