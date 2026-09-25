"""プロジェクト・サーバのテストに共通のもの：その場で作る音源と MIDI、重い処理の置き換え"""

import shutil

import numpy as np
import pytest
import soundfile as sf
from test_timeline_chords import _make_midi

from audio2chordpro import project as project_mod
from audio2chordpro.align import Alignment

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
def song(tmp_path):
    """(音源, AMT の MIDI)。音源は無音、ファイル名から曲名「朝のうた」・歌手「歌手A」"""
    audio = tmp_path / "歌手A - 朝のうた.wav"
    sf.write(str(audio), np.zeros(800, dtype=np.float32), 8000)
    return audio, _make_midi(tmp_path / "amt.mid")


@pytest.fixture
def calls(monkeypatch, song):
    """重い処理（tsumugi・SheetSage2・ボーカル分離・アライメント）を置き換えて、呼ばれた回数を数える。
    calls["hook"] に関数を入れると、ボーカル分離の途中で呼ぶ（実行中に別の操作が入る場面）"""
    n = {"midi": 0, "melody": 0, "vocals": 0, "align": 0, "hook": None}

    def make_midi(audio, opt):
        n["midi"] += 1
        return song[1]

    def run_sheetsage(audio, cache_dir, model):  # 歌メロ（ノートの無い MIDI）を置く
        n["melody"] += 1
        out = project_mod.sheetsage_dir(audio, cache_dir)
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy(song[1], out / "melody_vocal.mid")
        return out

    def vocal_features(audio, opt):
        n["vocals"] += 1
        if n["hook"]:
            n["hook"]()
        path = project_mod.ctc_path(audio, opt)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, np.zeros((1, 1)))

    def align_audio(audio, lyrics, tl, notes, opt):
        n["align"] += 1
        return Alignment.from_dict(ALIGNMENT)

    monkeypatch.setattr(project_mod, "make_midi", make_midi)
    monkeypatch.setattr(project_mod, "run_sheetsage", run_sheetsage)
    monkeypatch.setattr(project_mod, "vocal_features", vocal_features)
    monkeypatch.setattr(project_mod, "align_audio", align_audio)
    return n
