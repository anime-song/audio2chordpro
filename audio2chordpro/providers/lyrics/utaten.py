"""うたてん（https://utaten.com/）

  検索     : /search/=/title=<曲名>/artist_name=<歌手名>/…/page=<n>/（曲名は部分一致、1ページ40件、人気順）
  歌詞ページ: /lyric/<id>/。歌詞はふりがな付き（<span class="ruby"><span class="rb">漢字</span><span class="rt">かな</span></span>）
"""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from ...song_info import SongInfo
from .base import LyricsPage, LyricsSite, SongHit

BASE = "https://utaten.com/"
ROLES = {"作詞": "lyricist", "作曲": "composer", "編曲": "arranger"}


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _names(el) -> str:
    """作詞者などの名前（複数なら「・」でつなぐ）"""
    if el is None:
        return ""
    links = [_text(a) for a in el.find_all("a")]
    return "・".join(n for n in links if n) if links else _text(el)


def _lyrics(body) -> tuple[str, str]:
    """歌詞の要素 → (歌詞, ふりがな付き歌詞)。<br> が改行、連続した <br> が段落の区切り"""
    plain, ruby = [""], [""]
    for node in body.children:
        name = getattr(node, "name", None)
        if name == "br":
            plain.append("")
            ruby.append("")
        elif name == "span" and "ruby" in (node.get("class") or []):
            rb, rt = _text(node.find(class_="rb")), _text(node.find(class_="rt"))
            plain[-1] += rb
            ruby[-1] += f"{rb}({rt})" if rt else rb
        elif name is None:  # 文字
            s = str(node).replace("\r", "").replace("\n", "")
            plain[-1] += s
            ruby[-1] += s
        else:
            s = node.get_text()
            plain[-1] += s
            ruby[-1] += s

    def tidy(lines: list[str]) -> str:
        out: list[str] = []
        for line in (x.strip() for x in lines):
            if line or (out and out[-1]):
                out.append(line)
        return "\n".join(out).strip() + "\n"

    return tidy(plain), tidy(ruby)


class UtaTen(LyricsSite):
    name = "utaten"
    label = "うたてん"
    hosts = ("utaten.com", "www.utaten.com")

    def search_url(self, title: str, artist: str = "", page: int = 1) -> str:
        q = lambda s: quote(s.strip(), safe="")  # noqa: E731
        return (
            f"{BASE}search/=/title={q(title)}/artist_name={q(artist)}/sub_title=/lyricist=/composer=/"
            f"beginning=/body=/tag=/sort=popular_sort_asc/page={page}/"
        )

    def parse_search(self, html: str) -> list[SongHit]:
        hits = []
        for tr in _soup(html).select("table.searchResult tr"):
            a = tr.select_one(".searchResult__title a")
            if a is None:
                continue
            hit = SongHit(
                site=self.name,
                title=_text(a),
                artist=_text(tr.select_one(".searchResult__artist > p")),
                url=urljoin(BASE, a["href"]),
                beginning=_text(tr.select_one(".lyricList__beginning")),
            )
            for p in tr.select(".searchResult__lyricist p"):
                label = _text(p).split("：", 1)[0]
                if label in ROLES:
                    setattr(hit, ROLES[label], _names(p.select_one(".songWriters")))
            hits.append(hit)
        return hits

    def parse_song(self, html: str, url: str) -> LyricsPage:
        soup = _soup(html)
        head = soup.select_one(".newLyricTitle__main")
        if head is not None:
            for s in head.select(".newLyricTitle_afterTxt"):
                s.decompose()
        info = SongInfo(title=_text(head), artist=_text(soup.select_one(".newLyricWork__name")))
        for dt in soup.select("dt.newLyricWork__title"):
            role = ROLES.get(_text(dt))
            dd = dt.find_next_sibling("dd")
            if role and dd is not None:
                setattr(info, role, _names(dd))
        body = soup.select_one(".lyricBody .hiragana") or soup.select_one(".lyricBody")
        if body is None:
            raise ValueError(f"歌詞が見つかりません: {url}")
        lyrics, ruby = _lyrics(body)
        extra = {}
        kana = _text(soup.select_one(".newLyricTitle__kana"))
        if kana:
            extra["title_kana"] = kana.split("：", 1)[-1]
        date = _text(soup.select_one(".newLyricWork__date"))
        if date:
            extra["release"] = date.replace("リリース", "").strip()
        return LyricsPage(self.name, url, info, lyrics, ruby, extra)
