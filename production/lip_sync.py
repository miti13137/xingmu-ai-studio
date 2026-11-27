"""Run a locally installed SadTalker model on one portrait and voice track."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


MODEL_LOCK = threading.Lock()
REQUIRED_WEIGHTS = (
    'checkpoints/SadTalker_V0.0.2_256.safetensors',
    'checkpoints/mapping_00109-model.pth.tar',
    'gfpgan/weights/alignment_WFLW_4HG.pth',
    'gfpgan/weights/detection_Resnet50_Final.pth',
)


def configured_engine():
    configured_root = os.environ.get('XINGMU_SADTALKER_DIR')
    if not configured_root:
        raise ValueError('尚未設定本機 SadTalker，請參閱開發測試說明')
    root = Path(configured_root).expanduser().resolve()
    script = root / 'inference.py'
    if not script.is_file() or not (root / 'checkpoints').is_dir():
        raise ValueError('找不到 SadTalker 程式或模型資料夾')
    executable = Path(os.environ.get('XINGMU_SADTALKER_PYTHON', sys.executable)).expanduser().absolute()
    if not executable.is_file():
        raise ValueError('找不到 SadTalker 專用 Python')
    missing = [name for name in REQUIRED_WEIGHTS if not (root / name).is_file()]
    if missing:
        raise ValueError('缺少 SadTalker 模型檔：' + '、'.join(missing))
    device = os.environ.get('XINGMU_SADTALKER_DEVICE', 'cpu').lower()
    if device not in ('cpu', 'auto'):
        raise ValueError('模型裝置設定須為 cpu 或 auto')
    try:
        timeout = int(os.environ.get('XINGMU_SADTALKER_TIMEOUT_SECONDS', '900'))
    except ValueError as exc:
        raise ValueError('模型處理時間須以整數秒設定') from exc
    if not 60 <= timeout <= 7200:
        raise ValueError('模型處理時間須介於 60 與 7200 秒')
    return root, script, executable, device, timeout


def cache_destination(portrait, audio, root, executable, device):
    location = os.environ.get('XINGMU_LIP_SYNC_CACHE_DIR')
    if not location:
        return None
    digest = hashlib.sha256(b'xingmu-sadtalker-256-full-still-v1')
    digest.update(repr((str(root), str(executable), device)).encode())
    for file in (portrait, audio, root / 'inference.py'):
        # Include each file length to keep the input boundaries unambiguous.
        digest.update(str(file.stat().st_size).encode() + b':')
        with file.open('rb') as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    for relative in REQUIRED_WEIGHTS:
        stat = (root / relative).stat()
        digest.update(repr((relative, stat.st_size, stat.st_mtime_ns)).encode())
    folder = Path(location).expanduser().resolve()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError('無法建立嘴型動畫快取資料夾') from exc
    return folder / (digest.hexdigest() + '.mp4')


def is_mp4(path):
    try:
        with path.open('rb') as stream:
            return stream.read(8)[4:8] == b'ftyp'
    except OSError:
        return False


def publish_cache(result, cache):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with result.open('rb') as source:
                shutil.copyfileobj(source, stream)
        temporary.replace(cache)
    except OSError:
        # A valid render can still be used when its optional cache is unwritable.
        return
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def animate(portrait, audio, workdir):
    root, script, executable, device, timeout = configured_engine()
    cache = cache_destination(portrait, audio, root, executable, device)
    with MODEL_LOCK:
        if cache is not None and is_mp4(cache):
            # Render from a job-local copy, so clearing the cache cannot disrupt the job.
            try:
                return Path(shutil.copyfile(cache, workdir / 'cached.mp4'))
            except OSError:
                pass
        result = invoke_model(root, script, executable, device, timeout, portrait, audio, workdir)
        if cache is not None:
            publish_cache(result, cache)
        return result


def invoke_model(root, script, executable, device, timeout, portrait, audio, workdir):
    results = workdir / 'results'
    try:
        with (workdir / 'inference.log').open('wb') as log:
            result = subprocess.run([
                str(executable), str(script), '--source_image', str(portrait),
                '--driven_audio', str(audio), '--result_dir', str(results),
                '--preprocess', 'full', '--size', '256', '--still', '--batch_size', '1',
                *(['--cpu'] if device == 'cpu' else []),
            ], cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('本機嘴型同步逾時，請改用較短的台詞') from exc
    outputs = list(results.glob('*.mp4'))
    if result.returncode != 0 or len(outputs) != 1 or not is_mp4(outputs[0]):
        with (workdir / 'inference.log').open('rb') as log:
            log.seek(max(0, log.seek(0, 2) - 900))
            detail = log.read().decode(errors='replace').strip()
        message = f'SadTalker 未能輸出影片（結束代碼 {result.returncode}）。請檢查模型、執行環境及角色正面照。'
        raise ValueError(message + ('\n' + detail if detail else ''))
    return outputs[0]
