"""歌メロのノート（と拍）の取得元

  "amt"       : AMT の MIDI の melody トラック（同時発音は最高音だけ残す）
  "sheetsage" : SheetSage2 の歌メロ（melody_vocal.mid）。拍・小節頭（beat.lab / downbeat.lab）も使える

SheetSage2 の出力は cache_dir/sheetsage/<曲名>/ に置き、2回目以降は再利用する。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np

from .timeline import Note, Timeline, skyline

log = logging.getLogger(__name__)

SHEETSAGE_MODEL = "m-a-p/SheetSage2"  # 重みは CC BY-NC 4.0（非商用）
SHEETSAGE_REVISION = "488abe28ef4db3dbb056da19cb49d80f4b14bc61"  # 動作を確認したコミット（リモートコードも固定する）


def sheetsage_dir(audio: str | Path, cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "sheetsage" / Path(audio).stem


def has_sheetsage(audio: str | Path, cache_dir: str | Path) -> bool:
    return (sheetsage_dir(audio, cache_dir) / "melody_vocal.mid").exists()


def _copy_remote_code(model_dir: Path) -> None:
    """ローカルのモデルを読むとき、transformers がリモートコードの一部を modules キャッシュへコピーし損ねることがあるので補う"""
    from transformers.utils import HF_MODULES_CACHE

    dst = Path(HF_MODULES_CACHE) / "transformers_modules" / model_dir.name
    dst.mkdir(parents=True, exist_ok=True)
    for f in model_dir.glob("*.py"):
        if not (dst / f.name).exists():
            shutil.copy(f, dst / f.name)


def run_sheetsage(audio: str | Path, cache_dir: str | Path, model: str | Path = SHEETSAGE_MODEL) -> Path:
    """SheetSage2 で採譜して出力ディレクトリを返す（済んでいれば何もしない）。

    model: Hugging Face のリポジトリ名か、ダウンロード済みのモデルのディレクトリ"""
    out = sheetsage_dir(audio, cache_dir)
    if has_sheetsage(audio, cache_dir):
        return out
    import torch
    from transformers import AutoModel

    log.info("SheetSage2 で歌メロを採譜しています")
    if Path(model).is_dir():
        _copy_remote_code(Path(model))
        m = AutoModel.from_pretrained(str(model), trust_remote_code=True)
    else:
        m = AutoModel.from_pretrained(str(model), revision=SHEETSAGE_REVISION, trust_remote_code=True)
    m = m.eval().to("cuda" if torch.cuda.is_available() else "cpu")
    out.mkdir(parents=True, exist_ok=True)
    m.transcribe(str(audio), output_dir=str(out))
    del m
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return out


def _lab_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [r.split("\t") for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]


def sheetsage_beats(audio: str | Path, cache_dir: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """SheetSage2 の (拍[s], 小節頭[s])"""
    d = sheetsage_dir(audio, cache_dir)
    beats = np.array([float(r[0]) for r in _lab_rows(d / "beat.lab")])
    downbeats = np.array([float(r[0]) for r in _lab_rows(d / "downbeat.lab")])
    return beats, downbeats


def melody_notes(
    tl: Timeline, source: str = "amt", audio: str | Path | None = None, cache_dir: str | Path | None = None
) -> list[Note]:
    """歌メロのノート（拍単位、単旋律）"""
    if source == "amt":
        return skyline(tl.melody)
    if source != "sheetsage":
        raise ValueError(f"unknown melody source: {source}")
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(str(sheetsage_dir(audio, cache_dir) / "melody_vocal.mid"))
    notes = [
        Note(float(tl.sec2beat(n.start)), float(tl.sec2beat(n.end)), n.pitch)
        for inst in pm.instruments
        if not inst.is_drum
        for n in inst.notes
    ]
    notes.sort(key=lambda n: (n.on, -n.pitch))
    return skyline(notes)
