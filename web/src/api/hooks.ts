// サーバの状態の読み書き（TanStack Query）。ジョブが動いている間は問い合わせ続ける
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  fetchChordPro,
  type InfoPatch,
  type OptionsPatch,
  type ProjectDetail,
  type RunIn,
  unwrap,
  uploadAudio,
  uploadMidi,
} from "./client";

const POLL_MS = 1000;

export const keys = {
  projects: ["projects"] as const,
  project: (id: string) => ["project", id] as const,
  chordpro: (id: string, version: string | null | undefined) => ["chordpro", id, version] as const,
};

const path = (pid: string) => ({ params: { path: { pid } } });

export function useProjects() {
  return useQuery({
    queryKey: keys.projects,
    queryFn: () => unwrap(api.GET("/api/projects")),
    refetchInterval: (q) => (q.state.data?.some((p) => p.busy) ? POLL_MS * 2 : false),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: keys.project(id),
    queryFn: () => unwrap(api.GET("/api/projects/{pid}", path(id))),
    refetchInterval: (q) => (q.state.data?.busy ? POLL_MS : false),
  });
}

/** ChordPro の本文。version（output_version）が変わったら読み直す */
export function useChordPro(id: string, version: string | null | undefined) {
  return useQuery({
    queryKey: keys.chordpro(id, version),
    queryFn: () => fetchChordPro(id),
    enabled: Boolean(version),
    placeholderData: (prev) => prev,
  });
}

export function useUploadAudio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadAudio(file),
    onSuccess: (p) => {
      qc.setQueryData(keys.project(p.id), p);
      qc.invalidateQueries({ queryKey: keys.projects });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/api/projects/{pid}", path(id))),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.projects }),
  });
}

/** プロジェクトを変える操作。返ってきた最新のプロジェクトで画面を置き換える */
function useProjectMutation<A>(id: string, fn: (arg: A) => Promise<ProjectDetail>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (p) => {
      qc.setQueryData(keys.project(id), p);
      qc.invalidateQueries({ queryKey: keys.projects });
    },
  });
}

export function useUpdateInfo(id: string) {
  return useProjectMutation(id, (body: InfoPatch) => unwrap(api.PATCH("/api/projects/{pid}/info", { ...path(id), body })));
}

export function useUpdateOptions(id: string) {
  return useProjectMutation(id, (body: OptionsPatch) =>
    unwrap(api.PATCH("/api/projects/{pid}/options", { ...path(id), body })),
  );
}

export function useSetLyrics(id: string) {
  return useProjectMutation(id, (text: string) =>
    unwrap(api.PUT("/api/projects/{pid}/lyrics", { ...path(id), body: { text } })),
  );
}

export function useUseLyrics(id: string) {
  return useProjectMutation(id, (url: string) =>
    unwrap(api.POST("/api/projects/{pid}/lyrics/use", { ...path(id), body: { url } })),
  );
}

export function useUploadMidi(id: string) {
  return useProjectMutation(id, (file: File) => uploadMidi(id, file));
}

export function useSearchLyrics(id: string) {
  return useMutation({
    mutationFn: (q: { title?: string; artist?: string }) =>
      unwrap(api.GET("/api/projects/{pid}/lyrics/search", { params: { path: { pid: id }, query: q } })),
  });
}

/** 段階を実行する（ジョブ）。プロジェクトを読み直して、終わるまで問い合わせ続ける */
export function useRun(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<RunIn>) =>
      unwrap(api.POST("/api/projects/{pid}/run", { ...path(id), body: { stages: [], force: false, ...body } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.project(id) }),
  });
}

export function useCancelJob(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => unwrap(api.DELETE("/api/jobs/{job_id}", { params: { path: { job_id: jobId } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.project(id) }),
  });
}
