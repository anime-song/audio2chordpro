"""拍タイムライン（コード・調）+ 歌詞のアライメント → ChordPro テキスト

配置の規則（すべて拍単位）
  * モーラの開始を拍に直し、CTC の系統的なずれを補正 → 歌メロのノートへ DP で対応づけ → 8分グリッドに寄せる
  * コードは ±8分以内で最も近い「音節の頭」の直前に書く（ー・ッ・ン の前には書かない。同じ距離なら前 = 食い側）
  * どのモーラからも遠ければ（伸ばしの途中）、直前のモーラの後ろ
  * 行と行の間のコードは、次の行の最初のモーラまで 2.5 拍以内なら次の行頭、それより前なら前の行の末尾
  * 歌っていない小節が2小節以上続いたら間奏としてグリッド（[C]---- ----|、- = 8分）で書く
  * 行末の後ろにコードが2つ以上続く／1.5小節以上離れるときもグリッドにし、最後の音節と同じ小節のコードは
    行末をダッシュで閉じて書く（歌詞-[X]- ----|）
  * 行の途中でも、音節と次の音節の間にコードが2つ以上続く／1.5小節以上離れるときは、間をダッシュと小節線で埋める
    （歌詞--[X]- [Y]---|---- [Z]---歌詞。丸ごと空く小節はグリッドの行にして改行する）
  * 漢字の読みの途中に来たコードは、語全体をルビにしてその中に書く（言葉(こと[C]ば)）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .align import Alignment
from .lyrics import Token, hira, line_morae, split_morae
from .timeline import Note, Timeline

log = logging.getLogger(__name__)


@dataclass
class RenderOptions:
    simplify: bool = False  # E#/B#/Cb/Fb を F/C/B/E と綴る
    notation: str = "name"  # コードの書き方 "name"（コード名）| "degree"（ディグリー。例 VIm7、IV/V）
    head_outside: bool = False  # 行頭のコードを括弧の外に書く: [C](はい…  （既定は内側: ([C]はい…）
    tail_grid: bool = True  # 行末の後ろ・行の途中の音節の間に積み重なるコードを小節グリッドで書く
    min_gap_bars: int = 2  # 間奏とみなす、歌っていない小節の数
    tol: float = 0.5  # コードをモーラの直前に書ける距離 [拍]
    head_window: float = 2.5  # 行間のコードを次の行頭に書く範囲 [拍]


# ================================================================== 歌詞行（拍単位）
@dataclass
class LyricLine:
    text: str
    tokens: list[Token]
    on: np.ndarray  # 各モーラの開始 [拍]
    off: np.ndarray  # 各モーラの終了 [拍]
    conf: np.ndarray  # 各モーラの時刻が音響的に確かか
    paragraph_start: bool = False
    note_off: np.ndarray | None = None  # 各モーラが歌うノートの終わり [拍]（ノートに対応づけたとき。NaN = 不明）

    @property
    def start(self) -> float:
        return float(np.min(self.on))

    @property
    def end(self) -> float:
        return float(np.max(self.off))

    @property
    def last_on(self) -> float:
        return float(np.max(self.on))


def to_beats(
    alignment: Alignment,
    tl: Timeline,
    notes: list[Note] | None = None,
    snap: bool = True,
    grid: float = 0.5,
    grid_tol: float = 0.2,
) -> list[LyricLine]:
    """アライメント（秒）→ 拍単位の歌詞行。notes があれば系統的なずれを補正し、snap=True ならノートへ対応づける"""
    out: list[LyricLine] = []
    brk = False
    for L in alignment.lines:
        if not L.text.strip():
            brk = True
            continue
        if not L.morae:
            continue
        on = tl.sec2beat([m.start for m in L.morae], audio=True)
        off = tl.sec2beat([m.end for m in L.morae], audio=True)
        conf = np.array([m.confident for m in L.morae], bool)
        out.append(LyricLine(L.text, L.tokens, on, off, conf, brk))
        brk = False
    if notes and out:
        lag = _mora_lag(out, notes)
        if lag is not None:
            log.info("モーラ時刻 → メロディ発音の系統的なずれ %+.2f 拍を補正", lag)
            for L in out:
                L.on, L.off = L.on + lag, L.off + lag
        if snap:
            snap_to_notes(out, notes)
    if grid:  # 歌の発音はほぼ8分グリッドに乗る（食い = ちょうど8分前、の判定が安定する）
        for L in out:
            q = np.round(L.on / grid) * grid
            L.on = np.maximum.accumulate(np.where(np.abs(L.on - q) <= grid_tol, q, L.on))
    return out


def _mora_lag(lines: list[LyricLine], notes: list[Note], win: float = 0.75) -> float | None:
    """確信ありモーラの開始と最寄りのメロディ発音の差の中央値 [拍]"""
    nts = np.array([n.on for n in notes])
    d = []
    for L in lines:
        for b in L.on[L.conf]:
            j = np.searchsorted(nts, b)
            near = [nts[k] - b for k in (j - 1, j) if 0 <= k < len(nts) and abs(nts[k] - b) <= win]
            if near:
                d.append(min(near, key=abs))
    return float(np.median(d)) if len(d) >= 20 else None


def snap_to_notes(
    lines: list[LyricLine],
    notes: list[Note],
    w_dev: float = 1.0,
    skip_note: float = 0.6,
    melisma: float = 0.3,
    pitch_change: float = 0.4,
    rest_in_word: float = 0.5,
    rest_gap: float = 0.5,
    band: float = 6.0,
) -> None:
    """推定ノートとモーラを、脱落を許しつつ開始時刻の差が最小になるよう DP で対応づけ、モーラの開始をノートに寄せる。
      * ノートの脱落は高め（ノートはなるべくどれかのモーラが歌う）
      * 1モーラが複数ノートにまたがる（メリスマ）とき melisma、さらに音高が変われば pitch_change
      * 同じ語の中の連続モーラの間に rest_gap 拍以上の休符があれば rest_in_word
      * モーラの脱落は ッ が安く、ー・ン が中くらい、それ以外は高い
    D[i, j] = モーラ 1..i まで処理し、最後に使ったノートが j のときの最小コスト"""
    mor, conf, word, kana = [], [], [], []
    wid = 0
    for L in lines:
        for t in L.tokens:
            wid += 1
            for m in t.morae:
                kana.append(m)
                word.append(wid)
        wid += 1
        mor += list(L.on)
        conf += list(L.conf)
    mor = np.array(mor)
    M, N = len(mor), len(notes)
    if not M or not N:
        return
    on = np.array([n.on for n in notes])
    off = np.array([n.off for n in notes])
    pit = np.array([n.pitch for n in notes])
    skip_m = np.array([0.1 if k == "ッ" else 0.4 if k in ("ー", "ン") else 1.0 for k in kana])
    wdev = np.where(np.array(conf), w_dev, 0.3 * w_dev)  # 補間したモーラの時刻はあまり信用しない
    INF = 1e9
    D = np.full((M + 1, N + 1), INF)
    K = np.zeros((M + 1, N + 1), np.int8)  # 0 = モーラ脱落 / 1 = ノート j で歌い始め / 2 = メリスマ
    P = np.zeros((M + 1, N + 1), np.int32)  # 種類1のときの直前ノート
    S = np.full((M + 1, N + 1), -1, np.int32)  # モーラが歌い始めたノート
    D[0, :] = np.arange(N + 1) * skip_note
    for i in range(1, M + 1):
        D[i] = D[i - 1] + skip_m[i - 1]
        K[i] = 0
        lo = max(1, int(np.searchsorted(on, mor[i - 1] - band)))
        hi = min(N, int(np.searchsorted(on, mor[i - 1] + band)))
        for j in range(lo, hi + 1):
            dev = wdev[i - 1] * abs(mor[i - 1] - on[j - 1])
            for jp in range(max(0, j - 4), j):
                c = D[i - 1, jp] + dev + skip_note * (j - jp - 1)
                if jp and i > 1 and word[i - 1] == word[i - 2] and on[j - 1] - off[jp - 1] >= rest_gap:
                    c += rest_in_word
                if c < D[i, j]:
                    D[i, j], K[i, j], P[i, j], S[i, j] = c, 1, jp, j
            if j > 1 and S[i, j - 1] >= 0 and K[i, j - 1] != 0:
                c = D[i, j - 1] + melisma + (pitch_change if pit[j - 1] != pit[j - 2] else 0.0)
                if c < D[i, j]:
                    D[i, j], K[i, j], S[i, j] = c, 2, S[i, j - 1]
    i, j = M, int(np.argmin(D[M] + (N - np.arange(N + 1)) * skip_note))
    snapped = mor.copy()
    assigned = np.zeros(M, bool)
    last_note = np.full(M, -1)  # モーラが歌う最後のノート（メリスマなら最後の音）
    while i > 0:
        if K[i, j] != 0 and last_note[i - 1] < 0:
            last_note[i - 1] = j - 1
        if K[i, j] == 0:
            i -= 1
        elif K[i, j] == 2:
            j -= 1
        else:
            snapped[i - 1] = on[S[i, j] - 1]
            assigned[i - 1] = True
            i, j = i - 1, int(P[i, j])
    if assigned.any():  # 対応のないモーラは前後の対応済みモーラのずれを補間して動かす
        idx = np.arange(M)
        ok = idx[assigned]
        snapped = np.where(assigned, snapped, mor + np.interp(idx, ok, snapped[ok] - mor[ok]))
    note_off = np.where(last_note >= 0, off[np.maximum(last_note, 0)], np.nan)
    k = 0
    for L in lines:
        L.on = np.maximum.accumulate(snapped[k : k + len(L.on)])
        L.note_off = note_off[k : k + len(L.on)]
        k += len(L.on)


# ================================================================== 行内の配置
@dataclass
class Placement:
    before: dict = field(default_factory=dict)  # モーラ番号 -> [コード]
    after: dict = field(default_factory=dict)  # モーラ番号 -> [コード]
    gap: dict = field(default_factory=dict)  # モーラ番号 -> その後ろに書く「-」と小節線（render_gap）


def syllable_heads(morae: list[str]) -> np.ndarray:
    """音節の頭になるモーラか（ー・ッ・ン は前の音節の続き）"""
    return np.array([k == 0 or m not in ("ー", "ッ", "ン") for k, m in enumerate(morae)], bool)


def place_in_line(line: LyricLine, beat: float, label: str, pl: Placement, tol: float = 0.5) -> None:
    on = line.on
    d = np.abs(on - beat)
    cand = np.where((d <= tol + 1e-6) & syllable_heads(line_morae(line.tokens)))[0]
    if len(cand):
        k = int(cand[np.argmin(d[cand] + 1e-9 * cand)])  # 最も近い音節の頭（同じ距離なら前）
        pl.before.setdefault(k, []).append(label)
    elif beat < on.min():
        pl.before.setdefault(int(np.argmin(on)), []).append(label)
    else:
        started = np.where(on <= beat)[0]  # 伸ばしの途中：beat より前に始まった最後のモーラの後ろ
        pl.after.setdefault(int(started[-1]) if len(started) else 0, []).append(label)


def _units(line: LyricLine) -> list[tuple[Token, list, int | None]]:
    """(トークン, 続けて書く seg 列, 最初のモーラ番号)。同じ語の中で続く漢字はまとめる（ルビの単位）"""
    units = []
    g = 0
    for t in line.tokens:
        if not t.segs:
            units.append((t, [], None))
            continue
        groups = []
        for s in t.segs:
            if groups and s.kind == "kanji" and groups[-1][0].kind == "kanji" and s.run_id == groups[-1][0].run_id:
                groups[-1].append(s)
            else:
                groups.append([s])
        for segs in groups:
            units.append((t, segs, g))
            g += sum(len(x.morae) for x in segs)
    return units


def render_line(line: LyricLine, pl: Placement, head_outside: bool = False) -> str:
    """配置したコードを歌詞の表層に差し込む。
    * 漢字の読みの途中に来たコードは語全体をルビにする（字の境目なら 学[C]校 のように分けて書く）
    * 伸ばしの途中のコードと音節の間の「-」は、後ろの記号（！、」など）の後・空白の前に書く"""
    out: list[str] = []
    pending = ""
    ch = lambda labs: "".join(f"[{x}]" for x in labs)  # noqa: E731
    before = {k: list(v) for k, v in pl.before.items()}
    after = pl.after
    head = before.pop(0, []) if head_outside else []

    def flush():
        nonlocal pending
        if pending:
            out.append(pending)
            pending = ""

    for t, segs, g0 in _units(line):
        if not segs:  # 記号・空白
            if t.is_space:
                flush()
            out.append(t.surface)
            continue
        flush()
        if segs[0].kind == "kanji":
            ids, bounds, k = [], set(), g0
            for s in segs:
                bounds.add(k)
                ids += list(range(k, k + len(s.morae)))
                k += len(s.morae)
            need_ruby = (
                t.given_ruby
                or any(i not in bounds for i in ids[1:] if before.get(i))
                or any(i + 1 not in bounds for i in ids[:-1] if after.get(i))
            )
            out.append(ch(before.get(ids[0], [])))
            if need_ruby:
                if t.given_ruby and t.ruby_text and len(split_morae(t.ruby_text)) == len(ids):
                    shown = split_morae(t.ruby_text)
                else:
                    shown = [hira(m) for s in segs for m in s.morae]
                ruby = ""
                for n, (i, m) in enumerate(zip(ids, shown)):
                    if n:
                        ruby += ch(before.get(i, []))
                    ruby += m
                    if n < len(ids) - 1:
                        ruby += ch(after.get(i, []))
                out.append("".join(s.surface for s in segs) + f"({ruby})")
            else:
                k = g0
                for n, s in enumerate(segs):
                    if n:
                        out.append(ch(after.get(k - 1, [])) + ch(before.get(k, [])))
                    out.append(s.surface)
                    k += len(s.morae)
            pending += ch(after.get(ids[-1], [])) + pl.gap.get(ids[-1], "")
        else:
            s = segs[0]
            n = len(s.morae)
            pos = s.char_pos or [0] * n
            for k in range(n):
                i = g0 + k
                b = pos[k + 1] if k + 1 < n else len(s.surface)
                if before.get(i):
                    flush()
                    out.append(ch(before[i]))
                out.append(s.surface[pos[k] : b])
                if after.get(i) or pl.gap.get(i):
                    pending += ch(after.get(i, [])) + pl.gap.get(i, "")
                    if k + 1 < n:
                        flush()
    flush()
    return ch(head) + "".join(out)


# ================================================================== グリッド
def render_grid(
    tl: Timeline, bar0: int, bar1: int, simplify: bool = False, bars_per_line: int = 4, restate: bool = True
) -> list[str]:
    """小節 bar0..bar1-1 を「[C]---- ----|[F]---- [G]----|」の形（- = 8分）で。拍子が変わる小節には (3/4) を前置。
    restate: 行頭で、前から鳴っているコードを書き直す"""
    lines = []
    for L0 in range(bar0, bar1, bars_per_line):
        s = ""
        for bar in range(L0, min(L0 + bars_per_line, bar1)):
            b0, n = tl.bar_start(bar), tl.bar_len(bar)
            slots = n * 2
            if bar > 0 and n != tl.bar_len(bar - 1):
                s += f"({n}/4)"
            evs = {int(round((e.beat - b0) * 2)): e for e in tl.chords if b0 - 1e-6 <= e.beat < b0 + n - 1e-6}
            if restate and bar == L0 and 0 not in evs and tl.chord_at(b0) is not None:
                evs[0] = tl.chord_at(b0)  # 行頭では鳴っているコードを書き直す
            for k in range(slots):
                if k == slots // 2:
                    s += " "
                if k in evs:
                    s += f"[{tl.chord_label(evs[k], simplify)}]"
                s += "-"
            s += "|"
        lines.append(s)
    return lines


def _dashes(tl: Timeline, bar: int, k0: int, k1: int, chords: list[tuple[float, str]]) -> str:
    """小節 bar の8分 k0..k1-1 を「-」で埋め、コードをその位置（範囲の外なら端）に書く。小節の半分に空白"""
    b0, slots = tl.bar_start(bar), tl.bar_len(bar) * 2
    at: dict[int, list[str]] = {}
    for beat, lab in chords:
        at.setdefault(min(max(int(round((beat - b0) * 2)), k0), k1 - 1), []).append(lab)
    s = ""
    for k in range(k0, k1):
        if k == slots // 2 and k != k0:
            s += " "
        s += "".join(f"[{x}]" for x in at.get(k, [])) + "-"
    return s


def render_tail(tl: Timeline, last_on: float, chords: list[tuple[float, str]]) -> str:
    """最後の音節の後ろから小節の終わりまでを「-」で埋め、コードをその位置に書いて「|」で閉じる"""
    bar = tl.bar_index(last_on)
    first = int(round((last_on - tl.bar_start(bar)) * 2)) + 1
    return _dashes(tl, bar, first, tl.bar_len(bar) * 2, chords) + "|"


def starts_bar(tl: Timeline, beat: float) -> bool:
    """拍が小節の頭（±16分）か"""
    return abs(beat - tl.bar_start(tl.bar_index(beat + 0.25))) < 0.25


def gap_as_grid(tl: Timeline, a: float, b: float) -> bool:
    """行の途中の空きを、改行して音節の小節からグリッドで書くか（音節が小節の頭で、次の音節が次の小節以降）"""
    return tl.bar_index(b) > tl.bar_index(a + 0.25) and starts_bar(tl, a)


def render_gap(tl: Timeline, a: float, b: float, chords: list[tuple[float, str]], simplify: bool = False) -> str:
    """行の途中で、拍 a に始まる音節から拍 b に始まる次の音節までを「-」と小節線で埋める。
    * 音節が小節の頭なら、改行してその小節からグリッドにする（音節のコードはグリッドの頭と重なるので括弧付きで書く）
    * そうでなければ音節の後ろから「-」で埋め、丸ごと空く小節はグリッドの行にして改行する
    * 次の音節の前はその小節の頭から「-」で埋める（グリッドの最後の行が4小節に満たなければその行に続ける）"""
    bar_b = tl.bar_index(b)
    last = int(round((b - tl.bar_start(bar_b)) * 2))
    in_b = [c for c in chords if tl.bar_index(c[0]) >= bar_b]

    def lead(restate: bool) -> str:
        b0, ev = tl.bar_start(bar_b), tl.chord_at(tl.bar_start(bar_b))
        if restate and last and ev is not None and ev.beat < b0 - 1e-6:  # 行頭では前の小節から鳴っているコードを書き直す
            return _dashes(tl, bar_b, 0, last, [(b0, tl.chord_label(ev, simplify))] + in_b)
        return _dashes(tl, bar_b, 0, last, in_b)

    if gap_as_grid(tl, a, b):
        bar_a = tl.bar_index(a + 0.25)
        grid = render_grid(tl, bar_a, bar_b, simplify)
        if not last:
            return "\n" + "\n".join(grid) + "\n"
        if (bar_b - bar_a) % 4:
            return "\n" + "\n".join(grid[:-1] + [grid[-1] + lead(False)])
        return "\n" + "\n".join(grid) + "\n" + lead(True)
    bar_a = tl.bar_index(a)
    first = int(round((a - tl.bar_start(bar_a)) * 2)) + 1
    if bar_a == bar_b:
        return _dashes(tl, bar_a, first, last, chords)
    s = _dashes(tl, bar_a, first, tl.bar_len(bar_a) * 2, [c for c in chords if tl.bar_index(c[0]) <= bar_a]) + "|"
    if bar_b > bar_a + 1:  # 丸ごと空く小節（コードは render_grid が書く）
        return s + "\n" + "\n".join(render_grid(tl, bar_a + 1, bar_b, simplify)) + "\n" + lead(True)
    return s + lead(False)


def line_gaps(tl: Timeline, line: LyricLine, tol: float = 0.5) -> list[tuple[int, float, float]]:
    """行の途中で、音節と次の音節の間にコードが2つ以上続く／1.5小節以上離れる箇所。
    (前の音節の最後のモーラ番号, 前の音節の開始 [拍], 次の音節の開始 [拍])。漢字のルビの途中では切らない"""
    heads = np.where(syllable_heads(line_morae(line.tokens)))[0]
    inner = set()  # まとめて書く漢字の2モーラ目以降
    for _, segs, g0 in _units(line):
        if segs and segs[0].kind == "kanji":
            inner.update(range(g0 + 1, g0 + sum(len(x.morae) for x in segs)))
    beats = np.array([e.beat for e in tl.chords])
    out = []
    for h, h2 in zip(heads[:-1], heads[1:]):
        if h2 in inner:
            continue
        a, b = float(line.on[h]), float(line.on[h2])
        mid = beats[(beats > a + tol + 1e-6) & (beats < b - tol - 1e-6)]
        if len(mid) >= 2 or (len(mid) and mid[-1] - a >= 1.5 * tl.beats_per_bar):
            out.append((int(h2) - 1, a, b))
    return out


def _runs(mask: np.ndarray, breaks: set[int]) -> list[tuple[int, int]]:
    """mask が True の小節の連続区間（breaks の小節で区切る）"""
    out, k, n = [], 0, len(mask)
    while k < n:
        if not mask[k]:
            k += 1
            continue
        j = k + 1
        while j < n and mask[j] and j not in breaks:
            j += 1
        out.append((k, j))
        k = j
    return out


def _interludes(tl: Timeline, lines: list[LyricLine], n_bars: int, opt: RenderOptions) -> list[tuple[int, int]]:
    """間奏の小節の区間"""
    bpb = tl.beats_per_bar
    # 歌っている小節（行の範囲ではなくモーラの発声区間で。伸ばしは1小節、行末は2小節まで）
    sung = np.zeros(n_bars, bool)
    for L in lines:
        n = len(L.on)
        for k in range(n):
            end = min(L.off[k], L.on[k] + (2 if k == n - 1 else 1) * bpb)
            a = tl.bar_index(L.on[k])
            b = tl.bar_index(max(end, L.on[k] + 1e-3) - 1e-6) + 1
            sung[max(a, 0) : min(b, n_bars)] = True
    key_bars = {tl.bar_index(b + 1e-6) for b, _ in tl.keys[1:]}
    runs = []
    for a, b in _runs(~sung, key_bars):  # 間奏：歌っていない小節が min_gap_bars 以上（曲頭・曲末は1小節でも）
        if b - a >= opt.min_gap_bars or a == 0 or b == n_bars:
            runs.append((a, b))
    return runs


def _grid_bars(
    tl: Timeline, lines: list[LyricLine], n_bars: int, opt: RenderOptions, runs: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """グリッドで書く小節の区間（間奏 runs と、行末の後ろ）"""
    bpb = tl.beats_per_bar
    key_bars = {tl.bar_index(b + 1e-6) for b, _ in tl.keys[1:]}
    if not opt.tail_grid:
        return runs
    # 行末の後ろ（最後のモーラの次の小節 〜 次の行が始まる小節の手前）
    mask = np.zeros(n_bars, bool)
    for a, b in runs:
        mask[a:b] = True
    order = sorted(lines, key=lambda L: L.start)
    for n, L in enumerate(order):
        a = tl.bar_index(L.last_on) + 1
        b = tl.bar_index(order[n + 1].start) if n + 1 < len(order) else n_bars
        if b <= a or mask[a:b].all():
            continue
        tail = [e for e in tl.chords if tl.bar_start(a) - 1e-6 <= e.beat < tl.bar_start(b) - 1e-6]
        if len(tail) >= 2 or (tail and tail[-1].beat - L.last_on >= 1.5 * bpb):
            mask[a : min(b, n_bars)] = True
    return _runs(mask, key_bars)


# ================================================================== 全体
def build_chordpro(
    tl: Timeline, lines: list[LyricLine], header: list[str] | None = None, opt: RenderOptions | None = None
) -> str:
    """ChordPro のテキストを作る。header はタイトルなどのディレクティブ行（SongInfo.directives()）"""
    opt = opt or RenderOptions()
    bpb, tol = tl.beats_per_bar, opt.tol
    last_beat = max([e.beat for e in tl.chords] + [L.end for L in lines] + [0.0])
    n_bars = tl.bar_index(last_beat) + 1
    inter = _interludes(tl, lines, n_bars, opt)
    gaps = {}
    if opt.tail_grid:
        # 間奏をまたぐ箇所は、行の途中ではなく間奏のグリッドとして書く（アライメントが崩れて行が間奏をまたぐことがある）
        for i, L in enumerate(lines):
            gaps[i] = [
                (k, a, b)
                for k, a, b in line_gaps(tl, L, tol)
                if not any(max(ra, tl.bar_index(a)) < min(rb, tl.bar_index(b) + 1) for ra, rb in inter)
            ]
    runs = _grid_bars(tl, lines, n_bars, opt, inter)
    # 最後の音節が小節の頭で、その後ろに2小節以上の歌わない区間が続く行：グリッドを最後の音節の小節から始め、
    # 音節のコードは括弧付きで書く（小節の途中からグリッドを書くと位置を追いにくいので）
    paren_tail = {}
    key_bars = {tl.bar_index(b + 1e-6) for b, _ in tl.keys[1:]}
    for i, L in enumerate(lines if opt.tail_grid else []):
        h = int(np.where(syllable_heads(line_morae(L.tokens)))[0][-1])
        B = tl.bar_index(L.on[h] + 0.25)
        run = [r for r in runs if r[0] == B + 1 and r[1] - r[0] >= 2]
        alone = not any(M is not L and tl.bar_index(M.start) == B for M in lines)  # 同じ小節で次の行が始まらない
        if run and alone and starts_bar(tl, L.on[h]) and tl.bar_index(L.last_on) <= B and B + 1 not in key_bars:
            runs[runs.index(run[0])] = (B, run[0][1])
            paren_tail[i] = (h, B)
    spans = [(tl.bar_start(a), tl.bar_start(b)) for a, b in runs]
    all_on = np.concatenate([L.on for L in lines]) if lines else np.array([])

    def in_grid(beat: float) -> bool:
        for a, b in spans:
            if a <= beat < b:
                return True
            # グリッドの頭の8分前の食い（歌の音節と重ならないもの）はグリッドの最初の小節に含める
            if a - tol <= beat < a and not (np.abs(all_on - beat) <= 0.25).any():
                return True
        return False

    # 直後がグリッドの行：最後の音節の後 〜 その小節の終わりのコードは行末に「-[X]-|」の形で書く
    grid_starts = {a for a, _ in runs}
    tails = {i: [] for i, L in enumerate(lines) if tl.bar_index(L.last_on) + 1 in grid_starts}

    blocks = [("line", L.start, i) for i, L in enumerate(lines)] + [("grid", tl.bar_start(a), (a, b)) for a, b in runs]
    blocks.sort(key=lambda x: x[1])
    places = [Placement() for _ in lines]
    gap_chords = {(i, k): [] for i, g in gaps.items() for k, _, _ in g}
    for ev in tl.chords:
        if in_grid(ev.beat):
            continue
        lab = tl.chord_label(ev, opt.simplify)
        hit = [(i, k) for i, g in gaps.items() for k, a, b in g if a + tol + 1e-6 < ev.beat < b - tol - 1e-6]
        if hit:  # 行の途中の「-」の中に書く
            gap_chords[hit[0]].append((ev.beat, lab))
            continue
        prev = [bl for bl in blocks if bl[1] <= ev.beat + (tol if bl[0] == "line" else 0.0)]
        nxt = [bl for bl in blocks if bl[0] == "line" and bl[1] > ev.beat + tol]
        if not prev or prev[-1][0] == "grid":
            target = nxt[0][2] if nxt else None
        else:
            target = prev[-1][2]
            L = lines[target]
            near_next = nxt and lines[nxt[0][2]].start - ev.beat <= opt.head_window
            # 最後の音節を歌い終えた後（ノートが切れた後）に変わり、そのまま次の行が始まるコードは、食いではなく次の行の頭
            ended = (
                L.note_off is not None
                and ev.beat > L.last_on
                and L.note_off[-1] <= ev.beat + 1e-6
                and nxt
                and not any(ev.beat < e.beat < lines[nxt[0][2]].start - tol for e in tl.chords)
            )
            if near_next and (ev.beat > L.last_on + tol or ended):
                target = nxt[0][2]  # 前の行を歌い終えた後、次の行の直前 → 次の行頭
        if target is None:
            continue
        if target in tails and ev.beat > lines[target].last_on + tol:
            tails[target].append((ev.beat, lab))
        else:
            place_in_line(lines[target], ev.beat, lab, places[target], tol)

    for i, (h, B) in paren_tail.items():  # グリッドの頭と重なる、最後の音節のコード
        ev = tl.chord_at(tl.bar_start(B) + 1e-6)
        if ev is not None and ev.beat >= tl.bar_start(B) - tol:
            places[i].before.setdefault(h, []).append(f"({tl.chord_label(ev, opt.simplify)})")
    for (i, k), chords in gap_chords.items():
        (_, a, b) = next(g for g in gaps[i] if g[0] == k)
        places[i].gap[k] = render_gap(tl, a, b, chords, opt.simplify)
        if gap_as_grid(tl, a, b):  # 音節のコードはグリッドの頭と重なるので括弧付き
            heads = syllable_heads(line_morae(lines[i].tokens))
            h = max(j for j in range(k + 1) if heads[j])
            places[i].before[h] = [f"({x})" for x in places[i].before.get(h, [])]

    out = list(header or [])
    out.append(f"{{c:BPM={round(tl.bpm)}　{bpb}/4拍子　-:8分音符}}")
    out.append(f"{{key:{tl.keys[0][1]}}}")
    key_q = list(tl.keys[1:])
    merged = set()  # 前の行の後ろに続けて書いたグリッド
    for n, (kind, start, ref) in enumerate(blocks):
        while key_q and key_q[0][0] <= start + 1 + (bpb if kind == "line" else 0):
            out.append(f"{{key:{key_q.pop(0)[1]}}}")
        if kind == "grid":
            if n not in merged:
                out += render_grid(tl, *ref, simplify=opt.simplify)
            continue
        L = lines[ref]
        if L.paragraph_start and out and out[-1] != "":
            out.append("")
        text = render_line(L, places[ref], opt.head_outside)
        if tails.get(ref):
            text += render_tail(tl, L.last_on, tails[ref])
        # 行末のすぐ後ろの短いグリッド（行末と合わせて2小節まで、転調なし）は同じ行に続けて書く
        if ref in tails and n + 1 < len(blocks) and blocks[n + 1][0] == "grid":
            a, b = blocks[n + 1][2]
            width = (b - a) + (1 if tails[ref] else 0)
            short = width <= 2 and not places[ref].gap and not (key_q and key_q[0][0] < tl.bar_start(b))
            if a == tl.bar_index(L.last_on) + 1 and short:
                text += "".join(render_grid(tl, a, b, opt.simplify, bars_per_line=4, restate=False))
                merged.add(n + 1)
        out.append(text)
    return "\n".join(out) + "\n"
