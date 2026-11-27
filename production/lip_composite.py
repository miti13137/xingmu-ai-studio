"""Finish SadTalker's full-image render if its OpenCV clone exceeds the image bounds.

Run with the model's Python environment.
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def composite(model, source, animation, output):
    sys.path.insert(0, str(model))
    from src.utils.croper import Preprocesser

    portrait = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    if portrait is None:
        raise ValueError('無法讀取角色圖')
    height, width = portrait.shape[:2]
    preprocessor = Preprocesser('cpu')
    rgb = cv2.cvtColor(portrait, cv2.COLOR_BGR2RGB)
    landmarks = preprocessor.get_landmark(rgb)
    if landmarks is None:
        raise ValueError('找不到可還原的人臉位置')
    scaled, crop, quad = preprocessor.align_face(Image.fromarray(rgb), landmarks, output_size=512)
    origin_x, origin_y = crop[:2]
    left, top, right, bottom = map(int, quad)
    x1, x2 = (round(x * width / scaled[0]) for x in (origin_x + left, origin_x + right))
    y1, y2 = (round(y * height / scaled[1]) for y in (origin_y + top, origin_y + bottom))
    patch_width, patch_height = x2 - x1, y2 - y1
    cl, ct, cr, cb = max(x1, 0), max(y1, 0), min(x2, width), min(y2, height)
    if cr <= cl or cb <= ct or patch_width > width * 2 or patch_height > height * 2:
        raise ValueError('人臉合成範圍無效')
    feather = max(min(patch_width, patch_height) * 0.065, 3)
    xs = np.arange(cl - x1, cr - x1)
    ys = np.arange(ct - y1, cb - y1)
    distance = np.minimum(
        np.minimum(xs, patch_width - 1 - xs)[None, :],
        np.minimum(ys, patch_height - 1 - ys)[:, None],
    )
    alpha = np.clip(distance / feather, 0, 1).astype(np.float32)[..., None]
    info = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=width,height,r_frame_rate', '-of', 'json', str(animation),
    ]))['streams'][0]
    frame_width, frame_height = info['width'], info['height']
    frame_size = frame_width * frame_height * 3
    if not 0 < frame_size <= 2048 * 2048 * 3:
        raise ValueError('人臉動畫尺寸無效')

    decode = ['ffmpeg', '-v', 'error', '-i', str(animation), '-map', '0:v:0',
              '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
    encode = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
              '-s', f'{width}x{height}', '-r', info['r_frame_rate'], '-i', '-',
              '-an', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
              '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(output)]
    with subprocess.Popen(decode, stdout=subprocess.PIPE) as reader, \
         subprocess.Popen(encode, stdin=subprocess.PIPE) as writer:
        try:
            while data := reader.stdout.read(frame_size):
                frame = np.frombuffer(data, dtype=np.uint8).reshape(frame_height, frame_width, 3)
                face = cv2.resize(frame, (patch_width, patch_height))
                face = face[ct-y1:cb-y1, cl-x1:cr-x1]
                canvas = portrait.copy()
                region = canvas[ct:cb, cl:cr]
                region[:] = face * alpha + region * (1 - alpha)
                writer.stdin.write(canvas.tobytes())
        finally:
            reader.stdout.close()
            writer.stdin.close()
        if reader.wait() != 0 or writer.wait() != 0:
            raise ValueError('人臉合成輸出失敗')


if __name__ == '__main__':
    composite(*map(Path, sys.argv[1:]))
