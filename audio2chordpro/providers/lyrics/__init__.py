"""歌詞サイトから、曲名で検索して歌詞とメタデータ（歌手・作詞・作曲・編曲）を取る

    from audio2chordpro.providers.lyrics import search, fetch

    hits = search("曲名", artist="歌手名")   # 候補の一覧（UI で選んでもらう）
    page = fetch(hits[0])                    # 選んだ曲の歌詞とメタデータ
    transcribe(audio, midi, page.lyrics, page.info)

対応サイト: うたてん。
歌ネット・歌time はボット対策（Cloudflare のチャレンジ）でプログラムからの取得を受け付けないため対応していない。
取得した歌詞は各サイトの利用規約に従い、個人的な利用の範囲で使うこと。
"""

from __future__ import annotations

import logging
import unicodedata

import requests

from ...song_info import SongInfo
from .base import LyricsPage, LyricsSite, SiteBlocked, SongHit
from .utaten import UtaTen

log = logging.getLogger(__name__)

SITES: dict[str, LyricsSite] = {s.name: s for s in (UtaTen(),)}


def search(title: str, artist: str = "", sites: list[str] | None = None, page: int = 1) -> list[SongHit]:
    """各サイトで曲名（部分一致）・歌手名で検索し、候補を並べて返す。取得できなかったサイトは飛ばす"""
    hits: list[SongHit] = []
    for name in sites or list(SITES):
        try:
            hits += SITES[name].search(title, artist, page)
        except (SiteBlocked, requests.RequestException) as e:
            log.warning("%s: 検索できませんでした（%s）", SITES[name].label, e)
    return hits


def fetch(hit: SongHit | str) -> LyricsPage:
    """検索結果（または歌詞ページの URL）から、歌詞とメタデータを取る"""
    url = hit.url if isinstance(hit, SongHit) else hit
    for site in SITES.values():
        if (isinstance(hit, SongHit) and hit.site == site.name) or site.handles(url):
            return site.fetch(url)
    raise ValueError(f"対応していないサイトです: {url}")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold().replace(" ", "")


class SiteLyricsProvider:
    """LyricsProvider の実装：曲名が一致し（歌手名を指定すればそれも含む）、最初に見つかった曲を使う"""

    def __init__(self, sites: list[str] | None = None) -> None:
        self.sites = sites

    def __call__(self, title: str, artist: str = "") -> tuple[SongInfo, str] | None:
        for hit in search(title, artist, self.sites):
            if _norm(hit.title) == _norm(title) and (not artist or _norm(artist) in _norm(hit.artist)):
                page = fetch(hit)
                return page.info, page.lyrics
        return None


__all__ = ["SITES", "LyricsPage", "LyricsSite", "SiteBlocked", "SiteLyricsProvider", "SongHit", "fetch", "search"]
