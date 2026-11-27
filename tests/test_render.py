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

class VoiceTest(unittest.TestCase):
    def test_voice_is_mixed_at_offset_and_overlong_rejected(self):
        import array
        import math
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=25:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(root/'clip.mp4')], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', str(root/'voice.wav')], check=True)
            manifest = root / 'project.json'
            project = {'ratio': '16:9', 'shots': [{'video': 'clip.mp4', 'voice': 'voice.wav', 'voice_offset': 0.5, 'dialogue': '你好'}]}
            manifest.write_text(json.dumps(project, ensure_ascii=False), encoding='utf8')
            output = root/'result.mp4'
            render(manifest, output)
            samples = array.array('h')
            samples.frombytes(subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-f', 's16le', '-']))
            def rms(start, end):
                segment = samples[int(start*16000):int(end*16000)]
                return math.sqrt(sum(x*x for x in segment)/len(segment))
            self.assertLess(rms(.05, .3), 100)
            self.assertGreater(rms(.6, .85), 300)
            self.assertLess(rms(1.2, 1.5), 100)
            project['shots'][0]['voice_offset'] = 1.8
            manifest.write_text(json.dumps(project), encoding='utf8')
            with self.assertRaisesRegex(ValueError, '超過影片'):
                render(manifest, root/'overlong.mp4')
            self.assertFalse((root/'overlong.mp4').exists())


class TalkingShotTest(unittest.TestCase):
    def test_talking_shot_requires_engine_and_muxes_generated_video(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=s=160x90',
                '-frames:v', '1', str(root / 'portrait.png'),
            ], check=True)
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=0.5',
                str(root / 'voice.wav'),
            ], check=True)
            manifest = root / 'project.json'
            shot = {
                'video': 'portrait.png', 'image_seconds': 2, 'image_motion': 'talk',
                'voice_offset': 0.4, 'dialogue': '我回來了。',
            }
            output = root / 'result.mp4'

            def save():
                manifest.write_text(json.dumps({'shots': [shot]}), encoding='utf-8')

            save()
            with self.assertRaisesRegex(ValueError, '需要配音'):
                render(manifest, output)
            shot['voice'] = 'voice.wav'
            save()
            with patch.dict('os.environ', {'XINGMU_SADTALKER_DIR': ''}):
                with self.assertRaisesRegex(ValueError, '尚未設定本機 SadTalker'):
                    render(manifest, output)
            self.assertFalse(output.exists())

            # Model stand-in exercises the actual process and media handoff.
            engine = root / 'local model'
            engine.mkdir()
            (engine / 'checkpoints').mkdir()
            (engine / 'inference.py').write_text("""
import argparse
import subprocess
import sys
from pathlib import Path

assert sys.prefix != sys.base_prefix
parser = argparse.ArgumentParser()
parser.add_argument('--source_image', type=Path, required=True)
parser.add_argument('--driven_audio', type=Path, required=True)
parser.add_argument('--result_dir', type=Path, required=True)
parser.add_argument('--preprocess', choices=['full'], required=True)
parser.add_argument('--batch_size', choices=['1'], required=True)
parser.add_argument('--still', action='store_true')
parser.add_argument('--cpu', action='store_true')
args = parser.parse_args()
assert args.still and args.cpu and args.source_image.is_file()
assert args.driven_audio.read_bytes().startswith(b'RIFF')
args.result_dir.mkdir()
subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc=s=160x90:r=25:d=0.5', '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p', str(args.result_dir / 'sample.mp4')], check=True)
""", encoding='utf-8')
            env = root / 'model-python'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(env)], check=True)
            python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            with patch.dict('os.environ', {
                'XINGMU_SADTALKER_DIR': str(engine),
                'XINGMU_SADTALKER_PYTHON': str(python),
            }):
                render(manifest, output)
            info = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=nb_frames', '-of', 'json', str(output),
            ]))
            self.assertEqual(int(info['streams'][0]['nb_frames']), 50)
