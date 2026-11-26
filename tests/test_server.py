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

if __name__ == '__main__':
    unittest.main()
