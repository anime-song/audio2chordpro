"""入力を自動で用意するための差し込み口

  * MidiProvider   : 音源から AMT の MIDI（コード・調・拍・歌メロ）を作る（例: tsumugi。未実装）
  * LyricsProvider : 曲名・歌手名から歌詞とメタデータを取ってくる（実装: providers.lyrics の歌詞サイト）

pipeline.transcribe はファイルパス／テキストを受け取るだけなので、これらの出力をそのまま渡せばよい。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..song_info import SongInfo


class MidiProvider(Protocol):
    def __call__(self, audio: Path, out_dir: Path) -> Path:
        """音源から AMT の MIDI を作り、そのパスを返す。MIDI に必要なもの:
        トラック "Predicted Tempo Map"（テンポ・拍子・調）、"Predicted Chords"（marker にコード名）、"melody"（歌メロ）"""
        ...


class LyricsProvider(Protocol):
    def __call__(self, title: str, artist: str = "") -> tuple[SongInfo, str] | None:
        """曲のメタデータと歌詞（改行区切り、空行 = 段落）を返す。見つからなければ None"""
        ...
