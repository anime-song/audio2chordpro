"""1曲 = 1フォルダのプロジェクト。音源 → 曲情報 → 解析 → 歌詞 → アライメント → ChordPro を段階ごとに保存し、
入力が変わった段階から後ろだけやり直す（UI・Colab から使う）

    p = Project.create("projects", "song.mp3")   # 音源のタグ・ファイル名から曲情報を入れる
    p.run("analysis")                             # tsumugi・SheetSage2・ボーカル分離（重い。歌詞は要らない）
    hits = p.search_lyrics()                      # 曲情報で歌詞サイトを検索
    p.use_lyrics(hits[0])
    p.run()                                       # アライメント → ChordPro（まだの段階・古くなった段階だけ）
    p.status()                                    # {"midi": "done", "align": "stale", ...}

projects/<曲名>/
  project.json         設定（Options）・各段階を実行したときの入力の指紋・手直しの記録
  song.json            曲情報（SongInfo）と各項目の出所（tag / filename / site:<サイト> / manual）
  audio/<元のファイル名>
  lyrics.txt           使う歌詞
  lyrics.source.json   歌詞の出所（歌詞サイトの URL・ふりがな付き歌詞など）
  midi/amt.mid         AMT の MIDI（tsumugi の出力かアップロード。書き換えない）
  edits/chords.json    手で直したコード（秒・コード名）。あれば MIDI のコードの代わりに使う
  alignment.auto.json  自動のアライメント
  alignment.json       手で直したアライメント。いまの歌詞から作ったものならこちらを使う
  output/<曲名>.cho
  work/                SheetSage2 の出力（= Options.cache_dir。1〜2 MB）

大きい中間ファイル（ボーカル分離・CTC、合わせて1曲 200 MB ほど）はプロジェクトに置かず、
scratch_dir/<音源のハッシュ>/（既定 ~/.cache/audio2chordpro/scratch。= Options.scratch_dir）に置く。
アライメントをやり直すときにだけ使い、消えていればそのとき作り直す。tsumugi のステムは MIDI を取り出したら消す。

段階の状態: "done"（済み）/ "stale"（入力が変わった）/ "pending"（まだ）/ "skipped"（要らない）
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from .align import Alignment
from .melody import has_sheetsage, run_sheetsage, sheetsage_dir
from .pipeline import (
    Options,
    align_audio,
    ctc_path,
    make_midi,
    prepare,
    render_chordpro,
    scratch_dir,
    vocal_features,
)
from .render import RenderOptions
from .song_info import SongInfo, from_audio
from .timeline import midi_chords, write_chords_midi

log = logging.getLogger(__name__)

DEFAULT_MODELS_DIR = Path.home() / ".cache" / "audio2chordpro"  # tsumugi のソース・チェックポイント（全曲で共有）
DEFAULT_SCRATCH_DIR = DEFAULT_MODELS_DIR / "scratch"  # 大きい中間ファイル（消しても作り直せる）
STAGES = ("midi", "melody", "vocals", "align", "render")
ANALYSIS = ("midi", "melody", "vocals")  # 音源だけで走る重い段階
DEPS = {"align": ("midi", "melody", "vocals"), "render": ("midi", "melody", "align")}
_PER_PROJECT = ("cache_dir", "scratch_dir", "models_dir", "render")  # Options のうち project.json に書かないもの


class Project:
    """project.json・song.json は使うたびに読み、書くときは読み直してから変える。
    段階を実行している間（別のスレッド）に UI から設定・曲情報を変えても、どちらの書き込みも消えない"""

    def __init__(self, path: str | Path, models_dir: str | Path | None = None, scratch_dir: str | Path | None = None):
        # 絶対パスにしておく。tsumugi は実行中にプロセスのカレントフォルダを移す（os.chdir）ので、
        # 相対パスのままだと、その間に別のスレッド（UI）から開いたときに見つからない
        self.dir = Path(path).resolve()
        self.models_dir = Path(models_dir).resolve() if models_dir else DEFAULT_MODELS_DIR
        self.scratch_root = Path(scratch_dir).resolve() if scratch_dir else DEFAULT_SCRATCH_DIR
        if not (self.dir / "project.json").exists():
            raise FileNotFoundError(f"プロジェクトではありません: {self.dir}")
        self._move_old_work()

    @property
    def data(self) -> dict:
        """project.json の中身（読むだけ。変えるときは _update）"""
        with _lock(self.dir):
            return _read_json(self.dir / "project.json")

    def _update(self, change) -> None:
        """project.json を読み直して change(data) で変えて書く"""
        with _lock(self.dir):
            data = _read_json(self.dir / "project.json")
            change(data)
            _write_json(self.dir / "project.json", data)

    # ------------------------------------------------------------ 作る・開く
    @classmethod
    def create(
        cls,
        root: str | Path,
        audio: str | Path,
        models_dir: str | Path | None = None,
        scratch_dir: str | Path | None = None,
    ) -> Project:
        """音源からプロジェクトを作る。同じ音源のプロジェクトが root にあればそれを開く"""
        audio, root = Path(audio).resolve(), Path(root).resolve()
        sha = _file_sha1(audio)
        for p in projects(root, models_dir, scratch_dir):
            if p.data.get("audio_sha1") == sha:
                return p
        meta = from_audio(audio)
        d = _unique_dir(root, _safe_name(meta.info.title or audio.stem))
        (d / "audio").mkdir(parents=True)
        shutil.copy2(audio, d / "audio" / audio.name)
        _write_json(
            d / "project.json",
            {
                "version": 1,
                "created": _now(),
                "audio": f"audio/{audio.name}",
                "audio_sha1": sha,
                "options": _options_dict(Options()),
                "stages": {},
                "edits": {},
            },
        )
        _write_json(
            d / "song.json", {"info": asdict(meta.info), "sources": meta.sources, "alternatives": meta.alternatives}
        )
        p = cls(d, models_dir, scratch_dir)
        if meta.lyrics:
            p.set_lyrics(meta.lyrics, {"type": "tag"})
        return p

    @classmethod
    def open(
        cls, path: str | Path, models_dir: str | Path | None = None, scratch_dir: str | Path | None = None
    ) -> Project:
        return cls(path, models_dir, scratch_dir)

    def delete(self) -> None:
        """プロジェクトと、その大きい中間ファイルを消す"""
        shutil.rmtree(self.scratch, ignore_errors=True)
        shutil.rmtree(self.dir)

    def _move_old_work(self) -> None:
        """前の版は大きい中間ファイルも work/ に置いていた。ボーカル分離・CTC は scratch に移し、tsumugi の出力は消す"""
        work = self.work
        if not any((work / sub).is_dir() for sub in ("sep", "ctc", "tsumugi")):
            return
        with _lock(self.dir):
            for sub in ("sep", "ctc"):
                if (work / sub).is_dir():
                    shutil.copytree(work / sub, self.scratch / sub, dirs_exist_ok=True)
                    shutil.rmtree(work / sub)
            shutil.rmtree(work / "tsumugi", ignore_errors=True)

    # ------------------------------------------------------------ ファイル
    @property
    def audio(self) -> Path:
        return self.dir / self.data["audio"]

    @property
    def work(self) -> Path:
        return self.dir / "work"

    @property
    def scratch(self) -> Path:
        """この曲の大きい中間ファイルの置き場（音源のハッシュで分ける）"""
        return self.scratch_root / self.data["audio_sha1"][:16]

    @property
    def midi_path(self) -> Path:
        return self.dir / "midi" / "amt.mid"

    @property
    def lyrics_path(self) -> Path:
        return self.dir / "lyrics.txt"

    @property
    def chords_path(self) -> Path:
        return self.dir / "edits" / "chords.json"

    @property
    def auto_alignment_path(self) -> Path:
        return self.dir / "alignment.auto.json"

    @property
    def edited_alignment_path(self) -> Path:
        return self.dir / "alignment.json"

    @property
    def output_path(self) -> Path | None:
        name = self.data["stages"].get("render", {}).get("output")
        return self.dir / "output" / name if name else None

    # ------------------------------------------------------------ 設定
    @property
    def options(self) -> Options:
        d = self.data.get("options", {})
        render = RenderOptions(**_known(RenderOptions, d.get("render", {})))
        kw = {k: v for k, v in _known(Options, d).items() if k not in _PER_PROJECT}
        return Options(**kw, render=render, cache_dir=self.work, scratch_dir=self.scratch, models_dir=self.models_dir)

    def set_options(self, **changes) -> None:
        """Options・RenderOptions の項目を名前で変える（例: set_options(melody="amt", simplify=True)）"""
        render_names = {f.name for f in fields(RenderOptions)}
        option_names = {f.name for f in fields(Options)} - set(_PER_PROJECT)
        unknown = set(changes) - render_names - option_names
        if unknown:
            raise TypeError(f"unknown option: {', '.join(sorted(unknown))}")

        def change(data: dict) -> None:
            d = data.setdefault("options", {})
            for k, v in changes.items():
                if k in render_names:
                    d.setdefault("render", {})[k] = v
                else:
                    d[k] = str(v) if isinstance(v, Path) else v

        self._update(change)

    # ------------------------------------------------------------ 曲情報
    @property
    def song(self) -> dict:
        with _lock(self.dir):
            return _read_json(self.dir / "song.json")

    @property
    def info(self) -> SongInfo:
        return SongInfo.from_dict(self.song["info"])

    def set_info(self, source: str = "manual", **values: str) -> None:
        """曲情報の項目を変える（例: set_info(title="曲名", artist="歌手")）"""
        unknown = set(values) - {f.name for f in fields(SongInfo)}
        if unknown:
            raise TypeError(f"unknown field: {', '.join(sorted(unknown))}")
        with _lock(self.dir):
            song = self.song
            for k, v in values.items():
                song["info"][k] = v
                song["sources"][k] = source
            _write_json(self.dir / "song.json", song)

    def _fill_info(self, info: SongInfo, source: str) -> None:
        """空の項目・ファイル名から推した項目を info で埋める（タグ・手入力の項目はそのまま）"""
        song = self.song
        values = {
            k: v
            for k, v in asdict(info).items()
            if v and (not song["info"].get(k) or song["sources"].get(k) == "filename")
        }
        if values:
            self.set_info(source, **values)

    # ------------------------------------------------------------ 歌詞
    @property
    def lyrics(self) -> str:
        return self.lyrics_path.read_text(encoding="utf-8") if self.lyrics_path.exists() else ""

    @property
    def lyrics_source(self) -> dict:
        path = self.dir / "lyrics.source.json"
        return _read_json(path) if path.exists() else {}

    def set_lyrics(self, text: str, source: dict | None = None) -> None:
        """使う歌詞を決める（空ならコード譜だけ）。source は出所（{"type": "manual" | "tag" | "site", ...}）"""
        text = text.strip()
        if text:
            self.lyrics_path.write_text(text + "\n", encoding="utf-8")
        else:
            self.lyrics_path.unlink(missing_ok=True)
        _write_json(self.dir / "lyrics.source.json", source or {"type": "manual"})

    def search_lyrics(self, sites: list[str] | None = None):
        """曲情報で歌詞サイトを検索して候補（SongHit）を返す。
        見つからなければ、ファイル名の別の読み方（曲名と歌手の入れ替え）、曲名だけ、の順に試す"""
        from .providers.lyrics import search

        info, song = self.info, self.song
        queries = [(info.title, info.artist)] + [tuple(q) for q in song.get("alternatives", [])] + [(info.title, "")]
        for title, artist in dict.fromkeys(q for q in queries if q[0]):
            hits = search(title, artist, sites)
            if hits:
                return hits
        return []

    def use_lyrics(self, hit):
        """検索結果（SongHit）か歌詞ページの URL から歌詞を取って使う。曲情報の空いている項目も埋める"""
        from .providers.lyrics import fetch

        page = fetch(hit)
        source = {k: v for k, v in page.to_dict().items() if k != "lyrics"}
        self.set_lyrics(page.lyrics, {"type": "site", **source})
        self._fill_info(page.info, f"site:{page.site}")
        return page

    # ------------------------------------------------------------ MIDI・コード
    def set_midi(self, path: str | Path) -> None:
        """手元の AMT の MIDI を使う（tsumugi を走らせない）"""
        self.midi_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, self.midi_path)
        self._record("midi", _digest(self._inputs("midi")), source="upload", file=Path(path).name)

    def midi_chords(self) -> list[tuple[float, str]]:
        """MIDI のコード (秒, コード名)（コードを手で直すときの元）"""
        return midi_chords(self.midi_path)

    def chords(self) -> list[tuple[float, str]] | None:
        """手で直したコード (秒, コード名)。直していなければ None（MIDI のコードを使う）"""
        if not self.chords_path.exists():
            return None
        return [(c["time"], c["label"]) for c in _read_json(self.chords_path)["chords"]]

    def save_chords(self, chords: list[tuple[float, str]]) -> None:
        self.chords_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.chords_path, {"chords": [{"time": float(t), "label": lab} for t, lab in sorted(chords)]})

    def discard_chord_edits(self) -> None:
        self.chords_path.unlink(missing_ok=True)

    def export_midi(self, out: str | Path | None = None) -> Path:
        """手で直したコードを書き込んだ MIDI を保存する（既定は output/<曲名>.mid）"""
        out = Path(out) if out else self.dir / "output" / f"{self.output_stem()}.mid"
        out.parent.mkdir(parents=True, exist_ok=True)
        chords = self.chords()
        if chords is None:
            shutil.copy(self.midi_path, out)
        else:
            write_chords_midi(self.midi_path, chords, out)
        return out

    # ------------------------------------------------------------ アライメント
    def alignment(self) -> Alignment | None:
        """ChordPro に使うアライメント（いまの歌詞から作った手直しがあればそれ、なければ自動のもの）"""
        path = self._alignment_file()
        return Alignment.from_dict(_read_json(path)) if path else None

    def save_alignment(self, alignment: Alignment | dict) -> None:
        """手で直したアライメントを保存する（いまの歌詞に対するものとして記録する）"""
        d = alignment.to_dict() if isinstance(alignment, Alignment) else alignment
        _write_json(self.edited_alignment_path, d)
        rec = {"lyrics": _text_sha1(self.lyrics), "at": _now()}
        self._update(lambda data: data["edits"].__setitem__("alignment", rec))

    def discard_alignment_edits(self) -> None:
        self.edited_alignment_path.unlink(missing_ok=True)
        self._update(lambda data: data["edits"].pop("alignment", None))

    @property
    def alignment_edits_outdated(self) -> bool:
        """手で直したアライメントがあるが、そのあと歌詞が変わった（自動のものを使っている）"""
        rec = self.data["edits"].get("alignment")
        return self.edited_alignment_path.exists() and (not rec or rec["lyrics"] != _text_sha1(self.lyrics))

    def _alignment_file(self) -> Path | None:
        if not self.lyrics.strip():
            return None
        if self.edited_alignment_path.exists() and not self.alignment_edits_outdated:
            return self.edited_alignment_path
        return self.auto_alignment_path if self.auto_alignment_path.exists() else None

    # ------------------------------------------------------------ 段階
    def status(self) -> dict[str, str]:
        out = {}
        for s in STAGES:
            inputs, rec = self._inputs(s), self.data["stages"].get(s)
            if inputs is None:
                out[s] = "skipped"
            elif rec is None or not self._has_output(s):
                out[s] = "pending"
            else:
                out[s] = "done" if rec["inputs"] == _digest(inputs) else "stale"
        return out

    def run(self, *stages: str, force: bool = False, progress=None) -> dict[str, str]:
        """段階を実行する（必要な前の段階も）。済んでいて入力が変わっていない段階は飛ばす。

        stages  : STAGES の名前か "analysis"（midi・melody・vocals）。省略時はすべて
        force   : 指定した段階は済んでいてもやり直す
        progress: progress(段階, "start" | "done") を呼ぶ"""
        requested = {x for s in (stages or STAGES) for x in (ANALYSIS if s == "analysis" else (s,))}
        unknown = requested - set(STAGES)
        if unknown:
            raise ValueError(f"unknown stage: {', '.join(sorted(unknown))}")
        wanted: set[str] = set()

        def add(s: str) -> None:
            if s not in wanted:
                wanted.add(s)
                for d in DEPS.get(s, ()):
                    add(d)

        for s in requested:
            add(s)
        for s in STAGES:
            if s not in wanted:
                continue
            state = self.status()[s]
            redo = force and s in requested
            if state == "skipped" or (state == "done" and not redo):
                continue
            log.info("[%s] %s", self.dir.name, s)
            if progress:
                progress(s, "start")
            # 指紋は始める前の入力で取る（実行中に歌詞・設定が変われば、終わったあと "stale" になる）
            inputs = _digest(self._inputs(s))
            extra = getattr(self, f"_run_{s}")(state == "stale" or redo) or {}
            self._record(s, inputs, **extra)
            if progress:
                progress(s, "done")
        return self.status()

    def chordpro(self) -> str:
        """出力した ChordPro（まだなら ""）"""
        path = self.output_path
        return path.read_text(encoding="utf-8") if path and path.exists() else ""

    # ---- 段階の中身（redo: 前の結果を消してから作り直す）。返す dict は project.json の記録に足す
    def _run_midi(self, redo: bool) -> dict:
        opt = self.options
        out = scratch_dir(opt) / "tsumugi" / "out" / self.audio.stem  # ステム（1曲 250 MB ほど）・楽器ごとの MIDI
        if redo:
            shutil.rmtree(out, ignore_errors=True)
        midi = make_midi(self.audio, opt)
        self.midi_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(midi, self.midi_path)
        shutil.rmtree(out, ignore_errors=True)  # 使うのはコード・拍の MIDI だけ
        return {"source": "tsumugi"}

    def _run_melody(self, redo: bool) -> None:
        opt = self.options
        if redo:
            shutil.rmtree(sheetsage_dir(self.audio, opt.cache_dir), ignore_errors=True)
        run_sheetsage(self.audio, opt.cache_dir, opt.sheetsage_model)

    def _run_vocals(self, redo: bool) -> None:
        opt = self.options
        if redo:
            shutil.rmtree(scratch_dir(opt) / "sep" / "htdemucs" / self.audio.stem, ignore_errors=True)
            ctc_path(self.audio, opt).unlink(missing_ok=True)
        vocal_features(self.audio, opt)

    def _run_align(self, redo: bool) -> None:
        opt = self.options
        tl, notes = prepare(self.audio, self.midi_path, opt)
        alignment = align_audio(self.audio, self.lyrics, tl, notes, opt)
        _write_json(self.auto_alignment_path, alignment.to_dict())

    def _run_render(self, redo: bool) -> dict:
        opt = self.options
        tl, notes = prepare(self.audio, self.midi_path, opt, chords=self.chords())
        cho = render_chordpro(tl, self.alignment(), notes, self.info, opt)
        old = self.output_path
        name = f"{self.output_stem()}.cho"
        (self.dir / "output").mkdir(exist_ok=True)
        (self.dir / "output" / name).write_text(cho, encoding="utf-8")
        if old and old.name != name:
            old.unlink(missing_ok=True)
        return {"output": name}

    def _record(self, stage: str, inputs: str, **extra) -> None:
        rec = {"inputs": inputs, "at": _now(), **extra}
        self._update(lambda data: data["stages"].__setitem__(stage, rec))

    def output_stem(self) -> str:
        """出力ファイルの名前（拡張子なし）。曲名、無ければ音源のファイル名"""
        return _safe_name(self.info.title or self.audio.stem)

    def _inputs(self, stage: str) -> dict | None:
        """段階の結果を左右する入力（指紋にする）。None = この段階は要らない"""
        opt = self.options
        audio = self.data["audio_sha1"]
        sheetsage = str(opt.sheetsage_model) if "sheetsage" in (opt.melody, opt.beats) else None
        midi = _file_sha1(self.midi_path) if self.midi_path.exists() else None
        if stage in ("midi", "vocals"):
            return {"audio": audio}
        if stage == "melody":
            return {"audio": audio, "model": sheetsage} if sheetsage else None
        if stage == "align":
            if not self.lyrics.strip():
                return None
            beats = {"melody": opt.melody, "beats": opt.beats, "prior": opt.melody_prior, "model": sheetsage}
            return {"lyrics": _text_sha1(self.lyrics), "midi": midi, **beats}
        if stage == "render":
            alignment = self._alignment_file()
            return {
                "midi": midi,
                "chords": _file_sha1(self.chords_path) if self.chords_path.exists() else None,
                "alignment": _file_sha1(alignment) if alignment else None,
                "song": _file_sha1(self.dir / "song.json"),
                "options": self.data.get("options", {}),
                "model": sheetsage,
            }
        raise ValueError(f"unknown stage: {stage}")

    def _has_output(self, stage: str) -> bool:
        opt = self.options
        if stage == "midi":
            return self.midi_path.exists()
        if stage == "melody":
            return has_sheetsage(self.audio, opt.cache_dir)
        if stage == "vocals":  # scratch の一時ファイルなので記録だけで見る（消えていればアライメントのときに作り直す）
            return True
        if stage == "align":
            return self.auto_alignment_path.exists()
        path = self.output_path
        return bool(path and path.exists())


def projects(
    root: str | Path, models_dir: str | Path | None = None, scratch_dir: str | Path | None = None
) -> list[Project]:
    """root にあるプロジェクト（新しい順）"""
    root = Path(root).resolve()
    dirs = [d for d in root.iterdir() if (d / "project.json").exists()] if root.is_dir() else []
    found = [Project(d, models_dir, scratch_dir) for d in dirs]
    return sorted(found, key=lambda p: p.data.get("created", ""), reverse=True)


# ================================================================== 下まわり
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(d: Path) -> threading.RLock:
    """プロジェクトごとのロック（同じプロセスのスレッドの間で、読み直し → 書き込みを1つずつにする）"""
    key = d.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    """書きかけのファイルを残さないよう、一時ファイルに書いてから置き換える。
    Windows では読んでいる最中のファイルを置き換えられないので、少し待ってやり直す"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _text_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _digest(obj) -> str:
    return _text_sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))


def _known(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def _options_dict(opt: Options) -> dict:
    d = {k: v for k, v in asdict(opt).items() if k not in _PER_PROJECT}
    d["sheetsage_model"] = str(d["sheetsage_model"])
    d["render"] = asdict(opt.render)
    return d


def _safe_name(name: str) -> str:
    """フォルダ名・ファイル名に使えない文字を置き換える"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:80] or "song"


def _unique_dir(root: Path, name: str) -> Path:
    d, i = root / name, 2
    while d.exists():
        d, i = root / f"{name} ({i})", i + 1
    return d
