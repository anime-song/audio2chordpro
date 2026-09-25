// 1曲の画面：① 曲情報・歌詞 → ② 作成 → ③ ChordPro。解析の進み具合は上にいつも出す
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ANALYSIS, ApiError, fileUrl, type ProjectDetail, STAGES } from "../api/client";
import { useProject } from "../api/hooks";
import { ErrorText, Spinner } from "../components/ui";
import { activeJob, isFinished, STAGE_LABELS, stageState, stageView } from "../lib/project";
import { BuildStep } from "../steps/BuildStep";
import { LyricsStep } from "../steps/LyricsStep";
import { ResultStep } from "../steps/ResultStep";

type StepKey = "lyrics" | "build" | "result";

export function ProjectPage() {
  const { id = "" } = useParams();
  const project = useProject(id);
  if (project.isPending) return <main className="page muted">読み込んでいます…</main>;
  if (project.error) {
    return (
      <main className="page">
        <Link to="/">← プロジェクト一覧</Link>
        {project.error instanceof ApiError && project.error.status === 404 ? (
          <p>プロジェクトが見つかりません。</p>
        ) : (
          <ErrorText error={project.error} />
        )}
      </main>
    );
  }
  return <ProjectView key={id} project={project.data} />;
}

function firstStep(p: ProjectDetail): StepKey {
  if (p.output) return "result";
  const job = activeJob(p);
  if (job && !(job.stages.length === 1 && job.stages[0] === "analysis")) return "build";
  return "lyrics";
}

const STEP_KEYS: StepKey[] = ["lyrics", "build", "result"];

function ProjectView({ project: p }: { project: ProjectDetail }) {
  // 手順は URL（?step=）に置く（ブラウザの「戻る」で前の手順へ）。無ければ開いたときの状態から決める
  const [params, setParams] = useSearchParams();
  const [initial] = useState(() => firstStep(p));
  const asked = params.get("step") as StepKey | null;
  const step = asked && STEP_KEYS.includes(asked) ? asked : initial;
  const setStep = (s: StepKey) => setParams({ step: s });
  const steps: { key: StepKey; label: string; done: boolean }[] = [
    { key: "lyrics", label: "曲情報・歌詞", done: Boolean(p.lyrics.trim()) },
    { key: "build", label: "作成", done: STAGES.every((s) => isFinished(stageState(p, s))) },
    { key: "result", label: "コード譜", done: Boolean(p.output) },
  ];
  return (
    <main className="page">
      <header className="project-head">
        <Link to="/" className="back">
          ← プロジェクト一覧
        </Link>
        <div className="project-title">
          <h1>{p.info.title || p.id}</h1>
          {p.info.artist && <div className="muted">{p.info.artist}</div>}
        </div>
        <audio className="player" controls preload="none" src={fileUrl(p.id, "audio")} />
        <AnalysisStatus project={p} />
      </header>

      <nav className="stepper" aria-label="手順">
        {steps.map((s, i) => (
          <button
            key={s.key}
            className={`step${s.key === step ? " current" : ""}${s.done ? " done" : ""}`}
            aria-current={s.key === step ? "step" : undefined}
            onClick={() => setStep(s.key)}
          >
            <span className="step-no">{s.done ? "✓" : i + 1}</span>
            {s.label}
          </button>
        ))}
      </nav>

      {step === "lyrics" && <LyricsStep project={p} onBuild={() => setStep("build")} />}
      {step === "build" && <BuildStep project={p} onDone={() => setStep("result")} />}
      {step === "result" && <ResultStep project={p} onBuild={() => setStep("build")} />}
    </main>
  );
}

/** 音源の解析（歌詞の要らない重い段階）の進み具合 */
function AnalysisStatus({ project: p }: { project: ProjectDetail }) {
  const views = ANALYSIS.map((s) => [s, stageView(p, s)] as const);
  const running = views.find(([, v]) => v === "running");
  const failed = p.jobs[0]?.state === "error";
  let text: string;
  if (running) text = `解析中：${STAGE_LABELS[running[0]].name}`;
  else if (views.every(([, v]) => v === "done" || v === "skipped")) text = "音源の解析：済み";
  else if (views.some(([, v]) => v === "queued")) text = "音源の解析：待ち";
  else text = "音源の解析：未実行";
  return (
    <div className={`analysis${running ? " running" : ""}`}>
      {running && <Spinner />}
      <span>{text}</span>
      <span className="analysis-dots">
        {views.map(([s, v]) => (
          <span key={s} className={`dot dot-${v}`} title={STAGE_LABELS[s].name} />
        ))}
      </span>
      {failed && <span className="error small">失敗しました（「作成」に詳細）</span>}
    </div>
  );
}
