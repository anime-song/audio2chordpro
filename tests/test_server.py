"""UI の API（重い処理はまねるだけ。歌詞サイトにはつながない）"""

import time

import pytest
from fastapi.testclient import TestClient

from audio2chordpro.providers import lyrics as lyrics_mod
from audio2chordpro.providers.lyrics import LyricsPage, SongHit
from audio2chordpro.server import create_app
from audio2chordpro.song_info import SongInfo


@pytest.fixture
def client(tmp_path, calls):
    with TestClient(create_app(tmp_path / "projects", tmp_path / "models")) as c:
        yield c


def _upload(client, song, analyze=False):
    audio = song[0]
    with open(audio, "rb") as f:
        r = client.post("/api/projects", files={"audio": (audio.name, f, "audio/wav")}, data={"analyze": analyze})
    assert r.status_code == 200, r.text
    return r.json()


def _wait(client, job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"ジョブが終わりません: {job}")


def test_flow(client, song, calls, monkeypatch):
    # 音源を入れると曲情報はファイル名から、解析は裏で始まる
    p = _upload(client, song, analyze=True)
    pid = p["id"]
    assert (p["id"], p["info"]["title"], p["info"]["artist"], p["audio"]) == (
        "朝のうた",
        "朝のうた",
        "歌手A",
        song[0].name,
    )
    assert p["sources"] == {"title": "filename", "artist": "filename"}
    (job,) = p["jobs"]
    assert job["stages"] == ["analysis"]
    job = _wait(client, job["id"])
    assert job["state"] == "done", job
    assert job["finished_stages"] == ["midi", "melody", "vocals"]
    assert any("midi" in line for line in job["log"])

    # 歌詞を検索して選ぶ（空いている作詞・作曲は歌詞サイトで埋まる。ファイル名から推した曲名も置き換わる）
    hit = SongHit("utaten", "朝のうた", "歌手A", "https://utaten.com/lyric/ab123/", lyricist="作家B")
    page = LyricsPage("utaten", hit.url, SongInfo("朝のうた", "歌手A", "作家B", "作家C"), "あさ\n", "あさ\n")
    monkeypatch.setattr(lyrics_mod, "search", lambda title, artist="", sites=None, page=1: [hit])
    monkeypatch.setattr(lyrics_mod, "fetch", lambda h: page)
    hits = client.get(f"/api/projects/{pid}/lyrics/search").json()
    assert hits[0]["url"] == hit.url and hits[0]["lyricist"] == "作家B"
    p = client.post(f"/api/projects/{pid}/lyrics/use", json={"url": hit.url}).json()
    assert p["lyrics"] == "あさ\n" and p["lyrics_source"]["url"] == hit.url
    assert (p["info"]["lyricist"], p["sources"]["lyricist"]) == ("作家B", "site:utaten")
    assert p["status"]["align"] == "pending"

    # 残り（アライメント → ChordPro）
    job = client.post(f"/api/projects/{pid}/run", json={}).json()
    assert _wait(client, job["id"])["state"] == "done"
    p = client.get(f"/api/projects/{pid}").json()
    assert set(p["status"].values()) <= {"done", "skipped"} and not p["busy"]
    assert p["output"] == "朝のうた.cho"
    r = client.get(f"/api/projects/{pid}/chordpro", params={"download": True})
    assert r.text.startswith("{title:朝のうた}") and "attachment" in r.headers["content-disposition"]
    assert (calls["midi"], calls["melody"], calls["vocals"], calls["align"]) == (1, 1, 1, 1)

    # 書式を変えると ChordPro だけ古くなる
    p = client.patch(f"/api/projects/{pid}/options", json={"simplify": True}).json()
    assert p["options"]["simplify"] and p["status"]["render"] == "stale" and p["status"]["align"] == "done"

    # 曲情報の手直し
    p = client.patch(f"/api/projects/{pid}/info", json={"title": "朝の歌"}).json()
    assert (p["info"]["title"], p["sources"]["title"]) == ("朝の歌", "manual")

    # 一覧・ファイル
    assert [x["id"] for x in client.get("/api/projects").json()] == [pid]
    audio = client.get(f"/api/projects/{pid}/audio", headers={"Range": "bytes=0-9"})
    assert audio.status_code == 206 and len(audio.content) == 10
    assert client.get(f"/api/projects/{pid}/midi").content[:4] == b"MThd"

    # 同じ音源をもう一度入れても新しく作らない
    assert _upload(client, song)["id"] == pid

    client.delete(f"/api/projects/{pid}")
    assert client.get("/api/projects").json() == []


def test_errors(client, song, calls):
    assert client.get("/api/projects/nothing").status_code == 404
    assert client.get("/api/projects/..").status_code == 404
    assert client.get("/api/jobs/nothing").status_code == 404
    r = client.post("/api/projects", files={"audio": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 400

    pid = _upload(client, song)["id"]
    assert client.patch(f"/api/projects/{pid}/options", json={"melody": "nothing"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/run", json={"stages": ["lyrics"]}).status_code == 422
    assert client.get(f"/api/projects/{pid}/chordpro").status_code == 404

    # 失敗した段階はジョブに書く
    calls["hook"] = lambda: 1 / 0
    job = client.post(f"/api/projects/{pid}/run", json={"stages": ["vocals"]}).json()
    job = _wait(client, job["id"])
    assert job["state"] == "error" and job["error"].startswith("ZeroDivisionError")
    assert any("Traceback" in line for line in job["log"])


def test_upload_midi(client, song, calls):
    pid = _upload(client, song)["id"]
    with open(song[1], "rb") as f:
        p = client.put(f"/api/projects/{pid}/midi", files={"midi": ("song.mid", f, "audio/midi")}).json()
    assert p["status"]["midi"] == "done" and p["midi_source"] == "upload"
    job = client.post(f"/api/projects/{pid}/run", json={"stages": ["render"]}).json()
    assert _wait(client, job["id"])["state"] == "done"
    assert calls["midi"] == 0  # tsumugi は走らない
