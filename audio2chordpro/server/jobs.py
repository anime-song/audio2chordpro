"""段階の実行（ジョブ）を1本の列で順に流す。GPU を使う処理どうしが取り合わないようにする。
UI はジョブの状態（待ち・実行中の段階・終わった段階・ログ）を問い合わせて進み具合を出す。
ジョブはメモリにだけ置く（サーバを止めると消える。段階の結果は各プロジェクトに残る）"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial

from ..project import Project

log = logging.getLogger(__name__)

MAX_LOG_LINES = 500  # ジョブ1つに残すログの行数
KEEP_FINISHED = 200  # 終わったジョブを覚えておく数


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    project: str  # プロジェクトのフォルダ名
    stages: list[str]  # Project.run に渡す段階（空 = すべて）
    force: bool = False
    state: str = "queued"  # queued | running | done | error | cancelled
    stage: str | None = None  # 実行中の段階
    finished_stages: list[str] = field(default_factory=list)
    error: str | None = None
    log: list[str] = field(default_factory=list)
    created: str = field(default_factory=_now)
    started: str | None = None
    finished: str | None = None
    _ended: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    def wait(self, timeout: float | None = None) -> bool:
        """終わる（done・error・cancelled）まで待つ"""
        return self._ended.wait(timeout)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def _end(self, state: str) -> None:
        self.state, self.stage, self.finished = state, None, _now()
        self._ended.set()


class _JobLog(logging.Handler):
    """ワーカーのスレッドで出た audio2chordpro のログを、実行中のジョブに溜める"""

    def __init__(self, runner: Runner):
        super().__init__(logging.INFO)
        self.runner = runner
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        job = self.runner.current
        if job is not None and record.thread == self.runner.thread_id:
            job.log.append(self.format(record))
            del job.log[:-MAX_LOG_LINES]


class Runner:
    """open_project(フォルダ名) → Project でプロジェクトを開いて、ジョブを1つずつ実行する"""

    def __init__(self, open_project: Callable[[str], Project]):
        self._open = open_project
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self.current: Job | None = None
        self._thread = threading.Thread(target=self._work, name="audio2chordpro-runner", daemon=True)
        self._thread.start()
        self.thread_id = self._thread.ident
        self._handler = _JobLog(self)
        pkg = logging.getLogger("audio2chordpro")
        if pkg.level == logging.NOTSET or pkg.level > logging.INFO:
            pkg.setLevel(logging.INFO)
        pkg.addHandler(self._handler)

    def close(self) -> None:
        """待っているジョブを取り消し、実行中のものが終わったらワーカーを止める"""
        with self._lock:
            for job in self._jobs.values():
                if job.state == "queued":
                    job._end("cancelled")
        self._queue.put(None)
        self._thread.join()
        logging.getLogger("audio2chordpro").removeHandler(self._handler)

    # ------------------------------------------------------------ ジョブ
    def submit(self, project: str, stages: list[str] | tuple[str, ...] = (), force: bool = False) -> Job:
        """ジョブを列に足す。同じプロジェクト・同じ中身のジョブが待っていればそれを返す"""
        stages = list(stages)
        with self._lock:
            for job in self._jobs.values():
                if job.state == "queued" and (job.project, job.stages, job.force) == (project, stages, force):
                    return job
            job = Job(uuid.uuid4().hex[:12], project, stages, force)
            self._jobs[job.id] = job
            finished = [j.id for j in self._jobs.values() if not j.active]
            for jid in finished[: max(len(finished) - KEEP_FINISHED, 0)]:
                del self._jobs[jid]
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def jobs(self, project: str | None = None) -> list[Job]:
        """ジョブ（新しい順）"""
        with self._lock:
            jobs = [j for j in self._jobs.values() if project is None or j.project == project]
        return jobs[::-1]

    def busy(self, project: str) -> bool:
        return any(j.active for j in self.jobs(project))

    def cancel(self, job_id: str) -> bool:
        """待っているジョブを取り消す（実行中のものは止められない）"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != "queued":
                return False
            job._end("cancelled")
            return True

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            job = self._jobs.get(job_id)
            if job is None or job.state != "queued":
                continue
            self.current = job
            job.state, job.started = "running", _now()
            try:
                self._open(job.project).run(*job.stages, force=job.force, progress=partial(self._progress, job))
            except Exception as e:  # noqa: BLE001  失敗はジョブに書いて、次のジョブに進む
                log.error("%s", traceback.format_exc().rstrip())
                job.error = f"{type(e).__name__}: {e}"
                job._end("error")
            else:
                job._end("done")
            finally:
                self.current = None

    @staticmethod
    def _progress(job: Job, stage: str, event: str) -> None:
        if event == "start":
            job.stage = stage
        else:
            job.finished_stages.append(stage)
            job.stage = None
