"""手で直したコード：MIDI のコードの代わりに使う・MIDI に書き戻す（MIDI はその場で作る）"""

import mido
import pytest

from audio2chordpro.timeline import load_timeline, midi_chords, write_chords_midi

TPB = 480


def _track(name, events, end):
    """(絶対 tick, メッセージ) → トラック"""
    tr = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    prev = 0
    for tick, msg in sorted(events, key=lambda x: x[0]):
        tr.append(msg.copy(time=tick - prev))
        prev = tick
    tr.append(mido.MetaMessage("end_of_track", time=end - prev))
    return tr


def _make_midi(path, chords_in_tempo_track=False):
    """4/4、最初の4小節は 120 BPM（0.5 s/拍）、そのあと 150 BPM（0.4 s/拍）。コードは2小節ごと"""
    tempo = [
        (0, mido.MetaMessage("time_signature", numerator=4, denominator=4)),
        (0, mido.MetaMessage("key_signature", key="C")),
        (0, mido.MetaMessage("set_tempo", tempo=500000)),
        (16 * TPB, mido.MetaMessage("set_tempo", tempo=400000)),
    ]
    labels = [(0, "C:maj"), (8 * TPB, "A:min"), (16 * TPB, "F:maj"), (24 * TPB, "G:maj")]
    markers = [(t, mido.MetaMessage("marker", text=lab)) for t, lab in labels]
    mid = mido.MidiFile(ticks_per_beat=TPB)
    if chords_in_tempo_track:  # コードの marker がテンポマップのトラックにある MIDI
        mid.tracks.append(_track("Predicted Tempo Map", tempo + markers, 32 * TPB))
    else:
        mid.tracks.append(_track("Predicted Tempo Map", tempo, 32 * TPB))
        mid.tracks.append(_track("Predicted Chords", markers, 32 * TPB))
    mid.save(str(path))
    return path


def _labels(tl):
    return [(c.beat, c.raw) for c in tl.chords]


@pytest.mark.parametrize("in_tempo_track", [False, True])
def test_midi_chords_in_seconds(tmp_path, in_tempo_track):
    path = _make_midi(tmp_path / "a.mid", in_tempo_track)
    got = midi_chords(path)
    assert [lab for _, lab in got] == ["C:maj", "A:min", "F:maj", "G:maj"]
    assert [round(s, 6) for s, _ in got] == [0.0, 4.0, 8.0, 11.2]


def test_chords_override(tmp_path):
    path = _make_midi(tmp_path / "a.mid")
    assert _labels(load_timeline(str(path))) == [(0, "C:maj"), (8, "A:min"), (16, "F:maj"), (24, "G:maj")]
    edited = [(0.0, "C:maj"), (4.0, "A:min7"), (6.0, "D:7"), (8.0, "F:maj"), (11.2, "G:maj")]
    tl = load_timeline(str(path), chords=edited)
    assert _labels(tl) == [(0, "C:maj"), (8, "A:min7"), (12, "D:7"), (16, "F:maj"), (24, "G:maj")]


@pytest.mark.parametrize("in_tempo_track", [False, True])
def test_write_chords_midi(tmp_path, in_tempo_track):
    path = _make_midi(tmp_path / "a.mid", in_tempo_track)
    edited = [(0.0, "C:maj"), (6.0, "D:7"), (11.2, "G:maj")]
    write_chords_midi(path, edited, tmp_path / "b.mid")
    assert [(round(s, 6), lab) for s, lab in midi_chords(tmp_path / "b.mid")] == edited
    a, b = load_timeline(str(path)), load_timeline(str(tmp_path / "b.mid"))
    assert list(a.beat_times) == list(b.beat_times)
    assert _labels(b) == _labels(load_timeline(str(path), chords=edited))
