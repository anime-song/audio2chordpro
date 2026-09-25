"""曲のメタデータ：音源のタグ・ファイル名から読む。ChordPro の {title:} / {subtitle:} 行にする"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path

ROLES = (("lyricist", "作詞"), ("composer", "作曲"), ("arranger", "編曲"))


@dataclass
class SongInfo:
    title: str = ""
    artist: str = ""  # 歌
    lyricist: str = ""  # 作詞
    composer: str = ""  # 作曲
    arranger: str = ""  # 編曲

    @classmethod
    def from_dict(cls, d: dict) -> SongInfo:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names and v})

    def subtitle(self) -> str:
        """「歌：A　作詞：B　作曲・編曲：C」の形。同じ人の役割は「・」でまとめる"""
        parts = [f"歌：{self.artist}"] if self.artist else []
        groups: dict[str, list[str]] = {}
        for attr, label in ROLES:
            name = getattr(self, attr)
            if name:
                groups.setdefault(name, []).append(label)
        parts += [f"{'・'.join(labels)}：{name}" for name, labels in groups.items()]
        return "　".join(parts)

    def directives(self) -> list[str]:
        out = [f"{{title:{self.title}}}"] if self.title else []
        if self.subtitle():
            out.append(f"{{subtitle:{self.subtitle()}}}")
        return out


# ================================================================== 音源から
@dataclass
class AudioMeta:
    """音源のタグ・ファイル名から読めた曲情報"""

    info: SongInfo
    sources: dict[str, str] = field(default_factory=dict)  # 項目 → 出所 "tag" | "filename"
    lyrics: str = ""  # タグに埋め込まれた歌詞
    alternatives: list[tuple[str, str]] = field(default_factory=list)  # ファイル名の別の読み方 (曲名, 歌手)


def from_audio(path: str | Path) -> AudioMeta:
    """タグを優先し、無い曲名・歌手はファイル名から推す"""
    tags, lyrics = read_tags(path)
    meta = AudioMeta(SongInfo.from_dict(tags), {k: "tag" for k, v in tags.items() if v}, lyrics)
    guesses = parse_filename(Path(path).stem)
    if not meta.info.title:
        title, artist = guesses[0]
        meta.info.title, meta.sources["title"] = title, "filename"
        if artist and not meta.info.artist:
            meta.info.artist, meta.sources["artist"] = artist, "filename"
        meta.alternatives = guesses[1:]
    return meta


# タグの項目：ID3（mp3・wav）/ MP4（m4a）/ Vorbis コメント（flac・ogg）
_ID3 = {"title": "TIT2", "artist": "TPE1", "lyricist": "TEXT", "composer": "TCOM", "arranger": "TPE4"}
_MP4 = {"title": "©nam", "artist": "©ART", "composer": "©wrt"}
_LRC_TIME = re.compile(r"^(?:\[\d+:\d+(?:[.:]\d+)?\])+")
_LRC_TAG = re.compile(r"^\[[a-z]+:.*\]$", re.I)


def read_tags(path: str | Path) -> tuple[dict[str, str], str]:
    """({"title": …, "artist": …, "lyricist": …, "composer": …, "arranger": …}, 埋め込み歌詞)。読めなければ空"""
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags

    try:
        f = mutagen.File(str(path))
    except mutagen.MutagenError:
        return {}, ""
    tags = getattr(f, "tags", None)
    if tags is None:
        return {}, ""
    out: dict[str, str] = {}
    if isinstance(tags, ID3):
        for name, key in _ID3.items():
            if key in tags:
                out[name] = "、".join(str(v) for v in tags[key].text)
        lyrics = next((fr.text for fr in tags.getall("USLT") if fr.text.strip()), "")
    elif isinstance(tags, MP4Tags):
        for name, key in _MP4.items():
            out[name] = "、".join(tags.get(key, []))
        lyrics = "".join(tags.get("©lyr", []))
    else:  # Vorbis コメント（キーは大文字小文字を区別しない）
        for name in ("title", "artist", "lyricist", "composer", "arranger"):
            out[name] = "、".join(tags.get(name, []))
        lyrics = "".join(tags.get("lyrics", []) or tags.get("unsyncedlyrics", []))
    return {k: v.strip() for k, v in out.items() if v.strip()}, clean_lyrics(lyrics)


def clean_lyrics(text: str) -> str:
    """タグの歌詞を歌詞テキストの形にする（改行をそろえ、LRC の時刻・[ar:…] を落とす）"""
    lines = []
    for line in text.splitlines():
        if _LRC_TAG.match(line.strip()):
            continue
        lines.append(_LRC_TIME.sub("", line.strip()).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ファイル名の飾り：【MV】・[Official]・(Official Video) などと、先頭のトラック番号
_BRACKETS = re.compile(r"【[^】]*】|\[[^\]]*\]")
_NOISE = re.compile(
    r"[(（][^()（）]*(?:official|music|lyrics?|video|audio|mv|pv|full|hd|4k|歌詞|公式|フル)[^()（）]*[)）]", re.I
)
_TRACK_NO = re.compile(r"^\d{1,3}(?:\s*[.\-_]\s*|\s+)")
_QUOTED = re.compile(r"[「『](.+?)[」』]")
# 区切りと並び。"歌手 - 曲名"（前が歌手）、"曲名 / 歌手"（前が曲名）が多い
_SEPARATORS = ((" - ", "artist"), (" – ", "artist"), (" — ", "artist"), (" / ", "title"), ("／", "title"))


def parse_filename(stem: str) -> list[tuple[str, str]]:
    """ファイル名（拡張子なし）→ (曲名, 歌手) の候補。最初がいちばんありそうなもの。歌手が分からなければ ""。"""
    s = _NOISE.sub(" ", _BRACKETS.sub(" ", stem))
    s = _TRACK_NO.sub("", re.sub(r"\s+", " ", s).strip())
    m = _QUOTED.search(s)
    if m:  # 歌手「曲名」
        artist = (s[: m.start()] or s[m.end() :]).strip(" -/／")
        return [(m.group(1).strip(), artist)]
    for sep, first in _SEPARATORS:
        if sep in s:
            a, b = (x.strip() for x in s.split(sep, 1))
            return [(b, a), (a, b)] if first == "artist" else [(a, b), (b, a)]
    return [(s, "")]
