// ② 作成：段階ごとの状態と、実行中のジョブの進み具合・ログ
import { useEffect, useRef } from "react";
import { type Job, type ProjectDetail, STAGES } from "../api/client";
import { useCancelJob, useRun, useUpdateOptions, useUploadMidi } from "../api/hooks";
import { Card, Dropzone, ErrorText, StateBadge } from "../components/ui";
import { activeJob, isFinished, STAGE_LABELS, stageState, stageView } from "../lib/project";

export function BuildStep({ project: p, onDone }: { project: ProjectDetail; onDone: () => void }) {
  const run = useRun(p.id);
  const job = activeJob(p) ?? p.jobs[0];
  const allDone = STAGES.every((s) => isFinished(stageState(p, s)));

  // ChordPro が書き直され、ジョブがすべて終わったら ③ へ
  const version = useRef(p.output_version);
  useEffect(() => {
    if (p.output_version && p.output_version !== version.current && !p.busy) onDone();
    if (!p.busy) version.current = p.output_version;
  }, [p.output_version, p.busy, onDone]);

  return (
    <div className="step-body">
      <Card
        title="段階"
        actions={
          <button
            className="button primary"
            disabled={p.busy || allDone || run.isPending}
            onClick={() => run.mutate({ stages: [] })}
          >
            {allDone ? "すべて済み" : "作成する"}
          </button>
        }
      >
        <ol className="stages">
          {STAGES.map((s) => (
            <li key={s} className="stage">
              <StateBadge state={stageView(p, s)} />
              <div>
                <div className="stage-name">{STAGE_LABELS[s].name}</div>
                <div className="muted small">{STAGE_LABELS[s].detail}</div>
              </div>
            </li>
          ))}
        </ol>
        {!p.lyrics.trim() && <p className="muted small">歌詞がないので、位置合わせはせずコードだけの譜面を作ります。</p>}
        <ErrorText error={run.error} />
      </Card>
      {job && <JobCard projectId={p.id} job={job} />}
      <AdvancedCard project={p} />
    </div>
  );
}

const JOB_STATES: Record<Job["state"], string> = {
  queued: "待っています",
  running: "実行しています",
  done: "終わりました",
  error: "失敗しました",
  cancelled: "取り消しました",
};

function JobCard({ projectId, job }: { projectId: string; job: Job }) {
  const cancel = useCancelJob(projectId);
  const log = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [job.log.length]);
  return (
    <Card
      title={`ジョブ：${JOB_STATES[job.state]}`}
      actions={
        job.state === "queued" && (
          <button className="button small" disabled={cancel.isPending} onClick={() => cancel.mutate(job.id)}>
            取り消す
          </button>
        )
      }
    >
      {job.error && <p className="error">{job.error}</p>}
      {job.log.length > 0 ? (
        <pre ref={log} className="log">
          {job.log.join("\n")}
        </pre>
      ) : (
        <p className="muted small">ログはまだありません。</p>
      )}
      <ErrorText error={cancel.error} />
    </Card>
  );
}

/** 解析の設定と、手元の MIDI を使う */
function AdvancedCard({ project: p }: { project: ProjectDetail }) {
  const update = useUpdateOptions(p.id);
  const midi = useUploadMidi(p.id);
  return (
    <details className="card advanced">
      <summary>詳しい設定</summary>
      <div className="form">
        <label className="field">
          <span className="field-label">歌メロ</span>
          <select
            value={p.options.melody}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ melody: e.target.value as "sheetsage" | "amt" })}
          >
            <option value="sheetsage">SheetSage2 で採譜（非商用ライセンス）</option>
            <option value="amt">MIDI の melody トラック</option>
          </select>
        </label>
        <label className="field">
          <span className="field-label">拍</span>
          <select
            value={p.options.beats}
            disabled={update.isPending}
            onChange={(e) => update.mutate({ beats: e.target.value as "amt" | "sheetsage" })}
          >
            <option value="amt">MIDI のテンポマップ</option>
            <option value="sheetsage">SheetSage2 のビート（変拍子向き）</option>
          </select>
        </label>
      </div>
      <ErrorText error={update.error} />
      <h3 className="subhead">手元の AMT の MIDI を使う</h3>
      <p className="muted small">
        {p.midi_source === "upload"
          ? "アップロードした MIDI を使っています。"
          : "コード・拍の採譜（tsumugi）の代わりに、手元の MIDI を使います。"}
      </p>
      <Dropzone
        accept=".mid,.midi"
        title={midi.isPending ? "アップロードしています…" : "MIDI をここにドロップ"}
        busy={midi.isPending || p.busy}
        onFile={(file) => midi.mutate(file)}
      />
      {p.busy && <p className="muted small">ジョブが終わってから入れられます。</p>}
      <ErrorText error={midi.error} />
    </details>
  );
}
