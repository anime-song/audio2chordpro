"""歌詞と歌声の強制アライメント（モーラ単位の発声時刻）

CTC モデル（reazon-research/japanese-wav2vec2-base-rs35kh）の語彙は漢字かな混じり＋サブワードなので、
歌詞の読みの同じ区間を
  * かな1文字（ひらがな / カタカナ）
  * 語彙にあるかなのサブワード
  * 歌詞の表層の語（語彙にある場合。例: 漢字2字の熟語）
のどれで発声してもよいラティスにして、CTC の Viterbi で解く。
  * ー・ッ は空白（伸ばし・無音）で代用してよい。どのモーラも大きなペナルティで空白代用できる（聞き取れない箇所で全体が崩れない）
  * 行と行の間には wildcard を許し、歌詞にない発声を吸収する
  * メロディの発音時刻（と8分グリッド）に「モーラがここで始まりやすい」というボーナスを置く（事前分布）
  * 数字など読み候補のあるトークンは、候補ごとに解いてスコアの高い読みを採用する
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .lyrics import Token, build_segs, hira, kata, line_morae, tokenize
from .vocals import HOP

log = logging.getLogger(__name__)

VOWEL = {
    **{c: "あ" for c in "あかさたなはまやらわがざだばぱぁゃ"},
    **{c: "い" for c in "いきしちにひみりぎじぢびぴぃ"},
    **{c: "う" for c in "うくすつぬふむゆるぐずづぶぷぅゅゔ"},
    **{c: "え" for c in "えけせてねへめれげぜでべぺぇ"},
    **{c: "お" for c in "おこそとのほもよろをごぞどぼぽぉょ"},
}


# ================================================================== 結果
@dataclass
class Mora:
    kana: str
    start: float  # 秒
    end: float  # 秒
    confident: bool = True  # False = 前後から補間した時刻


@dataclass
class AlignedLine:
    text: str
    tokens: list[Token]
    morae: list[Mora] = field(default_factory=list)  # 発音のない行（記号だけ等）は空


@dataclass
class Alignment:
    """歌詞の各行とモーラの発声時刻。空行（段落区切り）は text="" の行として残す"""

    lines: list[AlignedLine]

    def to_dict(self) -> dict:
        return {
            "lines": [
                {
                    "text": L.text,
                    "tokens": [
                        {"surface": t.surface, "reading": t.reading, **({"ruby": t.ruby_text} if t.given_ruby else {})}
                        for t in L.tokens
                    ],
                    "morae": [
                        {"kana": m.kana, "start": m.start, "end": m.end, "confident": m.confident} for m in L.morae
                    ],
                }
                for L in self.lines
            ]
        }

    @classmethod
    def from_dict(cls, d: dict) -> Alignment:
        lines = []
        for L in d["lines"]:
            toks = [
                Token(t["surface"], t.get("reading", ""), given_ruby=bool(t.get("ruby")), ruby_text=t.get("ruby", ""))
                for t in L.get("tokens", [])
            ]
            for t in toks:
                build_segs(t)
            morae = [Mora(m["kana"], m["start"], m["end"], m.get("confident", True)) for m in L.get("morae", [])]
            lines.append(AlignedLine(L.get("text", ""), toks, morae))
        return cls(lines)


# ================================================================== ラティス
@dataclass
class Edge:
    a: int  # 開始文字位置（全曲をつないだひらがな列）
    b: int  # 終了文字位置
    tok: int  # 語彙 ID。-1 = 空白で代用
    pen: float = 0.0
    onset: bool = False  # 辺の開始 = モーラの発声開始（事前分布のボーナスを掛ける）


@dataclass
class Target:
    text: str  # 全曲の読み（ひらがな、行をつないだもの）
    mora_char: list[int]  # 各モーラの開始文字位置
    line_bounds: set[int]  # 行境界の文字位置（wildcard 可）
    line_mora: list[tuple[int, int]]  # 各行のモーラ範囲 [a, b)
    edges: list[Edge]


def build_target(
    lines_tokens: list[list[Token]], vocab: dict[str, int], drop_pen: float = 4.0, long_pen: float = 0.3
) -> Target:
    text, mora_char, bounds, line_mora, edges = "", [], {0}, [], []
    for toks in lines_tokens:
        m0, base = len(mora_char), len(text)
        for t in toks:
            tok_start = len(text)
            for s in t.segs:
                for m in s.morae:
                    mora_char.append(len(text))
                    text += hira(m)
            # 語（トークン）の表層が語彙にあれば、その読み全体の辺（漢字1字ずつの辺は作らない。評価で精度が下がった）
            if t.segs and len(t.surface) > 1 and t.surface in vocab:
                edges.append(Edge(tok_start, len(text), vocab[t.surface]))
        line = text[base:]
        for i, c in enumerate(line):
            for L in range(1, 6):  # かな1文字・かなのサブワード（ひらがな/カタカナ）
                if i + L > len(line):
                    break
                s = line[i : i + L]
                for v in {s, kata(s)}:
                    if v in vocab:
                        edges.append(Edge(base + i, base + i + L, vocab[v]))
            if c == "ー":
                if i and line[i - 1] in VOWEL and VOWEL[line[i - 1]] in vocab:
                    edges.append(Edge(base + i, base + i + 1, vocab[VOWEL[line[i - 1]]]))
                edges.append(Edge(base + i, base + i + 1, -1, long_pen))
            elif c == "っ":
                edges.append(Edge(base + i, base + i + 1, -1, long_pen))
            else:
                edges.append(Edge(base + i, base + i + 1, -1, drop_pen))
        bounds.add(len(text))
        line_mora.append((m0, len(mora_char)))
    uniq = {(e.a, e.b, e.tok): e for e in edges}
    starts = set(mora_char)
    for e in uniq.values():
        # 促音は発音の頭にならない。空白代用の辺にボーナスを与えると脱落ペナルティが相殺されるので除く
        e.onset = e.a in starts and text[e.a] != "っ" and e.tok >= 0
    return Target(text, mora_char, bounds, line_mora, list(uniq.values()))


def viterbi(E: np.ndarray, tg: Target, star_pen: float = 2.5, prior: np.ndarray | None = None, blank: int = 0):
    """ラティス上の CTC Viterbi。

    状態は境界 i の空白 B_i と、各辺 e の発声状態 X_e：
      B_i <- B_i, X_e(終点 i)
      X_e <- X_e, B_{始点}, X_e'(終点 = 始点, 別トークン)
    prior: (T,) モーラの発声開始がフレーム t に来ることへのボーナス（onset 辺に入るときに加算）
    戻り値: [(辺, 開始フレーム, 終了フレーム)], 総スコア"""
    T, N, K = E.shape[0], len(tg.text), len(tg.edges)
    ea = np.array([e.a for e in tg.edges])
    eb = np.array([e.b for e in tg.edges])
    etok = np.array([e.tok for e in tg.edges])
    epen = np.array([e.pen for e in tg.edges], np.float32)
    eon = np.array([e.onset for e in tg.edges], np.float32)
    prior = np.zeros(T, np.float32) if prior is None else prior
    by_end = [[] for _ in range(N + 1)]
    for k, e in enumerate(tg.edges):
        by_end[e.b].append(k)
    W = max(len(x) for x in by_end) or 1
    endpad = np.full((N + 1, W), K, np.int64)  # K = ダミー
    for i, ks in enumerate(by_end):
        endpad[i, : len(ks)] = ks
    tokpad = np.append(etok, -2)[endpad]
    tokpad = np.where(tokpad < 0, -3 - endpad, tokpad)  # 空白代用の辺は互いに別トークン扱い
    etok_u = np.where(etok < 0, -3 - np.arange(K), etok)
    tokidx = np.where(etok >= 0, etok, blank)
    star = E[:, 1:].max(1) - star_pen
    is_bound = np.zeros(N + 1, bool)
    is_bound[list(tg.line_bounds)] = True

    NEG = -1e30
    B = np.full(N + 1, NEG, np.float32)
    X = np.full(K, NEG, np.float32)
    bpB = np.zeros((T, N + 1), np.int32)  # -1 = 自己遷移、k = 辺 k から
    bpX = np.zeros((T, K), np.int32)  # -1 = 自己遷移、-2 = B から、k = 辺 k から
    B[0] = max(E[0, blank], star[0])
    X[ea == 0] = (E[0, tokidx] - epen + prior[0] * eon)[ea == 0]
    bpX[0] = -2
    ar = np.arange(N + 1)
    for t in range(1, T):
        et = E[t]
        V = np.append(X, NEG)[endpad]
        am = V.argmax(1)
        best1, arg1, tok1 = V[ar, am], endpad[ar, am], tokpad[ar, am]
        V2 = np.where(tokpad == tok1[:, None], NEG, V)
        am2 = V2.argmax(1)
        best2, arg2 = V2[ar, am2], endpad[ar, am2]
        eB = np.where(is_bound, max(et[blank], star[t]), et[blank])
        fromX = best1 > B
        nB = (np.where(fromX, best1, B) + eB).astype(np.float32)
        bpB[t] = np.where(fromX, arg1, -1)
        same = tok1[ea] == etok_u
        pX = np.where(same, best2[ea], best1[ea])
        pXarg = np.where(same, arg2[ea], arg1[ea])
        bon = prior[t] * eon
        cand = np.stack([X, B[ea] + bon, pX + bon])
        ch = cand.argmax(0)
        X = (cand[ch, np.arange(K)] + et[tokidx] - epen).astype(np.float32)
        bpX[t] = np.where(ch == 0, -1, np.where(ch == 1, -2, pXarg))
        B = nB
    # バックトラック
    fin = np.append(X[eb == N], B[N])
    fin_idx = np.append(np.where(eb == N)[0], -1)
    j = int(fin.argmax())
    score = float(fin[j])
    state = ("X", int(fin_idx[j])) if fin_idx[j] >= 0 else ("B", N)
    segs, cur_end, t = [], T - 1, T - 1
    while t >= 0:
        kind, idx = state
        if kind == "B":
            p = bpB[t, idx]
            if p != -1:
                state, cur_end = ("X", int(p)), t - 1
            t -= 1
            continue
        p = bpX[t, idx]
        if p == -1 and t > 0:
            t -= 1
            continue
        segs.append((idx, t, cur_end))
        if t == 0:
            break
        if p == -2:
            state = ("B", int(ea[idx]))
        else:
            state, cur_end = ("X", int(p)), t - 1
        t -= 1
    segs.reverse()
    return segs, score


def mora_times(tg: Target, segs, prior: np.ndarray | None = None, w: float = 1.0, max_step: float = 15.0):
    """各モーラの開始フレームと確信度。

    確信あり = 実トークンの辺の先頭にあたるモーラ（空白代用でもメロディ発音の上に置かれたもの）。
    それ以外（サブワード内部の2モーラ目以降、ー・ッ、聞き取れなかったモーラ）は行内の前後の確信ありモーラの間を等分する。"""
    char_t = np.full(len(tg.text) + 1, np.nan)
    char_ok = np.zeros(len(tg.text) + 1, bool)
    for k, s, _ in segs:
        ed = tg.edges[k]
        char_t[ed.a] = s
        char_ok[ed.a] = ed.tok >= 0 or (prior is not None and ed.onset and prior[s] >= 0.5 * w)
    t = char_t[tg.mora_char].copy()
    ok = char_ok[tg.mora_char].copy()
    for a, b in tg.line_mora:
        anchors = [i for i in range(a, b) if ok[i]]
        if not anchors:
            continue
        for i in range(a, b):
            if ok[i]:
                continue
            prv = [j for j in anchors if j < i]
            nxt = [j for j in anchors if j > i]
            if prv and nxt:
                p, q = prv[-1], nxt[0]
                t[i] = t[p] + (t[q] - t[p]) * (i - p) / (q - p)
            elif prv:
                p = prv[-1]
                step = t[i] - t[p] if not np.isnan(t[i]) and t[i] > t[p] else max_step
                t[i] = t[p] + min(max_step, step) * (i - p)
            else:
                t[i] = t[nxt[0]] - max_step * (nxt[0] - i)
    return t, ok


# ================================================================== メロディの事前分布
def onset_prior(
    T: int, note_times, grid_times, lag: float, w_note: float, w_grid: float, sigma: float = 0.04
) -> np.ndarray:
    """フレームごとの「ここでモーラが始まる」ボーナス。メロディの発音（+ CTC の系統的なずれ lag）に w_note、
    8分グリッドに w_grid のガウスの山を置き、大きいほうをとる"""
    t = np.arange(T) * HOP
    out = np.zeros(T, np.float32)
    for times, w in ((note_times, w_note), (grid_times, w_grid)):
        if w <= 0 or len(times) == 0:
            continue
        c = np.asarray(times) + lag
        j = np.clip(np.searchsorted(c, t), 1, len(c) - 1)
        d = np.minimum(np.abs(t - c[j - 1]), np.abs(t - c[j]))
        out = np.maximum(out, w * np.exp(-0.5 * (d / sigma) ** 2))
    return out


def estimate_lag(tg: Target, segs, note_times, win: float = 0.15) -> float | None:
    """CTC の発火位置 − 最寄りのメロディ発音 の中央値 [s]（CTC は子音の立ち上がりで発火するので負になりやすい）"""
    nt = np.asarray(note_times)
    d = []
    for k, s, _ in segs:
        e = tg.edges[k]
        if e.tok < 0 or not e.onset:
            continue
        x = s * HOP
        j = np.searchsorted(nt, x)
        near = [x - nt[i] for i in (j - 1, j) if 0 <= i < len(nt) and abs(x - nt[i]) <= win]
        if near:
            d.append(min(near, key=abs))
    return float(np.median(d)) if len(d) >= 20 else None


# ================================================================== 全体
def align_lyrics(
    lyrics: str,
    emissions: np.ndarray,
    vocab: dict[str, int],
    rms_db: np.ndarray,
    note_times=None,
    grid_times=None,
    w_note: float = 6.0,
    w_grid: float = 1.0,
    star_pen: float = 2.5,
) -> Alignment:
    """歌詞テキストを歌声に強制アライメントする。

    emissions: vocals.ctc_emissions の出力、rms_db: vocals.vocal_rms_db の出力
    note_times / grid_times: メロディの発音時刻・8分グリッドの時刻 [s]（与えると事前分布に使う）"""
    raw_lines = lyrics.splitlines()
    parsed = [tokenize(raw) if raw.strip() else [] for raw in raw_lines]
    sung = [toks for toks in parsed if line_morae(toks)]
    T = emissions.shape[0]
    prior = None

    def solve():
        tg = build_target(sung, vocab)
        return (tg, *viterbi(emissions, tg, star_pen, prior=prior))

    if note_times is not None and len(note_times):
        # CTC の発火は歌の発音より早い（子音）ので、まず事前分布なしで解いてずれを測り、その分ずらした山を置く
        tg0, segs0, _ = solve()
        lag = estimate_lag(tg0, segs0, note_times) or 0.0
        log.info("CTC の発火 − メロディ発音 = %+.0f ms", lag * 1000)
        prior = onset_prior(T, note_times, grid_times if grid_times is not None else [], lag, w_note, w_grid)

    for toks in sung:  # 読み候補の選択（1トークンずつ、全体のスコアで）
        for t in toks:
            if t.candidates and len(t.candidates) > 1:
                scores = {}
                for r in t.candidates:
                    t.set_reading(r)
                    scores[r] = solve()[2]
                best = max(t.candidates, key=lambda r: scores[r])  # 同点なら先の候補
                t.set_reading(best)
                log.info("読み候補 %s: %s", t.surface, " / ".join(f"{r}={s:.1f}" for r, s in scores.items()))

    tg, segs, _ = solve()
    mt, conf = mora_times(tg, segs, prior=prior, w=w_note)
    ok = ~np.isnan(mt)
    mt = np.interp(np.arange(len(mt)), np.arange(len(mt))[ok], mt[ok])  # 行まるごと聞き取れなかった場合
    log.info("モーラ %d（確信あり %d）", len(mt), int(conf.sum()))

    thr = np.percentile(rms_db, 60) - 12
    out, n = [], 0
    for raw, toks in zip(raw_lines, parsed):
        line = AlignedLine(raw, toks)
        if line_morae(toks):
            a, b = tg.line_mora[n]
            n += 1
            st = mt[a:b]
            en = np.append(st[1:], 0.0)
            f = int(st[-1]) + 1  # 行末モーラの終わり：ボーカルの音量が落ちるところ（最大3秒）
            while f < min(int(st[-1]) + 150, T) and rms_db[f] > thr:
                f += 1
            en[-1] = max(f, st[-1] + 5)
            line.morae = [
                Mora(m, round(float(s) * HOP, 3), round(float(e) * HOP, 3), bool(c))
                for m, s, e, c in zip(line_morae(toks), st, en, conf[a:b])
            ]
        out.append(line)
    return Alignment(out)
