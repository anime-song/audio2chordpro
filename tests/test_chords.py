"""コード名の表示：綴りは MIDI に書いてあるとおり"""

import pytest

from audio2chordpro.chords import Key, parse_chord


@pytest.mark.parametrize(
    "label, key, shown",
    [
        ("F#:7/A#", "Bb", "F#7/A#"),  # 調（Bb）に合わせて Gb7/Bb や F#7/Bb にしない
        ("A#:dim", "D", "A#dim"),
        ("B#:m7", "D", "B#m7"),
        ("Db/Eb", "D", "Db/Eb"),
        ("Ab:m7(9)", "D", "Abm7(9)"),
        ("A:m7(9)", "D", "Am7(9)"),
        ("G:M7", "D", "GM7"),
        ("C:sus4", "D", "Csus4"),
        ("A:min7", "C", "Am7"),  # Harte の品質名は歌本の表記に
        ("C:maj7/5", "C", "CM7/G"),  # Harte の度数のベースはルートから綴る
        ("Eb:maj/3", "C", "Eb/G"),
        ("F#:maj/3", "C", "F#/A#"),
        ("N", "C", "N.C."),
    ],
)
def test_render_keeps_midi_spelling(label, key, shown):
    assert parse_chord(label).render(Key(key)) == shown


def test_simplify():
    assert parse_chord("B#:m7").render(Key("D"), simplify=True) == "Cm7"
    assert parse_chord("Fb:m/Ab").render(Key("D"), simplify=True) == "Em/Ab"  # 綴りを変えるのは E#/B#/Cb/Fb だけ
    assert parse_chord("F##").render(Key("C"), simplify=True) == "G"
    assert parse_chord("F#:7/A#").render(Key("Bb"), simplify=True) == "F#7/A#"


def test_degrees(tmp_path):
    """ディグリー表記（chord-romanizer）。調は MIDI の調、N.C. はそのまま"""
    from test_timeline_chords import _make_midi

    from audio2chordpro.timeline import degree_labels, load_timeline, with_degrees

    path = _make_midi(tmp_path / "a.mid")  # C 長調：C → Am → F → G
    tl = load_timeline(str(path))
    assert degree_labels(tl) == ["I", "VIm", "IV", "V"]
    assert [tl.chord_label(e) for e in tl.chords] == ["C", "Am", "F", "G"]  # 元は変えない
    shown = with_degrees(load_timeline(str(path), chords=[(0.0, "N"), (4.0, "A:m7"), (8.0, "F:maj7"), (11.2, "G:7/B")]))
    assert [shown.chord_label(e) for e in shown.chords] == ["N.C.", "VIm7", "IVM7", "V7/VII"]
