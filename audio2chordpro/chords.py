"""コード名・調の解析。コードの綴り（F#7/A# か Gb7/Bb か）は MIDI に書いてあるとおりにする
（tsumugi が前後の文脈から臨時記号を決めている）。書いていない音（Harte の度数のベース "/5" など）だけここで綴る"""

from __future__ import annotations

import re
from dataclasses import dataclass

LETTERS = "CDEFGAB"
NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# 主音からの半音数 → 音度（0=I … 6=VII）。非ダイアトニック音は慣用的な綴りにする
#   長調: ♭2, ♭3, #4, ♭6, ♭7   短調も同じ表を使う
DEGREE_OF = {0: 0, 1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 5, 9: 5, 10: 6, 11: 6}

# Harte 表記（AMT の出力）→ 日本の歌本でよく見る表記
QUALITY_MAP = {
    "": "",
    "maj": "",
    "min": "m",
    "M7": "M7",
    "maj7": "M7",
    "min7": "m7",
    "hdim7": "m7-5",
    "m7b5": "m7-5",
    "dim": "dim",
    "dim7": "dim7",
    "aug": "aug",
    "minmaj7": "mM7",
    "maj6": "6",
    "min6": "m6",
    "sus4(b7)": "7sus4",
    "maj9": "M9",
}
DEGREE_SEMITONES = {"1": 0, "2": 2, "3": 4, "4": 5, "5": 7, "6": 9, "7": 11, "9": 14}


def name_to_pc(name: str) -> int:
    """音名 → ピッチクラス（0=C）"""
    pc = NATURAL_PC[name[0].upper()]
    for a in name[1:]:
        pc += {"#": 1, "b": -1, "♯": 1, "♭": -1}.get(a, 0)
    return pc % 12


def _spell(letter: str, pc: int) -> str | None:
    acc = {0: "", 1: "#", 11: "b", 2: "##", 10: "bb"}.get((pc - NATURAL_PC[letter]) % 12)
    return None if acc is None else letter + acc


@dataclass
class Key:
    tonic: str  # 例 "Gb"
    minor: bool = False

    @property
    def pc(self) -> int:
        return name_to_pc(self.tonic)

    def __str__(self) -> str:
        return self.tonic + ("m" if self.minor else "")

    def spell(self, pc: int, simplify: bool = False) -> str:
        """調に合わせてピッチクラスを綴る。simplify=True なら E#/B#/Cb/Fb と重変化記号を避ける"""
        degree = DEGREE_OF[(pc - self.pc) % 12]
        letter = LETTERS[(LETTERS.index(self.tonic[0]) + degree) % 7]
        s = _spell(letter, pc)
        if s is None or len(s) > 2 or (simplify and s in ("E#", "B#", "Cb", "Fb")):
            flat_side = "b" in self.tonic or self.tonic == "F"
            s = (FLAT_NAMES if flat_side else SHARP_NAMES)[pc]
        return s


def parse_key(s: str) -> Key:
    """ "Db" / "F#m" → Key"""
    minor = s.endswith("m")
    return Key(s[:-1] if minor else s, minor)


SIMPLE_NAMES = {"E#": "F", "B#": "C", "Cb": "B", "Fb": "E"}


def simple_name(name: str) -> str:
    """E#/B#/Cb/Fb と重変化記号を、ふつうの綴りにする"""
    if name in SIMPLE_NAMES:
        return SIMPLE_NAMES[name]
    if len(name) > 2:  # "F##" "Bbb"
        return SHARP_NAMES[name_to_pc(name)] if "#" in name else FLAT_NAMES[name_to_pc(name)]
    return name


@dataclass
class ChordSym:
    root_pc: int | None  # None = N.C.
    quality: str = ""
    bass_pc: int | None = None
    root_name: str = ""  # MIDI に書いてある綴り（"F#"）
    bass_name: str = ""  # 同上。Harte の度数（"/5"）のときはルートから綴る

    def render(self, key: Key, simplify: bool = False) -> str:
        """表示するコード名。綴りは書いてあるとおり（無ければ調に合わせる）。simplify なら E#/B#/Cb/Fb などを避ける"""
        if self.root_pc is None:
            return "N.C."
        root = self.root_name or key.spell(self.root_pc)
        s = (simple_name(root) if simplify else root) + self.quality
        if self.bass_pc is not None and self.bass_pc != self.root_pc:
            bass = self.bass_name or key.spell(self.bass_pc)
            s += "/" + (simple_name(bass) if simplify else bass)
        return s


def parse_chord(label: str) -> ChordSym:
    """Harte 表記（"A:min7", "C:maj7/5", "Db/F"）や "Am7" などを解析する"""
    label = label.strip()
    if label.startswith("(") and label.endswith(")"):  # 省略可のコード表記 "(B/D#)"
        label = label[1:-1]
    if label in ("N", "X", "N.C.", ""):
        return ChordSym(None)
    bass = None
    if "/" in label:
        label, bass = label.split("/", 1)
    if ":" in label:
        root, qual = label.split(":", 1)
    else:
        m = re.match(r"^([A-G][#b]*)(.*)$", label)
        root, qual = m.group(1), m.group(2)
    root_pc = name_to_pc(root)
    bass_pc, bass_name = None, ""
    if bass:
        if bass[0] in LETTERS:
            bass_pc, bass_name = name_to_pc(bass), bass
        else:  # Harte の度数表記 "/b7" など。ルートの文字から度数ぶん進めた文字で綴る
            degree = bass.strip("#b")
            acc = bass.count("#") - bass.count("b")
            bass_pc = (root_pc + DEGREE_SEMITONES[degree] + acc) % 12
            letter = LETTERS[(LETTERS.index(root[0].upper()) + int(degree) - 1) % 7]
            bass_name = _spell(letter, bass_pc) or ""
    return ChordSym(root_pc, QUALITY_MAP.get(qual, qual), bass_pc, root, bass_name)
