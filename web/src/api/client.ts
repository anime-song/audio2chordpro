// API（audio2chordpro/server/app.py）の呼び出し。型は schema.ts（npm run gen:api で作る）から
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type ProjectSummary = Schemas["ProjectSummary"];
export type ProjectDetail = Schemas["ProjectDetail"];
export type Job = Schemas["JobModel"];
export type SongInfo = Schemas["SongInfoModel"];
export type InfoPatch = Schemas["InfoPatch"];
export type Options = Schemas["OptionsModel"];
export type OptionsPatch = Schemas["OptionsPatch"];
export type LyricsHit = Schemas["LyricsHit"];
export type RunIn = Schemas["RunIn"];

export const STAGES = ["midi", "melody", "vocals", "align", "render"] as const;
export type Stage = (typeof STAGES)[number];
export type StageState = "done" | "stale" | "pending" | "skipped";
export const ANALYSIS: readonly Stage[] = ["midi", "melody", "vocals"];

export const AUDIO_ACCEPT = ".mp3,.wav,.flac,.m4a,.ogg";

export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** FastAPI のエラー（{detail: "…"} か、入力の検証エラーの配列）を文にする */
function message(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? String(d)).join("、");
  return `エラーが起きました（${status}）`;
}

/** openapi-fetch の結果からデータを取り出す（エラーなら ApiError を投げる） */
export async function unwrap<T>(req: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await req;
  if (error !== undefined || !response.ok) throw new ApiError(message(error, response.status), response.status);
  return data as T;
}

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new ApiError(message(body, r.status), r.status);
  return body as T;
}

/** プロジェクトの下のファイルの URL（audio / midi / chordpro） */
export function fileUrl(id: string, file: "audio" | "midi" | "chordpro", query = ""): string {
  return `/api/projects/${encodeURIComponent(id)}/${file}${query ? `?${query}` : ""}`;
}

/** 音源からプロジェクトを作る（analyze なら解析もすぐ始める） */
export function uploadAudio(file: File, analyze = true): Promise<ProjectDetail> {
  const form = new FormData();
  form.append("audio", file);
  form.append("analyze", String(analyze));
  return send("/api/projects", { method: "POST", body: form });
}

/** 手元の AMT の MIDI を使う */
export function uploadMidi(id: string, file: File): Promise<ProjectDetail> {
  const form = new FormData();
  form.append("midi", file);
  return send(fileUrl(id, "midi"), { method: "PUT", body: form });
}

export function fetchChordPro(id: string): Promise<string> {
  return send(fileUrl(id, "chordpro"), {});
}
