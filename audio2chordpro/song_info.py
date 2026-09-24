"""曲のメタデータ → ChordPro の {title:} / {subtitle:} 行"""

from __future__ import annotations

from dataclasses import dataclass, fields

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
