// ③ コード譜：プレビュー・書式の設定・ダウンロード
import { useState } from "react";
import { fileUrl, type OptionsPatch, type ProjectDetail } from "../api/client";
import { useChordPro, useRun, useUpdateOptions } from "../api/hooks";
import { ChordProView } from "../components/ChordProView";
import { Card, ErrorText, Spinner } from "../components/ui";
import { stageState } from "../lib/project";

const FORMAT: { key: "simplify" | "head_outside" | "tail_grid"; label: string }[] = [
  { key: "tail_grid", label: "歌わない所に続くコードを小節グリッド（[C]---- ----|）で書く" },
  { key: "head_outside", label: "行頭のコードを括弧の外に書く" },
  { key: "simplify", label: "E#・B#・Cb・Fb を F・C・B・E と書く" },
];

export function ResultStep({ project: p, onBuild }: { project: ProjectDetail; onBuild: () => void }) {
  const chordpro = useChordPro(p.id, p.output_version);
  const update = useUpdateOptions(p.id);
  const run = useRun(p.id);
  const [view, setView] = useState<"sheet" | "text">("sheet");

  if (!p.output) {
    return (
      <Card>
        <p>まだコード譜がありません。</p>
        <button className="button primary" onClick={onBuild}>
          作成へ
        </button>
      </Card>
    );
  }

  // 書式を変えたらすぐ作り直す（コード譜の作成だけなので速い）
  const setFormat = (patch: OptionsPatch) =>
    update.mutate(patch, { onSuccess: () => run.mutate({ stages: ["render"] }) });
  const outdated = stageState(p, "render") !== "done" || stageState(p, "align") === "stale";

  return (
    <div className="step-body result">
      <Card
        title={
          <>
            コード譜 {p.busy && <Spinner />}
          </>
        }
        actions={
          <>
            <div className="toggle" role="group" aria-label="コードの書き方">
              {(["name", "degree"] as const).map((n) => (
                <button
                  key={n}
                  className={p.options.notation === n ? "on" : ""}
                  disabled={update.isPending || p.busy}
                  title={n === "degree" ? "調の主音からの度数（chord-romanizer）。ChordPro の保存もこの書き方になります" : undefined}
                  onClick={() => p.options.notation !== n && setFormat({ notation: n })}
                >
                  {n === "name" ? "コード名" : "ディグリー"}
                </button>
              ))}
            </div>
            <div className="toggle" role="group" aria-label="表示">
              <button className={view === "sheet" ? "on" : ""} onClick={() => setView("sheet")}>
                譜面
              </button>
              <button className={view === "text" ? "on" : ""} onClick={() => setView("text")}>
                テキスト
              </button>
            </div>
            <a className="button small" href={fileUrl(p.id, "chordpro", "download=true")} download>
              ChordPro を保存
            </a>
            <a className="button small ghost" href={fileUrl(p.id, "midi")} download>
              MIDI
            </a>
          </>
        }
      >
        {outdated && !p.busy && (
          <div className="notice">
            歌詞・設定が変わっています。
            <button className="link-button" onClick={() => run.mutate({ stages: [] }, { onSuccess: onBuild })}>
              作り直す
            </button>
          </div>
        )}
        <ErrorText error={chordpro.error ?? run.error} />
        {chordpro.data !== undefined &&
          (view === "sheet" ? <ChordProView text={chordpro.data} /> : <pre className="cho-text">{chordpro.data}</pre>)}
      </Card>
      <Card title="書式">
        <div className="checks">
          {FORMAT.map((f) => (
            <label key={f.key} className="check">
              <input
                type="checkbox"
                checked={p.options[f.key]}
                disabled={update.isPending || p.busy}
                onChange={(e) => setFormat({ [f.key]: e.target.checked })}
              />
              {f.label}
            </label>
          ))}
        </div>
        <ErrorText error={update.error} />
      </Card>
    </div>
  );
}
