"""tsumugi（https://github.com/anime-song/tsumugi）で音源から AMT の MIDI を作る

tsumugi はパッケージとしてインストールできない（torch のバージョンも固定されている）ため、
決まったコミットのソースを GitHub から cache_dir にダウンロードして、そのまま import する。
依存ライブラリは audio2chordpro の側で入れてある（torch は audio2chordpro の 2.8 で動く）。

流れ（tsumugi の Colab と同じ）: ステム分離 → ステムごとに採譜 → MIDI をまとめる → ビート・コード・調を推定
出力: <曲名>_beat_chord.mid（トラック "Predicted Tempo Map" / "Predicted Chords" / 楽器ごとのノート）
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

REPO = "anime-song/tsumugi"
COMMIT = "020edc1be0fd8459f26a415dc171e1c2fe0a0150"  # 動作を確認したコミット


@contextlib.contextmanager
def _chdir(path: Path):
    """tsumugi はチェックポイントを実行時のフォルダの checkpoints/ などに置くので、その間だけ移る"""
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _patch_stem_splitter() -> None:
    """stem-splitter の WAV 保存は TorchCodec（Windows では FFmpeg の DLL が要る）を使うので soundfile に替える"""
    import soundfile as sf
    import stem_splitter.inference as ssi

    ssi._save_wav_no_warning = lambda path, x, sr: sf.write(str(path), x.detach().cpu().float().numpy().T, sr)


class TsumugiMidiProvider:
    """MidiProvider の実装。結果は cache_dir/tsumugi/out/<曲名>/ に置き、2回目以降は再利用する

    source: tsumugi のソースのフォルダ（省略時は COMMIT を GitHub からダウンロード）"""

    def __init__(self, cache_dir: str | Path = "cache", source: str | Path | None = None):
        self.root = (Path(cache_dir) / "tsumugi").resolve()
        self.source = Path(source).resolve() if source else None

    def ensure_source(self) -> Path:
        """tsumugi のソースを用意してそのフォルダを返す"""
        if self.source:
            return self.source
        src = self.root / "src" / COMMIT
        if not (src / "instrument_agnostic_amt").is_dir():
            import requests

            log.info("tsumugi のソースをダウンロードしています（%s@%s）", REPO, COMMIT[:7])
            r = requests.get(f"https://codeload.github.com/{REPO}/zip/{COMMIT}", timeout=60)
            r.raise_for_status()
            tmp = src.with_name(src.name + ".tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                z.extractall(tmp)
            (inner,) = tmp.iterdir()  # tsumugi-<commit>/
            shutil.rmtree(src, ignore_errors=True)
            inner.rename(src)
            shutil.rmtree(tmp, ignore_errors=True)
        return src

    def output_path(self, audio: str | Path) -> Path:
        stem = Path(audio).stem
        return self.root / "out" / stem / "merged" / f"{stem}_beat_chord.mid"

    def __call__(self, audio: str | Path, out_dir: str | Path | None = None) -> Path:
        """音源から MIDI を作り、そのパスを返す（out_dir を指定すればそこにコピーする）"""
        audio = Path(audio).resolve()
        midi = self.output_path(audio)
        if not midi.exists():
            self._run(audio)
        if out_dir is not None:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            midi = Path(shutil.copy(midi, Path(out_dir) / midi.name))
        return midi

    def _run(self, audio: Path) -> None:
        src = str(self.ensure_source())
        if src not in sys.path:
            sys.path.insert(0, src)
        _patch_stem_splitter()
        from instrument_agnostic_amt.cli import infer_stem

        log.info("tsumugi で MIDI を作っています（ステム分離 → 採譜 → ビート・コード推定）")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with _chdir(self.root):
                infer_stem.run_stem_separated_transcription(
                    audio,
                    output_root=self.root / "out",
                    predict_beat_chord=True,
                    predict_velocity=False,  # コード譜には要らない
                )
        finally:  # 後の処理（ボーカル分離・CTC・SheetSage2）のために GPU を空ける
            infer_stem.STEM_PIPELINE_CACHE.clear()
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        if not self.output_path(audio).exists():
            raise RuntimeError(f"tsumugi がビート・コード付きの MIDI を出力しませんでした: {self.output_path(audio)}")
