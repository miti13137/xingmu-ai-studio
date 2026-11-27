"""Offline Mandarin speech via installed Windows desktop voices."""
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import neural_speech

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voices = @($speech.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.Name -in @('zh-TW', 'zh-CN', 'zh-SG')
    } | Sort-Object { $_.VoiceInfo.Culture.Name -ne 'zh-TW' })
    if ($voices.Count -eq 0) { exit 3 }
    $speech.SelectVoice($voices[0].VoiceInfo.Name)
    $speech.Rate = [int]$env:XINGMU_SPEECH_RATE
    $speech.SetOutputToWaveFile((Join-Path $PWD 'voice.wav'))
    $speech.Speak([IO.File]::ReadAllText((Join-Path $PWD 'dialogue.txt')))
    $speech.SetOutputToNull()
} finally {
    $speech.Dispose()
}
"""


def synthesize(dialogue, rate, voice=None):
    if not isinstance(dialogue, str) or not dialogue.strip() or len(dialogue) > 500:
        raise ValueError('請輸入 1 至 500 字的配音台詞')
    if type(rate) is not int or not -3 <= rate <= 3:
        raise ValueError('配音速度須介於 -3 與 3')
    if voice is not None and voice != 'windows':
        if not isinstance(voice, str) or voice not in neural_speech.VOICES:
            raise ValueError('不支援這個配音音色')
        return neural_speech.synthesize(dialogue.strip(), rate, voice)
    engine = shutil.which('powershell.exe') if os.name == 'nt' else None
    if not engine:
        raise ValueError('這台電腦無法使用 Windows 本機國語語音')
    with tempfile.TemporaryDirectory(prefix='xingmu-speech-') as directory:
        folder = Path(directory)
        (folder / 'dialogue.txt').write_text(dialogue.strip(), encoding='utf-8')
        try:
            result = subprocess.run(
                [engine, '-NoProfile', '-NonInteractive', '-Command', SCRIPT],
                cwd=folder, env={**os.environ, 'XINGMU_SPEECH_RATE': str(rate)},
                capture_output=True, timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError('本機配音逾時，請縮短台詞再試') from exc
        if result.returncode == 3:
            raise ValueError('找不到可用的 Windows 國語桌面語音')
        if result.returncode:
            raise ValueError('Windows 本機配音失敗，請檢查系統語音設定')
        output = folder / 'voice.wav'
        try:
            with wave.open(str(output), 'rb') as wav:
                if not wav.getnframes():
                    raise ValueError('本機配音未產生音訊')
            if output.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('配音檔案過大，請縮短台詞')
            return output.read_bytes()
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError('本機配音未回傳有效 WAV') from exc
