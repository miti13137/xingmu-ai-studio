import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from production.render import render, probe, timestamp


class RenderTest(unittest.TestCase):
    def test_timestamp(self):
        self.assertEqual(timestamp(3661.125), '01:01:01,125')

    def test_real_mp4_render_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, colour in [('a', 'blue'), ('b', 'red')]:
                subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color={colour}:s=160x90:r=25:d=0.4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(root / f'{name}.mp4')], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', str(root / 'music.wav')], check=True)
            project = {'ratio': '16:9', 'music': 'music.wav', 'shots': [{'video': 'a.mp4', 'dialogue': '曼菁：你回來了。'}, {'video': 'b.mp4', 'dialogue': '文遠：我回來了。'}]}
            manifest = root / 'project.json'
            manifest.write_text(json.dumps(project, ensure_ascii=False), encoding='utf8')
            output = root / 'film.mp4'
            render(manifest, output)
            result = probe(output)
            self.assertGreater(float(result['format']['duration']), .7)
            self.assertTrue({'video', 'audio'}.issubset({x['codec_type'] for x in result['streams']}))
            previous = output.read_bytes()
            with self.assertRaises(ValueError):
                render(manifest, output)
            self.assertEqual(previous, output.read_bytes())

    def test_missing_shot_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'project.json'
            manifest.write_text(json.dumps({'shots': [{'video': 'missing.mp4'}]}))
            with self.assertRaises(ValueError):
                render(manifest, root / 'output.mp4')
            self.assertFalse((root / 'output.mp4').exists())

if __name__ == '__main__':
    unittest.main()
