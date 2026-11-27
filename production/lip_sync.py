"""Run a locally installed SadTalker model on one portrait and voice track."""
import os
import subprocess
import sys
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


def animate(portrait, audio, workdir):
    root, script, executable, device, timeout = configured_engine()
    results = workdir / 'results'
    try:
        with MODEL_LOCK, (workdir / 'inference.log').open('wb') as log:
            result = subprocess.run([
                str(executable), str(script), '--source_image', str(portrait),
                '--driven_audio', str(audio), '--result_dir', str(results),
                '--preprocess', 'full', '--size', '256', '--still', '--batch_size', '1',
                *(['--cpu'] if device == 'cpu' else []),
            ], cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('本機嘴型同步逾時，請改用較短的台詞') from exc
    outputs = list(results.glob('*.mp4'))
    if result.returncode != 0 or len(outputs) != 1:
        with (workdir / 'inference.log').open('rb') as log:
            log.seek(max(0, log.seek(0, 2) - 900))
            detail = log.read().decode(errors='replace').strip()
        message = f'SadTalker 未能輸出影片（結束代碼 {result.returncode}）。請檢查模型、執行環境及角色正面照。'
        raise ValueError(message + ('\n' + detail if detail else ''))
    return outputs[0]
