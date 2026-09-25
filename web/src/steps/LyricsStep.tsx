// ① 曲情報と歌詞。歌詞は歌詞サイトで検索して選ぶか、貼り付ける
import { useEffect, useRef, useState } from "react";
import type { LyricsHit, ProjectDetail, SongInfo } from "../api/client";
import { useRun, useSearchLyrics, useSetLyrics, useUpdateInfo, useUseLyrics } from "../api/hooks";
import { Card, ErrorText, Spinner } from "../components/ui";
import { buildJob, siteLabel, sourceLabel } from "../lib/project";

const FIELDS: { key: keyof SongInfo; label: string }[] = [
  { key: "title", label: "曲名" },
  { key: "artist", label: "歌手" },
  { key: "lyricist", label: "作詞" },
  { key: "composer", label: "作曲" },
  { key: "arranger", label: "編曲" },
];

export function LyricsStep({ project, onBuild }: { project: ProjectDetail; onBuild: () => void }) {
  const [lyrics, setLyrics] = useState(project.lyrics);
  const saveLyrics = useSetLyrics(project.id);
  const run = useRun(project.id);
  const dirty = lyrics.trim() !== project.lyrics.trim();

  // サーバ側で歌詞が変わったら（歌詞サイトから取った・タグにあった）入れ替える
  const shown = useRef(project.lyrics);
  useEffect(() => {
    if (project.lyrics !== shown.current) {
      shown.current = project.lyrics;
      setLyrics(project.lyrics);
    }
  }, [project.lyrics]);

  // 作成のジョブがもう待っている・動いているなら、もう一度は出さずに ② を見せる
  // （歌詞を直していれば保存する。アライメントがまだならその歌詞で、済んでいれば後で「やり直しが必要」になる）
  const building = Boolean(buildJob(project));
  const build = async () => {
    if (dirty) await saveLyrics.mutateAsync(lyrics);
    if (!building) await run.mutateAsync({ stages: [] });
    onBuild();
  };

  return (
    <div className="step-body">
      <div className="columns">
        <SongInfoCard project={project} />
        <LyricsSearchCard project={project} />
      </div>
      <Card
        title="歌詞"
        actions={
          <>
            <LyricsSource source={project.lyrics_source} hasLyrics={Boolean(project.lyrics.trim())} />
            <button
              className="button small"
              disabled={!dirty || saveLyrics.isPending}
              onClick={() => saveLyrics.mutate(lyrics)}
            >
              保存
            </button>
          </>
        }
      >
        <textarea
          className="lyrics-editor"
          value={lyrics}
          onChange={(e) => setLyrics(e.target.value)}
          placeholder={"歌詞を貼り付けるか、右上の検索から選んでください。\n1行 = 譜面の1行、空行 = 段落の区切り。漢字(かな) で読みを指定できます。"}
          spellCheck={false}
        />
        <ErrorText error={saveLyrics.error} />
      </Card>
      <div className="step-footer">
        <ErrorText error={run.error} />
        <button className="button primary" disabled={saveLyrics.isPending || run.isPending} onClick={build}>
          {building
            ? dirty
              ? "歌詞を保存して作成を見る"
              : "作成中です。進み具合を見る"
            : lyrics.trim()
              ? "この歌詞でコード譜を作る"
              : "歌詞なしでコード譜を作る"}{" "}
          →
        </button>
      </div>
    </div>
  );
}

function LyricsSource({ source, hasLyrics }: { source: Record<string, unknown>; hasLyrics: boolean }) {
  if (!hasLyrics) return <span className="muted small">未設定</span>;
  if (source.type === "site")
    return (
      <span className="muted small">
        出所：
        <a href={String(source.url)} target="_blank" rel="noreferrer">
          {siteLabel(String(source.site))}
        </a>
      </span>
    );
  if (source.type === "tag") return <span className="muted small">出所：音源のタグ</span>;
  return <span className="muted small">出所：手入力</span>;
}

function SongInfoCard({ project: p }: { project: ProjectDetail }) {
  const [form, setForm] = useState<SongInfo>(p.info);
  const update = useUpdateInfo(p.id);
  const changed = FIELDS.filter((f) => form[f.key] !== p.info[f.key]);

  // サーバ側で曲情報が変わったら（歌詞サイトで埋まった）、手で書きかけていない項目を入れ替える
  const shown = useRef(p.info);
  useEffect(() => {
    const prev = shown.current;
    shown.current = p.info;
    setForm((f) => {
      const next = { ...f };
      for (const { key } of FIELDS) if (f[key] === prev[key]) next[key] = p.info[key];
      return next;
    });
  }, [p.info]);

  const [altTitle, altArtist] = p.alternatives[0] ?? [];
  return (
    <Card
      title="曲情報"
      actions={
        <button
          className="button small"
          disabled={changed.length === 0 || update.isPending}
          onClick={() => update.mutate(Object.fromEntries(changed.map((f) => [f.key, form[f.key]])))}
        >
          保存
        </button>
      }
    >
      <div className="form">
        {FIELDS.map((f) => (
          <label key={f.key} className="field">
            <span className="field-label">{f.label}</span>
            <input value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
            <span className="field-source">{sourceLabel(p.sources[f.key])}</span>
          </label>
        ))}
      </div>
      {altTitle !== undefined && p.sources.title === "filename" && (
        <p className="muted small">
          ファイル名は「曲名：{altTitle}　歌手：{altArtist}」とも読めます。
          <button
            className="link-button"
            onClick={() => update.mutate({ title: altTitle, artist: altArtist })}
            disabled={update.isPending}
          >
            こちらにする
          </button>
        </p>
      )}
      <ErrorText error={update.error} />
    </Card>
  );
}

function LyricsSearchCard({ project: p }: { project: ProjectDetail }) {
  const search = useSearchLyrics(p.id);
  const use = useUseLyrics(p.id);
  const [title, setTitle] = useState(p.info.title);
  const [artist, setArtist] = useState(p.info.artist);

  // 歌詞がまだ無ければ、曲情報で一度だけ自動で検索する
  const searched = useRef(false);
  useEffect(() => {
    if (!searched.current && !p.lyrics.trim() && p.info.title) {
      searched.current = true;
      search.mutate({});
    }
  }, [p.lyrics, p.info.title, search]);

  return (
    <Card title="歌詞を検索">
      <form
        className="search-row"
        onSubmit={(e) => {
          e.preventDefault();
          search.mutate({ title, artist });
        }}
      >
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="曲名" aria-label="曲名" />
        <input value={artist} onChange={(e) => setArtist(e.target.value)} placeholder="歌手" aria-label="歌手" />
        <button className="button" disabled={!title.trim() || search.isPending}>
          {search.isPending ? <Spinner /> : "検索"}
        </button>
      </form>
      <ErrorText error={search.error ?? use.error} />
      {search.data?.length === 0 && <p className="muted small">見つかりませんでした。曲名を短くしてみてください。</p>}
      <ul className="hits">
        {search.data?.map((hit) => (
          <Hit
            key={hit.url}
            hit={hit}
            chosen={p.lyrics_source.url === hit.url}
            busy={use.isPending && use.variables === hit.url}
            disabled={use.isPending}
            onUse={() => use.mutate(hit.url)}
          />
        ))}
      </ul>
    </Card>
  );
}

function Hit(props: { hit: LyricsHit; chosen: boolean; busy: boolean; disabled: boolean; onUse: () => void }) {
  const { hit } = props;
  const writers = [hit.lyricist && `作詞：${hit.lyricist}`, hit.composer && `作曲：${hit.composer}`].filter(Boolean);
  return (
    <li className={`hit${props.chosen ? " chosen" : ""}`}>
      <div className="hit-body">
        <div className="hit-title">
          {hit.title}
          <span className="muted">　{hit.artist}</span>
        </div>
        {writers.length > 0 && <div className="muted small">{writers.join("　")}</div>}
        {hit.beginning && <div className="hit-beginning">歌い出し：{hit.beginning}</div>}
      </div>
      <button className="button small" disabled={props.disabled} onClick={props.onUse}>
        {props.busy ? <Spinner /> : props.chosen ? "使用中" : "この歌詞を使う"}
      </button>
    </li>
  );
}
