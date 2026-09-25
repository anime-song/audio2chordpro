"""音源 + AMT の MIDI（省略時は tsumugi で作る）+ 歌詞 → ChordPro"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .align import Alignment, align_lyrics
from .melody import SHEETSAGE_MODEL, melody_notes, run_sheetsage, sheetsage_beats
from .render import RenderOptions, build_chordpro, to_beats
from .song_info import SongInfo
from .timeline import Timeline, load_timeline
from .vocals import ctc_emissions, load_vocab, separate_vocals, vocal_rms_db

log = logging.getLogger(__name__)


@dataclass
class Options:
    cache_dir: str | Path = "cache"  # tsumugi・ボーカル分離・CTC・SheetSage2 の結果を置く場所
    # 大きい中間ファイル（tsumugi のステム・ボーカル分離・CTC）だけ別の場所に置く（None = cache_dir）。消しても作り直せる
    scratch_dir: str | Path | None = None
    models_dir: str | Path | None = None  # tsumugi のソース・チェックポイントの置き場（None = cache_dir）
    melody: str = "sheetsage"  # 歌メロの取得元 "sheetsage" | "amt"（MIDI の melody トラック）
    beats: str = "amt"  # 拍の取得元 "amt"（MIDI のテンポマップ）| "sheetsage"
    sheetsage_model: str | Path = SHEETSAGE_MODEL  # Hugging Face のリポジトリ名かローカルのディレクトリ
    melody_prior: bool = True  # メロディの発音時刻をアライメントの事前分布に使う
    snap_to_notes: bool = True  # モーラの開始を歌メロのノートに寄せる
    render: RenderOptions = field(default_factory=RenderOptions)


@dataclass
class Result:
    chordpro: str
    alignment: Alignment | None
    timeline: Timeline
    midi: Path | None = None  # 使った MIDI（tsumugi で作ったときはそのパス）


def make_midi(audio: str | Path, options: Options | None = None) -> Path:
    """tsumugi で音源から AMT の MIDI を作る（scratch_dir にキャッシュ）"""
    from .providers.midi import TsumugiMidiProvider

    opt = options or Options()
    return TsumugiMidiProvider(scratch_dir(opt), models_dir=opt.models_dir)(audio)


def prepare(
    audio: str | Path,
    midi: str | Path | None = None,
    options: Options | None = None,
    chords: list[tuple[float, str]] | None = None,
) -> tuple[Timeline, list]:
    """拍タイムライン（コード・調・拍）と歌メロのノートを用意する（必要なら tsumugi・SheetSage2 を実行）

    chords: 手で直したコード (秒, コード名)。与えると MIDI のコードの代わりに使う"""
    opt = options or Options()
    audio = Path(audio)
    if midi is None:
        midi = make_midi(audio, opt)
    if "sheetsage" in (opt.melody, opt.beats):
        run_sheetsage(audio, opt.cache_dir, opt.sheetsage_model)
    beats = sheetsage_beats(audio, opt.cache_dir) if opt.beats == "sheetsage" else None
    tl = load_timeline(str(midi), beats=beats, chords=chords)
    for m in tl.repairs:
        log.info("[テンポ] %s", m)
    return tl, melody_notes(tl, opt.melody, audio, opt.cache_dir)


def transcribe(
    audio: str | Path,
    midi: str | Path | None = None,
    lyrics: str | None = None,
    info: SongInfo | None = None,
    options: Options | None = None,
) -> Result:
    """ChordPro を作る。

    audio  : 音源（mp3/wav）
    midi   : AMT の MIDI（コード・調・拍・歌メロ）。コードと調は常にこの MIDI のもの。None なら tsumugi で作る
    lyrics : 歌詞テキスト（改行区切り、空行 = 段落）。None ならコード譜だけ
    info   : タイトル・歌手・作詞作曲などのメタデータ"""
    opt = options or Options()
    audio = Path(audio)
    midi = Path(midi) if midi is not None else make_midi(audio, opt)
    tl, notes = prepare(audio, midi, opt)
    alignment = align_audio(audio, lyrics, tl, notes, opt) if lyrics else None
    return Result(render_chordpro(tl, alignment, notes, info, opt), alignment, tl, midi)


def scratch_dir(options: Options | None = None) -> Path:
    """大きい中間ファイルの置き場"""
    opt = options or Options()
    return Path(opt.scratch_dir if opt.scratch_dir is not None else opt.cache_dir)


def ctc_path(audio: str | Path, options: Options | None = None) -> Path:
    return scratch_dir(options) / "ctc" / f"{Path(audio).stem}.npy"


def vocal_features(audio: str | Path, options: Options | None = None) -> tuple[Path, np.ndarray]:
    """ボーカル分離（WAV のパス）と CTC の事後確率（scratch_dir にキャッシュ）"""
    opt = options or Options()
    vocals = separate_vocals(audio, scratch_dir(opt))
    return vocals, ctc_emissions(vocals, cache=ctc_path(audio, opt))


def align_audio(audio: str | Path, lyrics: str, tl: Timeline, notes, options: Options | None = None) -> Alignment:
    """歌詞を歌声に合わせる（モーラごとの発声時刻）"""
    opt = options or Options()
    vocals, E = vocal_features(audio, opt)
    note_times = grid_times = None
    if opt.melody_prior:
        note_times = np.array(sorted(float(tl.beat2sec(n.on)) for n in notes))
        grid_times = np.array([float(tl.beat2sec(b)) for b in np.arange(0, len(tl.beat_times) - 1, 0.5)])
    return align_lyrics(lyrics, E, load_vocab(), vocal_rms_db(vocals), note_times, grid_times)


def render_chordpro(
    tl: Timeline, alignment: Alignment | None, notes=None, info: SongInfo | None = None, options: Options | None = None
) -> str:
    """タイムラインとアライメントから ChordPro を作る（保存・修正したアライメントからの再出力にも使う）"""
    opt = options or Options()
    lines = to_beats(alignment, tl, notes, snap=opt.snap_to_notes) if alignment else []
    return build_chordpro(tl, lines, (info or SongInfo()).directives(), opt.render)
