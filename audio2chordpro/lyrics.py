"""歌詞の1行 → トークン（表層・読み・モーラ）と、モーラ → 表層の文字位置の対応

コードを「あるモーラの直前」に書くとき、そのモーラが表層のどこに当たるかを決める。
  * かな                  → 文字単位
  * 送り仮名つきの漢字    → 送り仮名で切り分ける（書き直す = 書|き|直|す）
  * 漢字の連続            → 1字ずつの読みで割れれば割る（学校 = 学|校）。割れなければ語全体をルビ表示にする
                            （言葉(こと[C]ば)）
  * 英単語・数字          → 音節の境目に比例配分（Happy = Hap|py）
  * 歌詞中のルビ「運命(さだめ)」は読みとして使い、表示も残す

読みは pyopenjtalk（辞書を強化した pyopenjtalk-plus を推奨）、英単語は alkana（英語→カナ辞書）、
1字ごとの漢字の読みは KANJIDIC2 由来の data/kanji_readings.json を使う。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SMALL = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
KANJI_RE = r"[㐀-鿿豈-﫿々〆ヵヶ]"
LATIN_CH = re.compile(r"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９'’]")
RUBY_RE = re.compile(rf"({KANJI_RE}+)[(（]([ぁ-ゖァ-ヺー]+)[)）]")
CHUNK_RE = re.compile(
    rf"(?P<ruby>{KANJI_RE}+[(（][ぁ-ゖァ-ヺー]+[)）])"
    r"|(?P<latin>[A-Za-zＡ-Ｚａ-ｚ]+(?:['’][A-Za-zａ-ｚ]+)?)"
    r"|(?P<num>[0-9０-９]+)"
    r"|(?P<sp>\s+)"
)
DIGIT_KANA = ["ゼロ", "イチ", "ニー", "サン", "ヨン", "ゴー", "ロク", "ナナ", "ハチ", "キュー"]
VOICED = {
    "カ": "ガ",
    "キ": "ギ",
    "ク": "グ",
    "ケ": "ゲ",
    "コ": "ゴ",
    "サ": "ザ",
    "シ": "ジ",
    "ス": "ズ",
    "セ": "ゼ",
    "ソ": "ゾ",
    "タ": "ダ",
    "チ": "ヂ",
    "ツ": "ヅ",
    "テ": "デ",
    "ト": "ド",
    "ハ": "バ",
    "ヒ": "ビ",
    "フ": "ブ",
    "ヘ": "ベ",
    "ホ": "ボ",
}
ON_2ND = set("ンイウツキクチッー")  # 2モーラの音読みの2拍目に来る音
# 熟字訓（1字ずつに読みを割れない語）
JUKUJIKUN = set(
    """今日 明日 昨日 一昨日 明後日 今年 去年 今朝 大人 一人 二人 時計 景色 眼鏡 上手 下手 部屋
真面目 素人 玄人 土産 紅葉 果物 雪崩 五月雨 七夕 息子 風邪 一日 二日 二十歳 相撲 浴衣 心地 田舎 迷子
八百屋 行方 大和 足袋 為替 竹刀 名残 最寄 木綿 眼差 海老 百合 梅雨 時雨 吹雪 陽炎 蜻蛉 河原 清水 芝生
三味線 硫黄 乙女 早乙女 小豆 意気地 息吹 白髪 神楽 数寄屋""".split()
)


def kata(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def split_morae(kana: str) -> list[str]:
    """かな → モーラ（拗音は前の字とまとめる。〜 は長音 ー とみなす）"""
    out: list[str] = []
    for c in kana:
        if c in SMALL and out:
            out[-1] += c
        elif c in "〜～":
            out.append("ー")
        else:
            out.append(c)
    return out


# ================================================================== データ構造
@dataclass
class Seg:
    """トークン内の、読みを対応づけられる最小単位（漢字1字、かなの連続、英単語など）"""

    surface: str
    reading: str  # カタカナ
    kind: str  # "kana" | "kanji" | "other"
    morae: list[str] = field(default_factory=list)
    char_pos: list[int] = field(default_factory=list)  # kana/other: 各モーラの開始文字位置（seg 内）
    run_id: int = -1  # 同じ漢字連続（語）から分割された seg は同じ id


@dataclass
class Token:
    surface: str
    reading: str = ""  # カタカナ。"" は発音なし（記号・空白）
    given_ruby: bool = False  # 歌詞中にルビが書かれていた
    ruby_text: str = ""  # そのルビ（ひらがな/カタカナそのまま）
    candidates: list[str] | None = None  # 読み候補（数字など）。[] は辞書にない英単語
    segs: list[Seg] = field(default_factory=list)

    @property
    def morae(self) -> list[str]:
        return [m for s in self.segs for m in s.morae]

    @property
    def is_space(self) -> bool:
        return not self.reading and self.surface.strip() == ""

    def set_reading(self, reading: str) -> None:
        self.reading = reading
        build_segs(self)


def line_morae(tokens: list[Token]) -> list[str]:
    return [m for t in tokens for m in t.morae]


# ================================================================== 読み
def _ojt():
    import pyopenjtalk

    return pyopenjtalk


@lru_cache(maxsize=1)
def _kanjidic() -> dict:
    try:
        return json.loads((Path(__file__).parent / "data" / "kanji_readings.json").read_text(encoding="utf-8"))
    except OSError:
        return {}


@lru_cache(maxsize=4096)
def single_kanji_readings(ch: str) -> tuple[str, ...]:
    """1字の読み候補（KANJIDIC の音訓 + pyopenjtalk の単独読み + 促音化・連濁・半濁音の変種）"""
    cands = set(_kanjidic().get(ch, "").split())
    try:
        for w in _ojt().run_frontend(ch):
            r = kata(w["read"].replace("’", ""))
            if r:
                cands.add(r)
    except Exception:
        pass
    ext = set()
    for r in cands:
        if len(r) >= 2 and r[-1] in "クツキチ":
            ext.add(r[:-1] + "ッ")  # 学校 ガク→ガッ
        if r[0] in VOICED:
            ext.add(VOICED[r[0]] + r[1:])  # 連濁
        if r[0] in "ハヒフヘホ":
            ext.add(chr(ord(r[0]) + 2) + r[1:])  # 半濁音 ハ→パ
    return tuple(sorted(cands | ext, key=len, reverse=True))


def split_kanji_run(run: str, reading: str) -> list[tuple[str, str]] | None:
    """漢字の連続を1字ずつの読みに割る（学校 = ガッ|コウ）。曖昧・失敗なら None。

    辞書の読みに一致 = 高得点、1モーラまたは音読みの形（2拍目が ン/イ/ウ/ツ/キ/ク/チ/ッ）= 低得点。
    最高得点の分割が1通りに決まり、辞書にない字が1字以下のときだけ採用する。"""
    n = len(run)
    if n == 1:
        return [(run, reading)]
    mor = split_morae(reading)
    M = len(mor)
    if len(set(run.replace("々", run[0]))) == 1 and M % n == 0:  # 人々・日々
        k = M // n
        return [(run[i], "".join(mor[i * k : (i + 1) * k])) for i in range(n)]
    if M < n or run in JUKUJIKUN:
        return None
    NEG = -1e9

    def score(i, j, L):
        ch = run[i - 1] if run[i] == "々" and i else run[i]
        part = "".join(mor[j : j + L])
        known = single_kanji_readings(ch)
        if part[0] in "ンッーウァィゥェォャュョ" and part not in known:
            return NEG  # 今日 = キョ|ウ のような誤分割を防ぐ
        if part in known:
            return 3.0
        if L == 1 or (L == 2 and mor[j + 1] in ON_2ND):
            return 0.3
        return 0.1 if L == 2 else NEG

    best = [[(NEG, 0)] * (M + 1) for _ in range(n + 1)]  # (得点, 同点の分割数): 先頭 i 字で j モーラ
    back = [[None] * (M + 1) for _ in range(n + 1)]
    best[0][0] = (0.0, 1)
    for i in range(n):
        for j in range(M):
            sc, cnt = best[i][j]
            if sc <= NEG / 2:
                continue
            for L in (1, 2, 3):
                if j + L > M:
                    break
                s2 = score(i, j, L)
                if s2 <= NEG / 2:
                    continue
                tot = sc + s2
                cur, ccnt = best[i + 1][j + L]
                if tot > cur + 1e-9:
                    best[i + 1][j + L] = (tot, cnt)
                    back[i + 1][j + L] = (j, L)
                elif abs(tot - cur) < 1e-9:
                    best[i + 1][j + L] = (cur, ccnt + cnt)
    sc, cnt = best[n][M]
    if sc <= NEG / 2 or cnt != 1 or sc < 3.0 * n - 2.75:
        return None
    out, i, j = [], n, M
    while i > 0:
        pj, L = back[i][j]
        out.append((run[i - 1], "".join(mor[pj : pj + L])))
        i, j = i - 1, pj
    return out[::-1]


def english_kana(word: str) -> str | None:
    """英単語 → カナ（alkana）。無ければ None"""
    try:
        import alkana
    except ImportError:
        return None
    w = unicodedata.normalize("NFKC", word).lower().replace("’", "'")
    k = alkana.get_kana(w)
    if k:
        return k
    for suf, add in (("'s", "ズ"), ("s", "ズ"), ("ed", "ド"), ("ing", "イング")):
        base = alkana.get_kana(w[: -len(suf)]) if w.endswith(suf) else None
        if base:
            if add == "ズ" and base[-1] in "トツ":
                return base[:-1] + "ツ"
            if add == "ズ" and base[-1] in "クプフ":
                return base + "ス"
            return base + add
    return None


# ================================================================== モーラ → 表層位置
def _syllable_starts(word: str) -> list[int]:
    """英単語の音節の開始位置（母音のまとまりで大まかに）。数字は1字ずつ"""
    w = word.lower()
    if re.fullmatch(r"[0-9０-９]+", w):
        return list(range(len(w)))
    groups = [m.span() for m in re.finditer(r"[aeiouy]+", w)]
    if len(groups) > 1 and w.endswith("e") and groups[-1] == (len(w) - 1, len(w)) and w[-2] not in "aeiouy":
        groups = groups[:-1]  # 語末のサイレント e
    starts = [0]
    for (_, a1), (b0, _) in zip(groups, groups[1:]):
        starts.append(a1 if b0 - a1 <= 1 else a1 + 1)
    return sorted(set(starts))


def build_segs(tok: Token) -> None:
    """トークンの読みを表層に対応づけて segs を作る"""
    s, r = tok.surface, tok.reading
    if not r:
        tok.segs = []
        return
    if tok.given_ruby:
        tok.segs = [Seg(s, r, "kanji", split_morae(r))]
        return
    if LATIN_CH.search(s) and not re.search(KANJI_RE, s):
        mor = split_morae(r)
        starts = _syllable_starts(s)
        pos, last = [], 0
        for k in range(len(mor)):
            target = k / len(mor) * len(s)
            last = max(min(starts, key=lambda x: (abs(x - target), x)), last)
            pos.append(last)
        tok.segs = [Seg(s, r, "other", mor, pos)]
        return
    # 送り仮名で漢字部分と読みを切り分ける
    parts = re.findall(rf"{KANJI_RE}+|.", s)
    pat = "".join("(.+?)" if re.match(KANJI_RE, p) else "(" + re.escape(kata(p)) + ")" for p in parts)
    m = re.fullmatch(pat, kata(r))
    if not m:
        seg = Seg(s, r, "kanji" if re.search(KANJI_RE, s) else "kana", split_morae(r))
        if seg.kind == "kana":  # 表層のかなと読みが食い違う → 比例配分
            seg.char_pos = [round(k * len(s) / len(seg.morae)) for k in range(len(seg.morae))]
        tok.segs = [seg]
        return
    segs: list[Seg] = []
    for ri, (p, rd) in enumerate(zip(parts, m.groups())):
        if re.match(KANJI_RE, p):
            for ps, pr in split_kanji_run(p, rd) or [(p, rd)]:
                segs.append(Seg(ps, pr, "kanji", split_morae(pr), run_id=ri))
        elif segs and segs[-1].kind == "kana":
            segs[-1].surface += p
            segs[-1].reading += rd
        else:
            segs.append(Seg(p, rd, "kana"))
    for sg in segs:
        if sg.kind != "kana":
            continue
        sm = split_morae(sg.surface)
        sg.morae = split_morae(sg.reading)
        if len(sm) == len(sg.morae):
            pos, acc = [], 0
            for x in sm:
                pos.append(acc)
                acc += len(x)
            sg.char_pos = pos
        else:
            sg.char_pos = [round(k * len(sg.surface) / len(sg.morae)) for k in range(len(sg.morae))]
    tok.segs = segs


# ================================================================== トークン化
def tokenize(line: str) -> list[Token]:
    """歌詞1行をトークン化する。

    * 漢字の直後の括弧のかな「漢字(かな)」はルビ（読み）とみなす。ただし漢字数に対してかなが長すぎれば
      掛け声・合いの手の括弧とみなしてルビにしない
    * 英単語は alkana の読み（辞書に無ければ candidates = [] で「要確認」の印）
    * 数字は「数として読む／1字ずつ読む」の2つの読み候補を持たせる（どちらかは音響で選ぶ）"""
    toks: list[Token] = []
    pos = 0
    for m in list(CHUNK_RE.finditer(line)) + [None]:
        toks.extend(_tokenize_plain(line[pos : m.start() if m else len(line)]))
        if m is None:
            break
        pos = m.end()
        g = m.group(0)
        if m.lastgroup == "ruby":
            r = RUBY_RE.fullmatch(g)
            if len(split_morae(r.group(2))) > 2 * len(r.group(1)) + 1:
                toks.extend(_tokenize_plain(r.group(1)))
                toks.extend(_tokenize_plain(g[len(r.group(1)) :]))
            else:
                toks.append(Token(r.group(1), kata(r.group(2)), given_ruby=True, ruby_text=r.group(2)))
        elif m.lastgroup == "sp":
            toks.extend(Token(c) for c in g)
        elif m.lastgroup == "latin":
            k = english_kana(g)
            t = Token(g, k or "".join(kata(w["read"]) for w in _ojt().run_frontend(g) if w["mora_size"]))
            if not k:
                t.candidates = []
            elif k.endswith("ング"):  # BANG は「バン」と歌うことが多い（どちらかは音響で選ぶ）
                t.candidates = [k, k[:-1]]
            toks.append(t)
        else:
            digits = unicodedata.normalize("NFKC", g)
            as_number = "".join(kata(w["read"].replace("’", "")) for w in _ojt().run_frontend(digits) if w["mora_size"])
            by_digit = "".join(DIGIT_KANA[int(c)] for c in digits)
            t = Token(g, as_number)
            t.candidates = [as_number, by_digit] if as_number != by_digit else [as_number]
            toks.append(t)
    for t in toks:
        build_segs(t)
    return toks


def _tokenize_plain(text: str) -> list[Token]:
    if not text:
        return []
    words = _ojt().run_frontend(text) if text.strip() else []
    out: list[Token] = []
    norm = lambda x: unicodedata.normalize("NFKC", x).replace("’", "'").replace("‘", "'")  # noqa: E731
    i = 0
    for w in words:
        ws = w["string"]
        # 元のテキストで、この語に対応する範囲を探す（pyopenjtalk は空白を落とすことがある）
        while i < len(text) and text[i].isspace() and not norm(ws).startswith(norm(text[i])):
            out.append(Token(text[i]))
            i += 1
        j, acc = i, ""
        while j < len(text) and len(norm(acc)) < len(norm(ws)):
            acc += text[j]
            j += 1
        matched = norm(acc) == norm(ws)
        surface = text[i:j] if matched else ws
        i = j if matched else i + len(ws)
        read = kata(w["read"].replace("’", "")) if w.get("mora_size", 0) > 0 else ""
        if read and not re.search(r"[ァ-ヺー]", read):
            read = ""
        out.append(Token(surface, read))
    while i < len(text):
        out.append(Token(text[i]))
        i += 1
    return _merge_latin(out)


def _merge_latin(toks: list[Token]) -> list[Token]:
    """pyopenjtalk が英単語を1文字ずつに割ったもの（ｍ｜ｅ）を1語に戻し、英語辞書の読みを当てる"""
    out: list[Token] = []
    latin: set[int] = set()
    for t in toks:
        is_lat = bool(t.surface) and all(LATIN_CH.match(c) for c in t.surface)
        if is_lat and out and id(out[-1]) in latin:
            out[-1].surface += t.surface
            out[-1].reading += t.reading
            continue
        if is_lat:
            latin.add(id(t))
        out.append(t)
    for t in out:
        if id(t) in latin and re.search(r"[A-Za-zＡ-Ｚａ-ｚ]", t.surface):
            k = english_kana(t.surface)
            if k:
                t.reading = k
    return out
