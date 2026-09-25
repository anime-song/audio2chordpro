// プロジェクトの表示に使う名前と、状態の読み方
import { type Job, type ProjectDetail, type ProjectSummary, STAGES, type Stage, type StageState } from "../api/client";

export const STAGE_LABELS: Record<Stage, { name: string; detail: string }> = {
  midi: { name: "コード・拍の採譜", detail: "tsumugi：ステム分離 → 採譜 → ビート・コード・調の推定" },
  melody: { name: "歌メロの採譜", detail: "SheetSage2" },
  vocals: { name: "ボーカル分離", detail: "Demucs で歌声を取り出し、発音を認識（CTC）" },
  align: { name: "歌詞の位置合わせ", detail: "歌詞の一音一音（モーラ）が歌われる時刻を求める" },
  render: { name: "コード譜の作成", detail: "コードを歌詞の上に置いて ChordPro を書く" },
};

export type StageView = StageState | "running" | "queued";

export const STATE_LABELS: Record<StageView, string> = {
  done: "済み",
  stale: "やり直しが必要",
  pending: "未実行",
  skipped: "不要",
  running: "実行中",
  queued: "待ち",
};

const SITE_LABELS: Record<string, string> = { utaten: "うたてん" };

/** 曲情報の出所（tag / filename / site:<サイト> / manual）の表示名 */
export function sourceLabel(source: string | undefined): string {
  if (!source) return "";
  if (source.startsWith("site:")) return SITE_LABELS[source.slice(5)] ?? source.slice(5);
  return { tag: "タグ", filename: "ファイル名", manual: "手入力" }[source] ?? source;
}

export function siteLabel(site: string): string {
  return SITE_LABELS[site] ?? site;
}

/** 実行中のジョブ（なければ待っているジョブ） */
export function activeJob(p: ProjectDetail): Job | undefined {
  return p.jobs.find((j) => j.state === "running") ?? p.jobs.find((j) => j.state === "queued");
}

export function stageState(p: ProjectSummary, stage: Stage): StageState {
  return (p.status[stage] ?? "pending") as StageState;
}

/** 段階の表示上の状態（実行中のジョブがその段階を実行している・これから実行するなら running / queued） */
export function stageView(p: ProjectDetail, stage: Stage): StageView {
  const state = stageState(p, stage);
  const job = activeJob(p);
  if (!job) return state;
  if (job.stage === stage) return "running";
  if ((state === "pending" || state === "stale") && !job.finished_stages.includes(stage)) {
    const all = job.stages.length === 0;
    const analysis = job.stages.includes("analysis") && ["midi", "melody", "vocals"].includes(stage);
    if (all || analysis || job.stages.includes(stage) || stageBefore(stage, job.stages)) return "queued";
  }
  return state;
}

/** stage が、指定した段階（の前提）として実行されるか */
function stageBefore(stage: Stage, requested: string[]): boolean {
  const last = Math.max(...requested.map((s) => STAGES.indexOf(s as Stage)));
  return STAGES.indexOf(stage) < last;
}

export function isFinished(state: StageState): boolean {
  return state === "done" || state === "skipped";
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("ja-JP", { dateStyle: "medium", timeStyle: "short" });
}
