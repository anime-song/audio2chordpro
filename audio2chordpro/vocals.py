"""ボーカル分離と、歌声に対する CTC の事後確率（強制アライメントの入力）"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np

CTC_MODEL = "reazon-research/japanese-wav2vec2-base-rs35kh"
SR = 16000
HOP = 0.02  # wav2vec2 のフレーム間隔 [s]


def separate_vocals(audio: str | Path, cache_dir: str | Path) -> Path:
    """ボーカルを demucs（htdemucs）で分離して WAV のパスを返す（cache_dir にキャッシュ）。

    音源は librosa で読んでから渡す。demucs 自身の読み込みは mp3 によって先頭の無音の扱いが違い、
    ボーカルが数十 ms ずれることがある"""
    audio, cache_dir = Path(audio), Path(cache_dir)
    out = cache_dir / "sep" / "htdemucs" / audio.stem / "vocals.wav"
    if out.exists():
        return out
    import librosa
    import soundfile as sf
    import torch
    from demucs.api import Separator

    # shifts=0: 既定のランダムな時間シフトをやめて、同じ音源からは同じ結果が出るようにする
    sep = Separator("htdemucs", device="cuda" if torch.cuda.is_available() else "cpu", shifts=0, progress=True)
    y, _ = librosa.load(str(audio), sr=sep.samplerate, mono=False)
    y = np.atleast_2d(y)
    if len(y) != sep.audio_channels:
        y = np.repeat(y[:1], sep.audio_channels, axis=0)
    _, stems = sep.separate_tensor(torch.from_numpy(y))
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), stems["vocals"].numpy().T, sep.samplerate, subtype="PCM_16")
    return out


def load_vocab() -> dict[str, int]:
    """CTC モデルの語彙（トークン → ID）"""
    warnings.filterwarnings("ignore")
    from transformers import AutoProcessor

    return AutoProcessor.from_pretrained(CTC_MODEL).tokenizer.get_vocab()


def ctc_emissions(
    vocals: str | Path, cache: str | Path | None = None, win_s: float = 20.0, ctx_s: float = 2.0
) -> np.ndarray:
    """(フレーム数, 語彙数) の log 事後確率。長い曲は前後に文脈をつけた窓に分けて計算する"""
    if cache and Path(cache).exists():
        return np.load(cache)
    warnings.filterwarnings("ignore")
    import librosa
    import torch
    from transformers import Wav2Vec2ForCTC

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = Wav2Vec2ForCTC.from_pretrained(CTC_MODEL).to(dev).eval()
    y, _ = librosa.load(str(vocals), sr=SR, mono=True)
    hop, win, ctx = int(HOP * SR), int(win_s * SR), int(ctx_s * SR)
    outs = []
    for s in range(0, len(y), win):
        a, b = max(0, s - ctx), min(len(y), s + win + ctx)
        x = y[a:b]
        x = (x - x.mean()) / (x.std() + 1e-7)
        with torch.no_grad():
            lg = model(torch.tensor(x)[None].to(dev)).logits[0].float().log_softmax(-1).cpu().numpy()
        f0, n = (s - a) // hop, (min(s + win, len(y)) - s) // hop
        outs.append(lg[f0 : f0 + n])
    E = np.concatenate(outs).astype(np.float32)
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, E)
    return E


def vocal_rms_db(vocals: str | Path) -> np.ndarray:
    """フレーム（20ms）ごとのボーカルの RMS [dB]"""
    import librosa

    y, _ = librosa.load(str(vocals), sr=SR, mono=True)
    hop = int(HOP * SR)
    r = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop, center=True)[0]
    return 20 * np.log10(r + 1e-6)
