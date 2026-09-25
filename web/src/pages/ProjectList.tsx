// はじめの画面：音源を入れてプロジェクトを作る・これまでのプロジェクトを開く
import { Link, useNavigate } from "react-router";
import { AUDIO_ACCEPT, type ProjectSummary, STAGES } from "../api/client";
import { useDeleteProject, useProjects, useUploadAudio } from "../api/hooks";
import { Dropzone, ErrorText, Spinner } from "../components/ui";
import { formatDate, STAGE_LABELS, STATE_LABELS, stageState } from "../lib/project";

export function ProjectList() {
  const projects = useProjects();
  const upload = useUploadAudio();
  const navigate = useNavigate();

  return (
    <main className="page">
      <section className="intro">
        <h1>音源からコード譜を作る</h1>
        <p className="muted">
          音源を入れると、コード・拍の採譜と歌声の解析がすぐに始まります。その間に曲情報と歌詞を決めてください。
        </p>
        <Dropzone
          accept={AUDIO_ACCEPT}
          title={upload.isPending ? "アップロードしています…" : "音源をここにドロップ"}
          hint="mp3 / wav / flac / m4a / ogg。クリックして選ぶこともできます"
          busy={upload.isPending}
          onFile={(file) =>
            upload.mutate(file, { onSuccess: (p) => navigate(`/p/${encodeURIComponent(p.id)}`) })
          }
        />
        <ErrorText error={upload.error} />
      </section>

      <section>
        <h2 className="section-title">プロジェクト</h2>
        {projects.isPending && <p className="muted">読み込んでいます…</p>}
        <ErrorText error={projects.error} />
        {projects.data?.length === 0 && <p className="muted">まだありません。</p>}
        <ul className="project-list">
          {projects.data?.map((p) => (
            <ProjectRow key={p.id} project={p} />
          ))}
        </ul>
      </section>
    </main>
  );
}

function ProjectRow({ project: p }: { project: ProjectSummary }) {
  const remove = useDeleteProject();
  const done = STAGES.filter((s) => stageState(p, s) === "done" || stageState(p, s) === "skipped").length;
  return (
    <li className="project-row">
      <Link to={`/p/${encodeURIComponent(p.id)}`} className="project-link">
        <div className="project-name">{p.info.title || p.id}</div>
        <div className="muted small">
          {p.info.artist && <span>{p.info.artist}　</span>}
          {formatDate(p.created)}
        </div>
      </Link>
      <div className="project-progress" title={`${done} / ${STAGES.length} 段階`}>
        {p.busy && <Spinner />}
        {STAGES.map((s) => (
          <span
            key={s}
            className={`dot dot-${stageState(p, s)}`}
            title={`${STAGE_LABELS[s].name}：${STATE_LABELS[stageState(p, s)]}`}
          />
        ))}
      </div>
      <button
        className="button ghost small"
        disabled={p.busy || remove.isPending}
        onClick={() => {
          if (confirm(`「${p.info.title || p.id}」を削除しますか？\n音源・解析結果・コード譜もすべて消えます。`)) remove.mutate(p.id);
        }}
      >
        削除
      </button>
    </li>
  );
}
