"""Run a locally installed SadTalker model on one portrait and voice track."""
import os
import subprocess
import sys
from pathlib import Path


def animate(portrait, audio, workdir):
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
    results = workdir / 'results'
    try:
        with (workdir / 'inference.log').open('wb') as log:
            result = subprocess.run([
                str(executable), str(script), '--source_image', str(portrait),
                '--driven_audio', str(audio), '--result_dir', str(results),
                '--preprocess', 'full', '--still', '--batch_size', '1', '--cpu',
            ], cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=False)
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
