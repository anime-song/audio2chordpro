"""歌詞サイトの検索（動作確認用のコマンド）

  python -m audio2chordpro.providers.lyrics 曲名 [--artist 歌手名]          # 候補の一覧
  python -m audio2chordpro.providers.lyrics 曲名 --pick 1 -o lyrics.txt    # 1番目の歌詞を保存し、メタデータを表示
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import SITES, fetch, search


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m audio2chordpro.providers.lyrics", description="歌詞サイトで曲を検索する")
    ap.add_argument("title", help="曲名（部分一致）")
    ap.add_argument("--artist", default="", help="歌手名")
    ap.add_argument("--site", action="append", choices=list(SITES), help="検索するサイト（既定: すべて）")
    ap.add_argument("--pick", type=int, help="この番号の曲の歌詞とメタデータを取る")
    ap.add_argument("--ruby", action="store_true", help="ふりがな付き「漢字(かな)」で保存する")
    ap.add_argument("-o", "--out", type=Path, help="歌詞の保存先（省略時は標準出力）")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    hits = search(args.title, args.artist, args.site)
    if args.pick is None:
        for n, h in enumerate(hits, 1):
            credit = "　".join(f"{k}：{v}" for k, v in (("作詞", h.lyricist), ("作曲", h.composer), ("編曲", h.arranger)) if v)
            print(f"{n:3d}. [{SITES[h.site].label}] {h.title} / {h.artist}　{credit}")
        if not hits:
            print("見つかりませんでした", file=sys.stderr)
        return
    if not 1 <= args.pick <= len(hits):
        sys.exit(f"--pick は 1〜{len(hits)} で指定してください")
    page = fetch(hits[args.pick - 1])
    print(json.dumps({"url": page.url, **page.to_dict()["info"], **page.extra}, ensure_ascii=False, indent=1), file=sys.stderr)
    text = page.lyrics_ruby if args.ruby and page.lyrics_ruby else page.lyrics
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
