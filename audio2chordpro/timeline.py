"""AMT の MIDI（Predicted Tempo Map / Predicted Chords / melody トラック）→ 拍単位のタイムライン

以降の処理（歌詞の配置・ChordPro 出力）はすべて秒ではなく拍位置（float、0 = 最初の小節頭）で扱う。
AMT のテンポマップによくある誤りはここで直す：
  * テンポのオクターブ誤り（倍テンポ・半分テンポで数えた区間）
  * 冒頭の数小節だけテンポが極端な値になる
  * 1拍に満たない 1/16 拍子の小節が挟まって拍の位相がずれる
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import mido
import numpy as np

from .chords import ChordSym, Key, parse_chord, parse_key


@dataclass
class ChordEvent:
    beat: float
    chord: ChordSym
    raw: str
    label: str | None = None  # 表示をコード名の代わりにこれにする（ディグリー表記など）


@dataclass
class Note:
    on: float  # 拍
    off: float  # 拍
    pitch: int


@dataclass
class Timeline:
    beat_times: np.ndarray  # 各拍の時刻 [s]
    beats_per_bar: int
    chords: list[ChordEvent] = field(default_factory=list)
    keys: list[tuple[float, Key]] = field(default_factory=list)  # (拍, 調)
    melody: list[Note] = field(default_factory=list)  # MIDI の melody トラック
    audio_offset: float = 0.0  # 音響由来の時刻（歌のモーラ）を拍に直すときの補正 [s]
    repairs: list[str] = field(default_factory=list)  # テンポ修復のログ
    bar_starts: np.ndarray | None = None  # 各小節頭の拍番号（None = beats_per_bar ごと）。変拍子用

    # ---- 小節
    def bars(self) -> np.ndarray:
        if self.bar_starts is None:
            return np.arange(len(self.beat_times) // self.beats_per_bar + 2) * self.beats_per_bar
        return self.bar_starts

    def bar_index(self, beat: float) -> int:
        """beat を含む小節の番号（範囲外は beats_per_bar で外挿）"""
        bs = self.bars()
        if beat >= bs[-1]:
            return len(bs) - 1 + int((beat - bs[-1]) // self.beats_per_bar)
        return max(int(np.searchsorted(bs, beat + 1e-6, side="right") - 1), 0)

    def bar_start(self, i: int) -> float:
        bs = self.bars()
        if i < len(bs):
            return float(bs[i])
        return float(bs[-1] + (i - len(bs) + 1) * self.beats_per_bar)

    def bar_len(self, i: int) -> int:
        return int(round(self.bar_start(i + 1) - self.bar_start(i)))

    # ---- 秒 <-> 拍（範囲外は端の拍間隔で外挿）
    def sec2beat(self, t, audio: bool = False):
        bt = self.beat_times + (self.audio_offset if audio else 0.0)
        t = np.asarray(t, dtype=float)
        out = np.interp(t, bt, np.arange(len(bt), dtype=float))
        out = np.where(t < bt[0], (t - bt[0]) / (bt[1] - bt[0]), out)
        out = np.where(t > bt[-1], len(bt) - 1 + (t - bt[-1]) / (bt[-1] - bt[-2]), out)
        return out

    def beat2sec(self, b):
        return np.interp(b, np.arange(len(self.beat_times), dtype=float), self.beat_times)

    @property
    def bpm(self) -> float:
        return 60.0 / float(np.median(np.diff(self.beat_times)))

    # ---- コード・調
    def key_at(self, beat: float) -> Key:
        k = self.keys[0][1]
        for b, kk in self.keys:
            if b <= beat + 1e-6:
                k = kk
        return k

    def chord_label(self, ev: ChordEvent, simplify: bool = False) -> str:
        if ev.label is not None:
            return ev.label
        return ev.chord.render(self.key_at(ev.beat), simplify)

    def chord_at(self, beat: float) -> ChordEvent | None:
        cur = None
        for ev in self.chords:
            if ev.beat <= beat + 1e-6:
                cur = ev
        return cur


# ================================================================== 拍グリッドの修復
def _repair_beats(beat_times: np.ndarray, bpb: int, tol: float = 0.18):
    """小節ごとの拍間隔を中央値と比べ、外れた小節の連続区間を中央値のテンポで打ち直す。

    倍テンポ・半分テンポ・3:2 などの単純比なら「元の拍数 × 比」で拍数を決める
    （区間長から丸めると、区間内のテンポの揺れで小節の倍数からずれるため）。
    冒頭の異常区間は、最初の正常小節頭から等間隔に遡る（弱起・前奏のテンポ誤推定が多い）。"""
    period = np.diff(beat_times)
    P = float(np.median(period))
    n_bars = (len(beat_times) - 1) // bpb
    bar_period = np.array([period[i * bpb : (i + 1) * bpb].mean() for i in range(n_bars)])
    bad = np.abs(bar_period / P - 1) > tol
    new = [beat_times[0]]
    log = []
    i = 0
    while i < n_bars:
        if not bad[i]:
            new.extend(beat_times[i * bpb + 1 : (i + 1) * bpb + 1])
            i += 1
            continue
        j = i
        while j < n_bars and bad[j]:
            j += 1
        t0, t1 = beat_times[i * bpb], beat_times[j * bpb]
        n = max(1, int(round((t1 - t0) / P)))
        r = float(bar_period[i:j].mean()) / P
        for q in (0.5, 2 / 3, 0.75, 1.5, 2.0):
            if abs(r / q - 1) < 0.08 and ((j - i) * bpb * q) % bpb == 0:
                n = int((j - i) * bpb * q)
                break
        if i == 0 and j < n_bars and n % bpb:
            n = int(np.ceil(n / bpb)) * bpb  # 拍0 は音源の開始より前（負の時刻）になりうる
            new = list(t1 - np.arange(n, -1, -1) * P)
        else:
            new.extend(np.linspace(t0, t1, n + 1)[1:])
        msg = (
            f"小節 {i}〜{j - 1}（{t0:.2f}s〜{t1:.2f}s）: 推定 {60 / bar_period[i]:.0f}BPM×{j - i}小節 "
            f"→ {60 / P:.0f}BPMで{n}拍（{n / bpb:g}小節）に修復"
        )
        if n % bpb and not (i == 0 and new[0] < t0 - 1e-6):
            msg += " ※拍数が小節の倍数でない"
        log.append(msg)
        i = j
    new.extend(beat_times[n_bars * bpb + 1 :])
    return np.array(new), log


def _beat_ticks(tsigs, tpb: int, end_tick: int):
    """拍子イベントに従って拍の tick 位置を列挙する。1拍に満たない小節（グリッチ）は読み飛ばす"""
    tsigs = sorted(tsigs) or [(0, 4, 4)]
    if tsigs[0][0] != 0:
        tsigs.insert(0, (0, 4, 4))
    ticks, bpbs = [], []
    for k, (t0, num, den) in enumerate(tsigs):
        t1 = tsigs[k + 1][0] if k + 1 < len(tsigs) else end_tick + tpb
        bar = num * tpb * 4 // den
        if bar < tpb:
            continue
        # 拍は常に4分音符（2/2 は4拍、6/8 は3拍の小節）
        ticks.extend(range(t0, t1, tpb))
        bpbs += [num * 4 // den] * ((t1 - t0) // bar)
    bpb = max(set(bpbs), key=bpbs.count) if bpbs else 4
    return ticks, bpb


def external_grid(beats, downbeats, ref_period: float | None = None):
    """外部のビート推定（拍[s], 小節頭[s]）→ (拍時刻, 小節頭の拍番号, 拍子)。

    * ref_period（AMT の拍間隔）の2倍/半分ならテンポのオクターブを直す
    * 小節頭の誤検出で 1/4 や 7/4 の小節ができないよう、別の位置の小節頭が2小節以上続くときだけ拍子の変化とみなす
    * 最初の小節頭より前の拍（弱起）は1小節ぶんになるよう外挿し、拍0 を小節頭にする"""
    beats = np.sort(np.asarray(beats, float))
    downbeats = np.asarray(downbeats, float)
    if ref_period and len(beats) > 2:
        r = float(np.median(np.diff(beats))) / ref_period
        if 1.7 < r < 2.3:
            beats = np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) / 2]))
        elif 0.43 < r < 0.59:
            db0 = int(np.argmin(np.abs(beats - downbeats[0]))) if len(downbeats) else 0
            beats = beats[db0 % 2 :: 2]
    db = sorted({int(np.argmin(np.abs(beats - d))) for d in downbeats if np.min(np.abs(beats - d)) < 0.1})
    if len(db) < 2:
        return beats, None, 4
    bpb = int(np.bincount(np.diff(db)).argmax())
    if bpb >= 6 and bpb % 2 == 0:  # 半分のテンポで数えていた小節（= 2小節）を割る
        bpb //= 2
        db = sorted(set(db) | {d + bpb for d in db if d + bpb < len(beats)})
    dbset = set(db)
    clean = [db[0]]
    while clean[-1] + bpb < len(beats):
        nxt = clean[-1] + bpb
        if nxt not in dbset:
            alt = [
                d
                for d in (nxt - 1, nxt + 1, nxt - 2, nxt + 2)
                if d > clean[-1] and d in dbset and d + bpb in dbset and d + 2 * bpb in dbset
            ]
            if alt:
                nxt = alt[0]
        clean.append(nxt)
    pre = (bpb - clean[0] % bpb) % bpb
    beats = np.concatenate([beats[0] - (beats[1] - beats[0]) * np.arange(pre, 0, -1), beats])
    db = [d + pre for d in clean]
    starts = list(range(db[0] % bpb, db[0], bpb)) + db
    if starts[0] != 0:
        starts = [0] + starts
    while starts[-1] < len(beats) + bpb:
        starts.append(starts[-1] + bpb)
    return beats, np.array(starts), bpb


# ================================================================== 読み込み
def _abs_events(track):
    t = 0
    for msg in track:
        t += msg.time
        yield t, msg


@dataclass
class _Midi:
    """MIDI のテンポ・調・拍子と、コードの marker"""

    mid: mido.MidiFile
    tempos: list[tuple[int, int]]  # (tick, μs/拍)。先頭は tick 0
    key_ticks: list[tuple[int, str]]
    tsigs: list[tuple[int, int, int]]
    chord_ticks: list[tuple[int, str]]
    chord_tracks: list[int]  # コードの marker を書いてあるトラック

    def t2s(self, tick) -> float:
        tpb = self.mid.ticks_per_beat
        s, pt, ptempo = 0.0, 0, self.tempos[0][1]
        for tt, tp in self.tempos:
            if tt > tick:
                break
            s += (tt - pt) * ptempo / 1e6 / tpb
            pt, ptempo = tt, tp
        return s + (tick - pt) * ptempo / 1e6 / tpb

    def s2t(self, sec: float) -> int:
        tpb = self.mid.ticks_per_beat
        s, pt, ptempo = 0.0, 0, self.tempos[0][1]
        for tt, tp in self.tempos:
            seg = (tt - pt) * ptempo / 1e6 / tpb
            if s + seg > sec:
                break
            s += seg
            pt, ptempo = tt, tp
        return max(int(round(pt + (sec - s) * 1e6 * tpb / ptempo)), 0)


def _read_midi(path) -> _Midi:
    mid = mido.MidiFile(path)
    tempos, key_ticks, tsigs = [], [], []
    chords: dict[int, list] = {}  # トラック → marker
    others: dict[int, list] = {}
    for i, tr in enumerate(mid.tracks):
        for t, msg in _abs_events(tr):
            if msg.type == "set_tempo":
                tempos.append((t, msg.tempo))
            elif msg.type == "key_signature":
                key_ticks.append((t, msg.key))
            elif msg.type == "time_signature":
                tsigs.append((t, msg.numerator, msg.denominator))
            elif msg.type == "marker":
                (chords if tr.name.lower().startswith("predicted chord") else others).setdefault(i, []).append(
                    (t, msg.text)
                )
    # コードの marker がテンポマップのトラックに書かれている MIDI もある
    chords = chords or others
    tempos.sort()
    if not tempos or tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))
    chord_ticks = sorted((x for v in chords.values() for x in v), key=lambda x: x[0])
    return _Midi(mid, tempos, key_ticks, tsigs, chord_ticks, sorted(chords))


def midi_chords(path) -> list[tuple[float, str]]:
    """MIDI のコード (秒, コード名) の列（手で直すときの元）"""
    m = _read_midi(path)
    return [(m.t2s(t), lab) for t, lab in m.chord_ticks]


def write_chords_midi(path, chords: list[tuple[float, str]], out) -> None:
    """MIDI のコードの marker を chords (秒, コード名) に置き換えて out に保存する（テンポ・ノートはそのまま）"""
    m = _read_midi(path)
    mid = m.mid
    for i in m.chord_tracks:
        _set_events(mid.tracks[i], [(t, msg) for t, msg in _abs_events(mid.tracks[i]) if msg.type != "marker"])
    track = next((tr for tr in mid.tracks if tr.name.lower().startswith("predicted chord")), None)
    if track is None:
        track = mido.MidiTrack([mido.MetaMessage("track_name", name="Predicted Chords", time=0)])
        mid.tracks.append(track)
    events = [(t, msg) for t, msg in _abs_events(track)]
    events += [(m.s2t(sec), mido.MetaMessage("marker", text=lab)) for sec, lab in sorted(chords)]
    _set_events(track, events)
    mid.save(str(out))


def _set_events(track, events) -> None:
    """絶対 tick のイベントでトラックを作り直す（end_of_track は最後に1つ）"""
    events = sorted(((t, msg) for t, msg in events if msg.type != "end_of_track"), key=lambda x: x[0])
    end = max([t for t, msg in _abs_events(track)] + [t for t, _ in events] + [0])
    track.clear()
    prev = 0
    for t, msg in events:
        track.append(msg.copy(time=t - prev))
        prev = t
    track.append(mido.MetaMessage("end_of_track", time=end - prev))


def load_timeline(
    path: str,
    beats=None,
    quantize: float = 0.5,
    melody_track: str = "melody",
    chords: list[tuple[float, str]] | None = None,
) -> Timeline:
    """AMT の MIDI を読み込む。

    beats: 外部のビート推定 (拍[s], 小節頭[s])。与えるとテンポマップの代わりに拍グリッドにする。
           コード・調は常に MIDI のもの（秒に直してから拍グリッドに載せる）。
    quantize: コードの開始位置を丸める単位 [拍]（0.5 = 8分）
    chords: (秒, コード名) の列。与えると MIDI のコードの代わりに使う（ユーザーが手で直したコード）"""
    m = _read_midi(path)
    mid, tpb, key_ticks, tsigs, t2s = m.mid, m.mid.ticks_per_beat, m.key_ticks, m.tsigs, m.t2s
    if chords is None:
        chords = [(t2s(t), lab) for t, lab in m.chord_ticks]

    end_tick = max(t for tr in mid.tracks for t, _ in _abs_events(tr))
    beat_ticks, bpb = _beat_ticks(tsigs, tpb, end_tick)
    raw_beats = np.array([t2s(t) for t in beat_ticks])
    repaired, log = _repair_beats(raw_beats, bpb)
    bar_starts = None
    if beats is not None:
        ref = float(np.median(np.diff(repaired)))
        beat_times, bar_starts, bpb = external_grid(*beats, ref_period=ref)
        log = [f"外部ビート: {len(beat_times)}拍"]
    else:
        beat_times = repaired
    tl = Timeline(beat_times, bpb, repairs=log, bar_starts=bar_starts)

    # コード：秒 → 拍 → 量子化。同じ位置は後勝ち（前のは瞬間的な誤検出とみなす）、同じコードの連続はまとめる
    for sec, lab in sorted(chords, key=lambda x: x[0]):
        b = float(tl.sec2beat(sec))
        if quantize:
            b = round(b / quantize) * quantize
        ev = ChordEvent(b, parse_chord(lab), lab)
        if tl.chords and abs(tl.chords[-1].beat - b) < 1e-6:
            tl.chords[-1] = ev
        elif not tl.chords or tl.chords[-1].chord != ev.chord:
            tl.chords.append(ev)

    # 調：小節頭に丸める
    for tick, k in key_ticks:
        b = float(tl.sec2beat(t2s(tick)))
        i = tl.bar_index(b)
        b = tl.bar_start(i + 1) if b - tl.bar_start(i) > tl.bar_len(i) / 2 else tl.bar_start(i)
        if not tl.keys or str(tl.keys[-1][1]) != k:
            tl.keys.append((b, parse_key(k)))
    tl.keys = tl.keys or [(0.0, Key("C"))]

    # メロディ
    for tr in mid.tracks:
        if tr.name.strip().lower() != melody_track:
            continue
        on = {}
        for t, msg in _abs_events(tr):
            if msg.type == "note_on" and msg.velocity > 0:
                on[msg.note] = t
            elif msg.type in ("note_on", "note_off") and msg.note in on:
                s = on.pop(msg.note)
                tl.melody.append(Note(float(tl.sec2beat(t2s(s))), float(tl.sec2beat(t2s(t))), msg.note))
        tl.melody.sort(key=lambda n: (n.on, -n.pitch))
    tl.audio_offset = estimate_grid_offset(tl)
    return tl


def estimate_grid_offset(tl: Timeline, grid: float = 0.5) -> float:
    """メロディの発音が8分グリッドから系統的にずれている量 [s]（正 = 音響がグリッドより遅い）"""
    dev = []
    for n in tl.melody:
        q = round(n.on / grid) * grid
        if abs(n.on - q) < grid * 0.4:
            dev.append(float(tl.beat2sec(n.on) - tl.beat2sec(q)))
    return float(np.median(dev)) if dev else 0.0


def skyline(notes: list[Note]) -> list[Note]:
    """同時に鳴っている音はいちばん高い音だけ残す（歌メロ抽出の簡易版）"""
    out: list[Note] = []
    for n in notes:
        if out and abs(out[-1].on - n.on) < 0.06:
            if n.pitch > out[-1].pitch:
                out[-1] = n
            continue
        out.append(n)
    return out


# ================================================================== ディグリー表記
def degree_labels(tl: Timeline) -> list[str]:
    """各コードのディグリー（調の主音からの度数。例 VIm7、IV/V、bVI7/I）。chord-romanizer で前後の流れを見て決める。
    主音は MIDI の調（転調も）。chord-romanizer が読めないコードは、コード名のままにする"""
    from chord_romanizer import Romanizer

    names = [tl.chord_label(ev) for ev in tl.chords]
    seq = [
        "N.C." if ev.chord.root_pc is None else (name, tl.key_at(ev.beat).tonic) for ev, name in zip(tl.chords, names)
    ]
    romanizer = Romanizer.strict(default_tonic=tl.keys[0][1].tonic if tl.keys else "C")
    try:
        events = romanizer.annotate_events(seq)
    except ValueError:  # 読めないコードがある。そこを N.C. にしてやり直す
        events = romanizer.annotate_events([x if _readable(x) else "N.C." for x in seq])
    # N.C.（と読めなかったコード）は辞書で返ってくる。そこはコード名（N.C. は "N.C."）のまま
    return [name if isinstance(e, dict) else e.roman for e, name in zip(events, names)]


def _readable(item) -> bool:
    from chord_romanizer import Romanizer

    if not isinstance(item, tuple):
        return True
    try:
        Romanizer.strict(default_tonic=item[1]).romanize(item[0])
        return True
    except ValueError:
        return False


def with_degrees(tl: Timeline) -> Timeline:
    """コードをディグリーで表示するタイムライン（元は変えない）"""
    labels = degree_labels(tl)
    return replace(tl, chords=[replace(ev, label=lab) for ev, lab in zip(tl.chords, labels)])
