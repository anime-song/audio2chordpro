// ChordPro を譜面の形（コードを歌詞の上）で見せる
import { useMemo } from "react";
import { type Line, parseChordPro } from "../lib/chordpro";

export function ChordProView({ text }: { text: string }) {
  const lines = useMemo(() => parseChordPro(text), [text]);
  return (
    <div className="sheet">
      {lines.map((line, i) => (
        <SheetLine key={i} line={line} />
      ))}
    </div>
  );
}

function SheetLine({ line }: { line: Line }) {
  switch (line.kind) {
    case "empty":
      return <div className="sheet-gap" />;
    case "directive":
      if (line.name === "title") return <h2 className="sheet-title">{line.value}</h2>;
      if (line.name === "subtitle") return <div className="sheet-subtitle">{line.value}</div>;
      if (line.name === "key") return <div className="sheet-meta">Key: {line.value}</div>;
      if (line.name === "comment") return <div className="sheet-meta">{line.value}</div>;
      return (
        <div className="sheet-meta">
          {line.name}
          {line.value && `: ${line.value}`}
        </div>
      );
    case "lyrics":
      return (
        <div className={`sheet-line${line.grid ? " grid" : ""}`}>
          {line.segments.map((s, i) => (
            <span className="seg" key={i}>
              <span className="chord">{s.chord ?? " "}</span>
              <span className="lyric">{s.text || " "}</span>
            </span>
          ))}
        </div>
      );
  }
}
