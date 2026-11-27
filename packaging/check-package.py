"""Exercise a frozen build without relying on FFmpeg or Python in the app's PATH."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


def verify(executable):
    tools = Path(os.environ['XINGMU_MEDIA_TOOLS'])
    dot_exe = '.exe'
    fixture = subprocess.check_output([
        str(tools / f'ffmpeg{dot_exe}'), '-v', 'error',
        '-f', 'lavfi', '-i', 'color=c=teal:s=160x90:r=25:d=0.8',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=0.8',
        '-c:v', 'libx264', '-c:a', 'aac', '-f', 'mp4',
        '-movflags', 'frag_keyframe+empty_moov', '-',
    ])
    env = {**os.environ, 'PATH': str(Path(os.environ.get('SystemRoot', '/usr')) / ('System32' if os.name == 'nt' else 'bin'))}
    with subprocess.Popen(
        [str(executable), '--no-browser'], env=env, stdout=subprocess.PIPE,
    ) as app:
        try:
            for line in app.stdout:
                print(line.decode('utf-8', errors='replace').rstrip(), flush=True)
                match = re.search(rb'http://127\.0\.0\.1:\d+/', line)
                if match:
                    break
            else:
                raise RuntimeError('Cannot find local server address.')
            base = match.group().decode().rstrip('/')
            page = urlopen(base, timeout=10).read().decode()
            assert 'Google Flow 製片' in page
            token = re.search(r"const token='([\w-]+)'", page).group(1)

            def request(path, body=None):
                headers = {'X-Xingmu-Token': token}
                if body is not None:
                    headers['Content-Type'] = 'application/octet-stream'
                with urlopen(Request(base + path, data=body, headers=headers), timeout=15) as response:
                    return response.read()

            config = {'ratio': '9:16', 'shots': [{'dialogue': '曼菁，這張票還在。'}]}
            job = json.loads(request('/api/jobs', json.dumps(config).encode()))['id']
            path = f'/api/jobs/{job}'
            request(path + '/clip/0', fixture)
            request(path + '/render', b'')
            for _ in range(100):
                state = json.loads(request(path))
                if state['status'] == 'done':
                    break
                if state['status'] == 'error':
                    raise RuntimeError(state['error'])
                time.sleep(0.2)
            else:
                raise TimeoutError('Packaged workbench did not finish the export')
            movie = request(path + '/download')
            probe = subprocess.check_output([
                str(tools / f'ffprobe{dot_exe}'), '-v', 'error',
                '-show_entries', 'stream=codec_name,width,height', '-of', 'json', '-',
            ], input=movie)
            video, audio = json.loads(probe)['streams']
            assert (video['codec_name'], video['width'], video['height']) == ('h264', 720, 1280)
            assert audio['codec_name'] == 'aac'
            for name in ('ffmpeg.exe', 'ffprobe.exe'):
                assert list((Path(env['LOCALAPPDATA']) / 'XingmuStudio').glob(f'ffmpeg-*/{name}'))
            print('Packaged workbench opened, rendered subtitles and exported a 720p MP4.')
        finally:
            app.terminate()
            app.wait(timeout=10)


if __name__ == '__main__':
    verify(Path(sys.argv[1]).resolve())
