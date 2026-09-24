"""コマンドライン

python -m audio2chordpro song.mp3 --midi song.mid --lyrics lyrics.txt -o song.cho --title 曲名 --artist 歌手
python -m audio2chordpro song.mp3 --midi song.mid -o chords.cho                  # 歌詞なし（コード譜だけ）
python -m audio2chordpro song.mp3 --midi song.mid --alignment song.align.json -o song.cho   # 保存したアライメントから再出力
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .align import Alignment
from .melody import SHEETSAGE_MODEL
from .pipeline import Options, prepare, render_chordpro, transcribe
from .render import RenderOptions
from .song_info import SongInfo


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="audio2chordpro", description="音源 + AMT の MIDI + 歌詞 → ChordPro")
    ap.add_argument("audio", type=Path, help="音源（mp3 / wav）")
    ap.add_argument("--midi", type=Path, required=True, help="AMT の MIDI（コード・調・拍・歌メロ）")
    ap.add_argument("--lyrics", type=Path, help="歌詞のテキストファイル（UTF-8、空行 = 段落）")
    ap.add_argument("-o", "--out", type=Path, help="出力する ChordPro（省略時は標準出力）")
    g = ap.add_argument_group("メタデータ")
    for name, label in (
        ("title", "曲名"),
        ("artist", "歌"),
        ("lyricist", "作詞"),
        ("composer", "作曲"),
        ("arranger", "編曲"),
    ):
        g.add_argument(f"--{name}", default="", help=label)
    g = ap.add_argument_group("アライメント")
    g.add_argument("--save-alignment", type=Path, help="モーラごとの時刻を JSON で保存する")
    g.add_argument("--alignment", type=Path, help="保存したアライメントを使う（音声処理をしない）")
    g.add_argument("--cache-dir", type=Path, default=Path("cache"), help="中間結果の置き場所（既定 ./cache）")
    g.add_argument(
        "--melody",
        default="sheetsage",
        choices=["sheetsage", "amt"],
        help="歌メロの取得元（amt = MIDI の melody トラック）",
    )
    g.add_argument("--beats", default="amt", choices=["amt", "sheetsage"], help="拍の取得元")
    g.add_argument(
        "--sheetsage-model",
        default=SHEETSAGE_MODEL,
        help="SheetSage2 のモデル（Hugging Face のリポジトリ名かダウンロード済みのディレクトリ）",
    )
    g = ap.add_argument_group("出力の書式")
    g.add_argument("--simplify", action="store_true", help="E#/B#/Cb/Fb を F/C/B/E と綴る")
    g.add_argument("--head-outside", action="store_true", help="行頭のコードを括弧の外に書く")
    g.add_argument("--no-tail-grid", action="store_true", help="行末の後ろのコードを小節グリッドにしない")
    return ap


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    info = SongInfo(args.title, args.artist, args.lyricist, args.composer, args.arranger)
    opt = Options(
        cache_dir=args.cache_dir,
        melody=args.melody,
        beats=args.beats,
        sheetsage_model=args.sheetsage_model,
        render=RenderOptions(simplify=args.simplify, head_outside=args.head_outside, tail_grid=not args.no_tail_grid),
    )
    if args.alignment:
        tl, notes = prepare(args.audio, args.midi, opt)
        alignment = Alignment.from_dict(json.loads(args.alignment.read_text(encoding="utf-8")))
        cho = render_chordpro(tl, alignment, notes, info, opt)
    else:
        lyrics = args.lyrics.read_text(encoding="utf-8") if args.lyrics else None
        result = transcribe(args.audio, args.midi, lyrics, info, opt)
        cho, alignment = result.chordpro, result.alignment
        if args.save_alignment and alignment:
            args.save_alignment.write_text(
                json.dumps(alignment.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
            )
    if args.out:
        args.out.write_text(cho, encoding="utf-8")
    else:
        sys.stdout.write(cho)
