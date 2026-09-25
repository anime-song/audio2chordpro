"""プロジェクトの段階と、入力が変わったときのやり直し（重い処理はまねるだけ。MIDI・音源はその場で作る）"""

import json
import shutil

import pytest
from conftest import ALIGNMENT

from audio2chordpro.project import Project, projects
from audio2chordpro.timeline import midi_chords


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
    assert (calls["vocals"], calls["align"]) == (1, 0)

    # 歌詞を入れるとアライメントから後ろをやり直す。解析はやり直さない
    p.set_lyrics("あさ")
    assert p.status()["align"] == "pending"
    assert set(p.run().values()) <= {"done", "skipped"}
    assert (calls["vocals"], calls["align"]) == (1, 1)
    assert "あ" in p.chordpro()
    p.run()
    assert (calls["vocals"], calls["align"]) == (1, 1)

    # 出力の書式・曲情報を変えたら ChordPro だけ
    p.set_options(simplify=True)
    assert p.status()["render"] == "stale" and p.status()["align"] == "done"
    p.set_info(title="朝の歌")
    p.run()
    assert p.output_path.name == "朝の歌.cho" and not (p.dir / "output" / "朝のうた.cho").exists()
    assert p.song["sources"]["title"] == "manual"
    assert (calls["vocals"], calls["align"]) == (1, 1)

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


def test_changes_during_a_run_are_kept(tmp_path, song, calls):
    """段階の実行中に設定・歌詞を変えても消えず、そのとき実行していた段階より後ろは古い扱いになる"""
    p = Project.create(tmp_path / "projects", song[0], models_dir=tmp_path / "models")
    p.set_options(melody="amt")
    other = Project.open(p.dir)  # UI（別のスレッド）から開いたもの
    calls["hook"] = lambda: (other.set_options(simplify=True), other.set_lyrics("あさ"))
    p.run("analysis")
    assert calls["midi"] == 1
    assert p.options.render.simplify and p.lyrics == "あさ\n"
    assert p.status() == {
        "midi": "done",
        "melody": "skipped",
        "vocals": "done",
        "align": "pending",
        "render": "pending",
    }


def test_large_files_stay_out_of_the_project(tmp_path, song, calls):
    """tsumugi のステムは消し、ボーカル分離・CTC は scratch に置く（プロジェクトには残さない）"""
    p = Project.create(tmp_path / "projects", song[0], scratch_dir=tmp_path / "scratch")
    p.set_options(melody="amt")
    p.run("analysis")
    assert p.midi_path.exists()
    assert p.scratch.parent == tmp_path / "scratch"
    assert not (p.scratch / "tsumugi" / "out" / p.audio.stem).exists()  # ステムは消えている
    assert p.options.scratch_dir == p.scratch and (p.scratch / "ctc").is_dir()
    assert not any(p.dir.rglob("*.npy"))

    # scratch が消えても、ボーカル分離は済みのまま（アライメントのときに作り直す）
    shutil.rmtree(p.scratch)
    assert p.status()["vocals"] == "done"

    # 消すと scratch も消える
    scratch = p.scratch
    (scratch / "ctc").mkdir(parents=True)
    p.delete()
    assert not p.dir.exists() and not scratch.exists()


def test_old_work_folder_is_moved(tmp_path, song):
    """前の版の work/ にあった大きいファイル：ボーカル分離・CTC は scratch へ、tsumugi の出力は消す"""
    p = Project.create(tmp_path / "projects", song[0], scratch_dir=tmp_path / "scratch")
    for f in ("sep/htdemucs/x/vocals.wav", "ctc/x.npy", "tsumugi/out/x/stems/a.wav", "sheetsage/x/melody_vocal.mid"):
        (p.work / f).parent.mkdir(parents=True, exist_ok=True)
        (p.work / f).write_bytes(b"x")
    q = Project.open(p.dir, scratch_dir=tmp_path / "scratch")
    assert sorted(x.name for x in q.work.iterdir()) == ["sheetsage"]
    assert (q.scratch / "sep/htdemucs/x/vocals.wav").exists() and (q.scratch / "ctc/x.npy").exists()
