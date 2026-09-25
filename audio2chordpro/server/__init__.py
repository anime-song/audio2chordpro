"""UI のサーバ

audio2chordpro serve                       # http://127.0.0.1:8000 を開く（プロジェクトは ./projects）
audio2chordpro serve --root D:/songs --port 8080 --no-browser

画面（web/ のビルド）が無ければ、GitHub の Release からビルド済みのものを取ってくる（server/web.py）
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from .app import create_app
from .jobs import Job, Runner
from .web import ensure_web


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="audio2chordpro serve", description="UI のサーバを起動する")
    ap.add_argument("--root", type=Path, default=Path("projects"), help="プロジェクトを置くフォルダ（既定 ./projects）")
    ap.add_argument(
        "--models-dir", type=Path, help="tsumugi のソース・チェックポイントの置き場（既定 ~/.cache/audio2chordpro）"
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを開かない")
    ap.add_argument(
        "--no-web-download", action="store_true", help="画面（web/ のビルド）が無くても GitHub から取ってこない"
    )
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    import uvicorn

    if not args.no_web_download:
        ensure_web()
    app = create_app(args.root, args.models_dir)
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, [f"http://{args.host}:{args.port}/"]).start()
    uvicorn.run(app, host=args.host, port=args.port)


__all__ = ["Job", "Runner", "create_app", "main"]
