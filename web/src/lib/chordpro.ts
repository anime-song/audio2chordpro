// ChordPro のテキスト → 表示用の行（コードを歌詞の上に載せる）

/** コード1つと、その下に来る文字（行頭のコードより前の文字は chord = null） */
export type Segment = { chord: string | null; text: string };

export type Line =
  | { kind: "directive"; name: string; value: string } // {title:…} など
  | { kind: "lyrics"; segments: Segment[]; grid: boolean } // grid: 小節グリッド（[C]---- ----|）の行
  | { kind: "empty" };

const DIRECTIVE = /^\{\s*([\w-]+)\s*(?::\s*(.*?))?\s*\}$/;
const CHORD = /\[([^\]]*)\]/g;
const GRID_TEXT = /^[-|\s]*(\(\d+\/\d+\))?[-|\s]*$/; // - = 8分、| = 小節線、(3/4) = 拍子

const ALIASES: Record<string, string> = { t: "title", st: "subtitle", c: "comment", k: "key" };

export function parseChordPro(text: string): Line[] {
  return text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((raw): Line => {
      const line = raw.trimEnd();
      if (!line.trim()) return { kind: "empty" };
      const d = DIRECTIVE.exec(line.trim());
      if (d) {
        const name = d[1].toLowerCase();
        return { kind: "directive", name: ALIASES[name] ?? name, value: d[2] ?? "" };
      }
      const segments = splitChords(line);
      const grid = segments.some((s) => s.chord !== null) && segments.every((s) => GRID_TEXT.test(s.text));
      return { kind: "lyrics", segments, grid };
    });
}

export function splitChords(line: string): Segment[] {
  const segments: Segment[] = [];
  let last = 0;
  let chord: string | null = null;
  for (const m of line.matchAll(CHORD)) {
    const text = line.slice(last, m.index);
    if (chord !== null || text) segments.push({ chord, text });
    chord = m[1];
    last = m.index + m[0].length;
  }
  if (chord !== null || last < line.length || segments.length === 0) segments.push({ chord, text: line.slice(last) });
  return segments;
}
