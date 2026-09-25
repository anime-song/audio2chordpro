"""プロジェクトの段階と、入力が変わったときのやり直し（重い処理はまねるだけ。MIDI・音源はその場で作る）"""

import json

import numpy as np
import pytest
import soundfile as sf
from test_timeline_chords import _make_midi

from audio2chordpro import project as project_mod
from audio2chordpro.align import Alignment
from audio2chordpro.project import Project, projects
from audio2chordpro.timeline import midi_chords

ALIGNMENT = {
    "lines": [
        {
            "text": "あさ",
            "tokens": [{"surface": "あさ", "reading": "アサ"}],
            "morae": [{"kana": "あ", "start": 0.0, "end": 0.4}, {"kana": "さ", "start": 4.0, "end": 4.4}],
        }
    ]
}


@pytest.fixture
def calls(monkeypatch):
    """重い処理を置き換えて、呼ばれた回数を数える"""
    n = {"vocals": 0, "align": 0}

    def vocal_features(audio, opt):
        n["vocals"] += 1
        path = project_mod.ctc_path(audio, opt)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, np.zeros((1, 1)))

    def align_audio(audio, lyrics, tl, notes, opt):
        n["align"] += 1
        return Alignment.from_dict(ALIGNMENT)

    def no_tsumugi(*a, **k):
        raise AssertionError("MIDI は set_midi で渡している")

    monkeypatch.setattr(project_mod, "vocal_features", vocal_features)
    monkeypatch.setattr(project_mod, "align_audio", align_audio)
    monkeypatch.setattr(project_mod, "make_midi", no_tsumugi)
    return n


@pytest.fixture
def song(tmp_path):
    audio = tmp_path / "歌手A - 朝のうた.wav"
    sf.write(str(audio), np.zeros(800, dtype=np.float32), 8000)
    return audio, _make_midi(tmp_path / "amt.mid")


def test_stages(tmp_path, song, calls):
    audio, midi = song
    p = Project.create(tmp_path / "projects", audio, models_dir=tmp_path / "models")
    assert p.dir.name == "朝のうた"
    assert (p.info.title, p.info.artist, p.song["sources"]["title"]) == ("朝のうた", "歌手A", "filename")
    assert p.status() == {
        "midi": "pending",
        "melody": "pending",
        "vocals": "pending",
        "align": "skipped",
        "render": "pending",
    }
    p.set_options(melody="amt")  # SheetSage2 を使わない
    p.set_midi(midi)
    assert p.status()["midi"] == "done" and p.status()["melody"] == "skipped"

    # 歌詞なし：コード譜だけ
    assert p.run() == {"midi": "done", "melody": "skipped", "vocals": "done", "align": "skipped", "render": "done"}
    assert p.output_path.name == "朝のうた.cho"
    assert p.chordpro().startswith("{title:朝のうた}\n{subtitle:歌：歌手A}")
    assert calls == {"vocals": 1, "align": 0}

    # 歌詞を入れるとアライメントから後ろをやり直す。解析はやり直さない
    p.set_lyrics("あさ")
    assert p.status()["align"] == "pending"
    assert set(p.run().values()) <= {"done", "skipped"}
    assert calls == {"vocals": 1, "align": 1}
    assert "あ" in p.chordpro()
    p.run()
    assert calls == {"vocals": 1, "align": 1}

    # 出力の書式・曲情報を変えたら ChordPro だけ
    p.set_options(simplify=True)
    assert p.status()["render"] == "stale" and p.status()["align"] == "done"
    p.set_info(title="朝の歌")
    p.run()
    assert p.output_path.name == "朝の歌.cho" and not (p.dir / "output" / "朝のうた.cho").exists()
    assert p.song["sources"]["title"] == "manual"
    assert calls == {"vocals": 1, "align": 1}

    # 拍の取り方を変えるとアライメントもやり直す
    p.set_options(melody_prior=False)
    assert p.status()["align"] == "stale"
    p.run("render")
    assert calls["align"] == 2

    # 同じ音源ならプロジェクトを作らずに開く
    assert Project.create(tmp_path / "projects", audio).dir == p.dir
    assert [q.dir for q in projects(tmp_path / "projects")] == [p.dir]


def test_edits(tmp_path, song, calls):
    audio, midi = song
    p = Project.create(tmp_path / "projects", audio, models_dir=tmp_path / "models")
    p.set_options(melody="amt")
    p.set_midi(midi)
    p.set_lyrics("あさ")
    p.run()
    before = p.chordpro()

    # コードの手直し
    chords = p.midi_chords()
    chords[1] = (chords[1][0], "D:7")
    p.save_chords(chords)
    assert p.status()["render"] == "stale"
    p.run()
    assert p.chordpro() != before and "D7" in p.chordpro()
    assert [lab for _, lab in midi_chords(p.export_midi())] == ["C:maj", "D:7", "F:maj", "G:maj"]
    p.discard_chord_edits()
    p.run()
    assert p.chordpro() == before

    # アライメントの手直し：歌詞が変わったら使わない（自動のものに戻る）
    edited = json.loads(json.dumps(ALIGNMENT))
    edited["lines"][0]["morae"][1]["start"] = 8.0
    p.save_alignment(edited)
    assert p.status()["render"] == "stale"
    assert p.alignment().lines[0].morae[1].start == 8.0
    p.set_lyrics("あさ\n")  # 中身が同じなら手直しはそのまま
    assert not p.alignment_edits_outdated
    p.set_lyrics("あさだ")
    assert p.alignment_edits_outdated and p.status()["align"] == "stale"
    assert p.alignment().lines[0].morae[1].start == 4.0
    p.discard_alignment_edits()
    assert not p.edited_alignment_path.exists()


def test_run_rejects_unknown_stage(tmp_path, song):
    p = Project.create(tmp_path / "projects", song[0])
    with pytest.raises(ValueError):
        p.run("lyrics")
    with pytest.raises(TypeError):
        p.set_options(no_such_option=1)
