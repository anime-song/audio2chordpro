// 小さな共通部品
import { type ReactNode, useRef, useState } from "react";
import { STATE_LABELS, type StageView } from "../lib/project";

export function StateBadge({ state }: { state: StageView }) {
  return (
    <span className={`badge badge-${state}`}>
      {state === "running" && <Spinner />}
      {STATE_LABELS[state]}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden />;
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/** ファイルをドロップするか、クリックして選ぶ */
export function Dropzone({
  accept,
  title,
  hint,
  busy,
  onFile,
}: {
  accept: string;
  title: string;
  hint?: string;
  busy?: boolean;
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const take = (files: FileList | null) => {
    if (files?.[0] && !busy) onFile(files[0]);
  };
  return (
    <div
      className={`dropzone${over ? " over" : ""}${busy ? " busy" : ""}`}
      role="button"
      tabIndex={0}
      aria-disabled={busy}
      onClick={() => !busy && input.current?.click()}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !busy) {
          e.preventDefault();
          input.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
    >
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        onChange={(e) => {
          take(e.target.files);
          e.target.value = "";
        }}
      />
      <div className="dropzone-title">
        {busy && <Spinner />}
        {title}
      </div>
      {hint && <div className="dropzone-hint">{hint}</div>}
    </div>
  );
}
