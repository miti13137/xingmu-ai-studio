import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'production'))
import story_ai

EXAMPLE = {'title': '霓虹舊夢', 'script': '曼菁在雨夜遇見文遠。', 'characters': [{'name': '曼菁', 'description': '墨色旗袍'}, {'name': '文遠', 'description': '深色西裝'}], 'shots': [{'visual': '雨夜街道', 'camera': '推近', 'duration': 5, 'dialogue': '曼菁：你回來了。', 'image_prompt': 'cinematic street', 'video_prompt': 'slow push in'}]}


class MockOllama(BaseHTTPRequestHandler):
    calls = []
    remote = False
    def log_message(self, *args):
        pass
    def reply(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self.calls.append(self.path)
        self.reply({'models': [{'name': 'local:latest', 'size': 123}, {'name': 'paid:cloud', 'size': 0}]})
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.calls.append((self.path, data))
        if self.path == '/api/show':
            self.reply({'remote_model': 'paid:cloud'} if self.remote else {'details': {'format': 'gguf'}})
        else:
            self.reply({'done': True, 'message': {'content': json.dumps(EXAMPLE, ensure_ascii=False)}})


class StoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), MockOllama)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = patch.object(story_ai, 'PORT', cls.http.server_port)
        cls.port.start()

    @classmethod
    def tearDownClass(cls):
        cls.port.stop()
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        MockOllama.calls = []
        MockOllama.remote = False

    def test_story_round_trip_v3_format(self):
        project = story_ai.generate('local:latest', '霓虹舊夢', '1940年代上海的曼菁和文遠。', '9:16', '短劇')
        self.assertEqual(project['name'], '霓虹舊夢')
        self.assertEqual(project['shots'][0]['dialogue'], '曼菁：你回來了。')
        call = next(x for x in MockOllama.calls if isinstance(x, tuple) and x[0] == '/api/chat')
        self.assertFalse(call[1]['stream'])
        self.assertIsInstance(call[1]['format'], dict)

    def test_cloud_model_disallowed_before_generation(self):
        self.assertEqual(story_ai.models(), ['local:latest'])
        with self.assertRaises(ValueError):
            story_ai.generate('paid:cloud', '劇名', '構想', '9:16', '短劇')
        MockOllama.remote = True
        with self.assertRaises(ValueError):
            story_ai.generate('local:latest', '劇名', '構想', '9:16', '短劇')
        self.assertFalse(any(isinstance(x, tuple) and x[0] == '/api/chat' for x in MockOllama.calls))

    def test_invalid_input_does_not_contact_model(self):
        with self.assertRaises(ValueError):
            story_ai.generate('local:latest', '劇名', '', '9:16', '短劇')
        self.assertEqual(MockOllama.calls, [])

if __name__ == '__main__':
    unittest.main()
