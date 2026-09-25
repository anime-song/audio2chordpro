"""音源のタグ・ファイル名からの曲情報（音源は無音の短いファイルをその場で作る）"""

import numpy as np
import pytest
import soundfile as sf

from audio2chordpro.song_info import clean_lyrics, from_audio, parse_filename, read_tags


@pytest.mark.parametrize(
    "stem, expected",
    [
        ("朝のうた", [("朝のうた", "")]),
        ("歌手A - 朝のうた", [("朝のうた", "歌手A"), ("歌手A", "朝のうた")]),
        ("朝のうた / 歌手A", [("朝のうた", "歌手A"), ("歌手A", "朝のうた")]),
        ("【MV】歌手A「朝のうた」", [("朝のうた", "歌手A")]),
        ("01 朝のうた", [("朝のうた", "")]),
        ("03. 歌手A - 朝のうた (Official Music Video)", [("朝のうた", "歌手A"), ("歌手A", "朝のうた")]),
        ("朝のうた(Acoustic ver.)", [("朝のうた(Acoustic ver.)", "")]),
        ("3月9日", [("3月9日", "")]),
    ],
)
def test_parse_filename(stem, expected):
    assert parse_filename(stem) == expected


def test_clean_lyrics():
    lrc = "[ar:歌手A]\r\n[00:01.00]あさの ひかり\r\n[00:05.50]ことばを のせて\r\n\r\n\r\n\r\n[01:00.00]よるの うた\r\n"
    assert clean_lyrics(lrc) == "あさの ひかり\nことばを のせて\n\nよるの うた"


def _silence(path):
    sf.write(str(path), np.zeros(800, dtype=np.float32), 8000)
    return path


def test_read_id3_in_wav(tmp_path):
    from mutagen.id3 import TCOM, TEXT, TIT2, TPE1, USLT
    from mutagen.wave import WAVE

    path = _silence(tmp_path / "歌手B - 別の曲.wav")
    f = WAVE(str(path))
    f.add_tags()
    for frame in (TIT2(text="朝のうた"), TPE1(text="歌手A"), TEXT(text="作家B"), TCOM(text="作家C")):
        f.tags.add(frame)
    f.tags.add(USLT(lang="jpn", text="あさの ひかり\nことばを のせて"))
    f.save()

    tags, lyrics = read_tags(path)
    assert tags == {"title": "朝のうた", "artist": "歌手A", "lyricist": "作家B", "composer": "作家C"}
    assert lyrics == "あさの ひかり\nことばを のせて"
    meta = from_audio(path)  # タグがあればファイル名は使わない
    assert (meta.info.title, meta.info.artist, meta.sources["title"]) == ("朝のうた", "歌手A", "tag")


def test_read_vorbis_in_flac(tmp_path):
    from mutagen.flac import FLAC

    path = _silence(tmp_path / "x.flac")
    f = FLAC(str(path))
    f["TITLE"], f["ARRANGER"], f["LYRICS"] = "朝のうた", "作家D", "あさの ひかり"
    f.save()
    assert read_tags(path) == ({"title": "朝のうた", "arranger": "作家D"}, "あさの ひかり")


def test_from_filename_without_tags(tmp_path):
    meta = from_audio(_silence(tmp_path / "歌手A - 朝のうた.wav"))
    assert (meta.info.title, meta.info.artist) == ("朝のうた", "歌手A")
    assert meta.sources == {"title": "filename", "artist": "filename"}
    assert meta.alternatives == [("歌手A", "朝のうた")]
    assert meta.lyrics == ""
