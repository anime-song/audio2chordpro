"""歌詞サイトの共通部分：検索結果・歌詞ページのデータと、HTTP の取得"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from ...song_info import SongInfo

USER_AGENT = "audio2chordpro (+https://github.com/anime-song/audio2chordpro)"
MIN_INTERVAL = 1.0  # 同じサイトへのリクエストの最小間隔 [s]（サーバに負荷をかけない）
TIMEOUT = 20.0


class SiteBlocked(RuntimeError):
    """サイトがボット対策などで自動取得を受け付けなかった"""


@dataclass
class SongHit:
    """検索結果の1曲（UI で選んでもらうための情報）"""

    site: str  # サイトの名前（LyricsSite.name）
    title: str
    artist: str
    url: str  # 歌詞ページ
    lyricist: str = ""
    composer: str = ""
    arranger: str = ""
    beginning: str = ""  # 歌い出し（候補を見分ける手がかり）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LyricsPage:
    """歌詞ページから取ったメタデータと歌詞"""

    site: str
    url: str
    info: SongInfo
    lyrics: str  # 改行区切り、空行 = 段落（transcribe にそのまま渡せる）
    lyrics_ruby: str = ""  # ふりがな付き「漢字(かな)」（サイトにふりがながあれば）
    extra: dict = field(default_factory=dict)  # サイト固有の情報（タイトルのよみ、発売日など）

    def to_dict(self) -> dict:
        d = asdict(self)
        d["info"] = asdict(self.info)
        return d


class LyricsSite:
    """歌詞サイト1つ分。サブクラスで search_url / parse_search / parse_song を実装する"""

    name = ""  # 識別子（"utaten" など）
    label = ""  # 表示名
    hosts: tuple[str, ...] = ()  # このサイトの URL のホスト名

    def __init__(self) -> None:
        self._last = 0.0
        self._session = None

    # ---- サイトごとに実装する
    def search_url(self, title: str, artist: str = "", page: int = 1) -> str:
        raise NotImplementedError

    def parse_search(self, html: str) -> list[SongHit]:
        raise NotImplementedError

    def parse_song(self, html: str, url: str) -> LyricsPage:
        raise NotImplementedError

    # ---- 共通
    def search(self, title: str, artist: str = "", page: int = 1) -> list[SongHit]:
        """曲名（部分一致）と歌手名で検索する"""
        return self.parse_search(self.get(self.search_url(title, artist, page)))

    def fetch(self, url: str) -> LyricsPage:
        """歌詞ページからメタデータと歌詞を取る"""
        return self.parse_song(self.get(url), url)

    def handles(self, url: str) -> bool:
        return urlparse(url).hostname in self.hosts

    def get(self, url: str) -> str:
        import requests

        if self._session is None:
            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = self._session.get(url, timeout=TIMEOUT)
        finally:
            self._last = time.monotonic()
        if r.status_code in (403, 429, 503) and ("cf-mitigated" in r.headers or "Just a moment" in r.text):
            raise SiteBlocked(f"{self.label} がボット対策で自動取得を受け付けませんでした（{r.status_code}）")
        r.raise_for_status()
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else r.apparent_encoding
        return r.text
