"""UI の API（FastAPI）。中身は Project を呼ぶだけで、重い段階は Runner の列に流す。

  プロジェクト : GET/POST /api/projects、GET/DELETE /api/projects/{id}
  曲情報・設定 : PATCH /api/projects/{id}/info、PATCH /api/projects/{id}/options
  歌詞         : PUT /api/projects/{id}/lyrics、GET …/lyrics/search、POST …/lyrics/use
  実行         : POST /api/projects/{id}/run → ジョブ。GET /api/jobs/{id} で進み具合
  ファイル     : GET …/chordpro、…/audio（Range 対応）、…/midi、PUT …/midi（手元の MIDI を使う）

{id} はプロジェクトのフォルダ名。web/ のビルド（server/static/）があれば / で配る（npm run build）。
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from ..project import Project, projects
from .jobs import Runner
from .web import STATIC_DIR

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".ogg")

StageName = Literal["midi", "melody", "vocals", "align", "render"]
StageState = Literal["done", "stale", "pending", "skipped"]


# ================================================================== データの形
class SongInfoModel(BaseModel):
    title: str = ""
    artist: str = ""
    lyricist: str = ""
    composer: str = ""
    arranger: str = ""


class InfoPatch(BaseModel):
    title: str | None = None
    artist: str | None = None
    lyricist: str | None = None
    composer: str | None = None
    arranger: str | None = None


class OptionsModel(BaseModel):
    """Options と RenderOptions の項目（Project.set_options の名前）"""

    melody: Literal["sheetsage", "amt"]
    beats: Literal["amt", "sheetsage"]
    sheetsage_model: str
    melody_prior: bool
    snap_to_notes: bool
    simplify: bool
    head_outside: bool
    tail_grid: bool
    min_gap_bars: int
    tol: float
    head_window: float


class OptionsPatch(BaseModel):
    melody: Literal["sheetsage", "amt"] | None = None
    beats: Literal["amt", "sheetsage"] | None = None
    sheetsage_model: str | None = None
    melody_prior: bool | None = None
    snap_to_notes: bool | None = None
    simplify: bool | None = None
    head_outside: bool | None = None
    tail_grid: bool | None = None
    min_gap_bars: int | None = None
    tol: float | None = None
    head_window: float | None = None


class JobModel(BaseModel):
    id: str
    project: str
    stages: list[str]
    force: bool
    state: Literal["queued", "running", "done", "error", "cancelled"]
    stage: str | None
    finished_stages: list[str]
    error: str | None
    log: list[str]
    created: str
    started: str | None
    finished: str | None


class EditsModel(BaseModel):
    chords: bool  # コードを手で直した
    alignment: bool  # アライメントを手で直した
    alignment_outdated: bool  # 手で直したアライメントのあとに歌詞が変わった（使っていない）


class ProjectSummary(BaseModel):
    id: str
    created: str
    info: SongInfoModel
    status: dict[StageName, StageState]
    busy: bool  # 待っている・実行中のジョブがある


class ProjectDetail(ProjectSummary):
    audio: str  # 音源のファイル名
    sources: dict[str, str]  # 曲情報の各項目の出所（tag / filename / site:<サイト> / manual）
    alternatives: list[list[str]]  # ファイル名の別の読み方 [曲名, 歌手]
    lyrics: str
    lyrics_source: dict
    options: OptionsModel
    midi_source: str | None  # "tsumugi" | "upload"
    output: str | None  # ChordPro のファイル名
    output_version: str | None  # ChordPro を書き直すたびに変わる（画面が読み直す目印）
    edits: EditsModel
    jobs: list[JobModel]  # このプロジェクトのジョブ（新しい順）


class LyricsIn(BaseModel):
    text: str


class LyricsHit(BaseModel):
    site: str
    title: str
    artist: str
    url: str
    lyricist: str = ""
    composer: str = ""
    arranger: str = ""
    beginning: str = ""


class UseLyricsIn(BaseModel):
    url: str  # 歌詞ページ（検索結果の url）


class RunIn(BaseModel):
    stages: list[StageName | Literal["analysis"]] = []  # 空 = すべて
    force: bool = False


# ================================================================== アプリ
def create_app(root: str | Path = "projects", models_dir: str | Path | None = None) -> FastAPI:
    # 絶対パスにしておく（tsumugi の実行中はプロセスのカレントフォルダが変わる）
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    models_dir = Path(models_dir).resolve() if models_dir else None

    def open_project(pid: str) -> Project:
        d = root / pid
        if pid in ("", ".", "..") or Path(pid).name != pid or not (d / "project.json").is_file():
            raise HTTPException(404, f"プロジェクトがありません: {pid}")
        return Project(d, models_dir)

    runner = Runner(open_project)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        runner.close()

    app = FastAPI(title="audio2chordpro", lifespan=lifespan)
    app.state.runner = runner

    def summary(p: Project) -> dict:
        return {
            "id": p.dir.name,
            "created": p.data.get("created", ""),
            "info": p.song["info"],
            "status": p.status(),
            "busy": runner.busy(p.dir.name),
        }

    def detail(p: Project) -> ProjectDetail:
        data, song, opt = p.data, p.song, p.options
        options = {k: getattr(opt, k) for k in OptionsModel.model_fields if hasattr(opt, k)}
        options |= {k: getattr(opt.render, k) for k in OptionsModel.model_fields if hasattr(opt.render, k)}
        output = p.output_path
        return ProjectDetail(
            **summary(p),
            audio=p.audio.name,
            sources=song.get("sources", {}),
            alternatives=[list(a) for a in song.get("alternatives", [])],
            lyrics=p.lyrics,
            lyrics_source=p.lyrics_source,
            options=OptionsModel(**{**options, "sheetsage_model": str(opt.sheetsage_model)}),
            midi_source=data["stages"].get("midi", {}).get("source"),
            output=output.name if output and output.exists() else None,
            output_version=data["stages"].get("render", {}).get("inputs") if output and output.exists() else None,
            edits=EditsModel(
                chords=p.chords_path.exists(),
                alignment=p.edited_alignment_path.exists(),
                alignment_outdated=p.alignment_edits_outdated,
            ),
            jobs=[j.to_dict() for j in runner.jobs(p.dir.name)],
        )

    def idle(pid: str) -> None:
        if runner.busy(pid):
            raise HTTPException(409, "実行中のジョブがあります")

    def save_upload(upload: UploadFile, exts: tuple[str, ...]) -> Path:
        name = Path(upload.filename or "").name
        if Path(name).suffix.lower() not in exts:
            raise HTTPException(400, f"対応していないファイルです（{' / '.join(exts)}）: {name}")
        path = root / ".uploads" / uuid.uuid4().hex / name
        path.parent.mkdir(parents=True)
        with open(path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        return path

    # ------------------------------------------------------------ プロジェクト
    @app.get("/api/projects", response_model=list[ProjectSummary])
    def list_projects():
        return [summary(p) for p in projects(root, models_dir)]

    @app.post("/api/projects", response_model=ProjectDetail)
    def create_project(audio: Annotated[UploadFile, File()], analyze: Annotated[bool, Form()] = True):
        """音源からプロジェクトを作る（同じ音源のプロジェクトがあればそれ）。analyze なら解析のジョブも始める"""
        path = save_upload(audio, AUDIO_EXTS)
        try:
            p = Project.create(root, path, models_dir)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        if analyze:
            runner.submit(p.dir.name, ["analysis"])
        return detail(p)

    @app.get("/api/projects/{pid}", response_model=ProjectDetail)
    def get_project(pid: str):
        return detail(open_project(pid))

    @app.delete("/api/projects/{pid}", status_code=204)
    def delete_project(pid: str):
        p = open_project(pid)
        idle(pid)
        shutil.rmtree(p.dir)

    @app.patch("/api/projects/{pid}/info", response_model=ProjectDetail)
    def update_info(pid: str, body: InfoPatch):
        p = open_project(pid)
        p.set_info(**body.model_dump(exclude_none=True))
        return detail(p)

    @app.patch("/api/projects/{pid}/options", response_model=ProjectDetail)
    def update_options(pid: str, body: OptionsPatch):
        p = open_project(pid)
        p.set_options(**body.model_dump(exclude_none=True))
        return detail(p)

    # ------------------------------------------------------------ 歌詞
    @app.put("/api/projects/{pid}/lyrics", response_model=ProjectDetail)
    def set_lyrics(pid: str, body: LyricsIn):
        p = open_project(pid)
        p.set_lyrics(body.text)
        return detail(p)

    @app.get("/api/projects/{pid}/lyrics/search", response_model=list[LyricsHit])
    def search_lyrics(pid: str, title: str | None = None, artist: str = ""):
        """歌詞サイトを検索する。title を省けば曲情報から（Project.search_lyrics）"""
        from ..providers.lyrics import search

        p = open_project(pid)
        hits = search(title, artist) if title else p.search_lyrics()
        return [h.to_dict() for h in hits]

    @app.post("/api/projects/{pid}/lyrics/use", response_model=ProjectDetail)
    def use_lyrics(pid: str, body: UseLyricsIn):
        """歌詞ページから歌詞を取って使う（曲情報の空いている項目も埋める）"""
        import requests

        from ..providers.lyrics import SiteBlocked

        p = open_project(pid)
        try:
            p.use_lyrics(body.url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except (SiteBlocked, requests.RequestException) as e:
            raise HTTPException(502, f"歌詞を取得できませんでした: {e}") from e
        return detail(p)

    # ------------------------------------------------------------ 実行
    @app.post("/api/projects/{pid}/run", response_model=JobModel)
    def run(pid: str, body: RunIn):
        open_project(pid)
        return runner.submit(pid, body.stages, body.force).to_dict()

    @app.get("/api/jobs", response_model=list[JobModel])
    def list_jobs(project: str | None = None):
        return [j.to_dict() for j in runner.jobs(project)]

    @app.get("/api/jobs/{job_id}", response_model=JobModel)
    def get_job(job_id: str):
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(404, f"ジョブがありません: {job_id}")
        return job.to_dict()

    @app.delete("/api/jobs/{job_id}", response_model=JobModel)
    def cancel_job(job_id: str):
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(404, f"ジョブがありません: {job_id}")
        if not runner.cancel(job_id) and job.state == "running":
            raise HTTPException(409, "実行中のジョブは止められません")
        return job.to_dict()

    # ------------------------------------------------------------ ファイル
    @app.get("/api/projects/{pid}/chordpro", response_class=PlainTextResponse)
    def get_chordpro(pid: str, download: bool = False):
        p = open_project(pid)
        path = p.output_path
        if not path or not path.exists():
            raise HTTPException(404, "ChordPro はまだありません")
        headers = {"Content-Disposition": _attachment(path.name)} if download else None
        return PlainTextResponse(path.read_text(encoding="utf-8"), headers=headers)

    @app.get("/api/projects/{pid}/audio")
    def get_audio(pid: str):
        return FileResponse(open_project(pid).audio)

    @app.get("/api/projects/{pid}/midi")
    def get_midi(pid: str, edited: bool = True):
        """MIDI（edited なら手で直したコードを書き込んだもの）"""
        p = open_project(pid)
        if not p.midi_path.exists():
            raise HTTPException(404, "MIDI はまだありません")
        path = p.export_midi() if edited else p.midi_path
        return FileResponse(path, media_type="audio/midi", filename=f"{p.output_stem()}.mid")

    @app.put("/api/projects/{pid}/midi", response_model=ProjectDetail)
    def upload_midi(pid: str, midi: Annotated[UploadFile, File()]):
        """手元の AMT の MIDI を使う（tsumugi を走らせない）"""
        p = open_project(pid)
        idle(pid)
        path = save_upload(midi, (".mid", ".midi"))
        try:
            p.set_midi(path)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        return detail(p)

    # ------------------------------------------------------------ 画面
    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str):
        """web/ のビルドを配る。ファイルでないパス（/p/<id> など、画面の中の行き先）には index.html を返す"""
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        if not (STATIC_DIR / "index.html").exists():  # web/ をまだビルドしていなければ API の説明へ
            return RedirectResponse("/docs")
        file = (STATIC_DIR / path).resolve()
        if path and file.is_file() and file.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _attachment(name: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(name)}"
