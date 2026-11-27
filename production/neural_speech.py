"""Optional local Kokoro Mandarin speech synthesis."""
import io
import os
import sys
import wave
from array import array
from functools import lru_cache
from pathlib import Path
from threading import Lock

VOICES = {'mandarin-female': 3, 'mandarin-male': 58}
LOCK = Lock()


@lru_cache(maxsize=1)
def create_engine(root, model):
    try:
        import sherpa_onnx
        from opencc import OpenCC
    except ImportError as exc:
        raise ValueError('請先為星幕的 Python 安裝 sherpa-onnx 及 OpenCC') from exc
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            provider='cpu',
            num_threads=2,
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(model),
                voices=str(root / 'voices.bin'),
                tokens=str(root / 'tokens.txt'),
                lexicon=','.join(str(root / name) for name in ('lexicon-us-en.txt', 'lexicon-zh.txt')),
            ),
        ),
    )
    if not config.validate():
        raise ValueError('Kokoro 模型設定無效')
    return sherpa_onnx.OfflineTts(config), OpenCC('t2s')


def synthesize(dialogue, rate, voice):
    configured = os.environ.get('XINGMU_KOKORO_DIR')
    if not configured:
        raise ValueError('尚未設定 XINGMU_KOKORO_DIR，請參閱開發測試說明')
    root = Path(configured).expanduser().resolve()
    model = root / 'model.int8.onnx'
    if not model.is_file():
        model = root / 'model.onnx'
    required = (model, root / 'voices.bin', root / 'tokens.txt',
                root / 'lexicon-zh.txt', root / 'lexicon-us-en.txt')
    if not all(path.is_file() for path in required):
        raise ValueError('Kokoro 模型檔不完整，請使用已完整解壓縮的 1.1 多語版')
    if ',' in str(root):
        raise ValueError('Kokoro 模型路徑不可含逗號')
    with LOCK:
        engine, converter = create_engine(root, model)
        audio = engine.generate(converter.convert(dialogue), sid=VOICES[voice], speed=1.0 + rate * 0.1)
    rate_hz = audio.sample_rate
    if rate_hz <= 0 or len(audio.samples) == 0:
        raise ValueError('Kokoro 沒有生成有效配音')
    if len(audio.samples) * 2 > 16 * 1024 * 1024 - 44:
        raise ValueError('配音檔案過大，請縮短台詞')
    pcm = array('h', (round(max(-1.0, min(1.0, float(v))) * 32767) for v in audio.samples))
    if sys.byteorder == 'big':
        pcm.byteswap()
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, rate_hz, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm.tobytes())
    return output.getvalue()
