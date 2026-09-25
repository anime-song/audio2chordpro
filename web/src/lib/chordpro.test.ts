import { describe, expect, it } from "vitest";
import { parseChordPro, splitChords } from "./chordpro";

describe("splitChords", () => {
  it("コードの前の文字・コードごとの文字に分ける", () => {
    expect(splitChords("あさの[C]ひかり[Am]に")).toEqual([
      { chord: null, text: "あさの" },
      { chord: "C", text: "ひかり" },
      { chord: "Am", text: "に" },
    ]);
  });
  it("行頭のコード・続くコード・コードだけ", () => {
    expect(splitChords("[F]---[G]-")).toEqual([
      { chord: "F", text: "---" },
      { chord: "G", text: "-" },
    ]);
    expect(splitChords("[C][G]")).toEqual([
      { chord: "C", text: "" },
      { chord: "G", text: "" },
    ]);
    expect(splitChords("コードなし")).toEqual([{ chord: null, text: "コードなし" }]);
  });
  it("参考表記のコード・語の途中のコード", () => {
    expect(splitChords("ひか[(C)]り")).toEqual([
      { chord: null, text: "ひか" },
      { chord: "(C)", text: "り" },
    ]);
    expect(splitChords("言葉(こと[G]ば)")).toEqual([
      { chord: null, text: "言葉(こと" },
      { chord: "G", text: "ば)" },
    ]);
  });
});

describe("parseChordPro", () => {
  it("ディレクティブ・歌詞・グリッド・空行", () => {
    const text = "{title:朝のうた}\r\n{c:BPM=150　4/4拍子}\n[C]---- ----|[F]---- (3/4)[G]---|\n\nあさの[C]ひかり\n";
    expect(parseChordPro(text)).toEqual([
      { kind: "directive", name: "title", value: "朝のうた" },
      { kind: "directive", name: "comment", value: "BPM=150　4/4拍子" },
      {
        kind: "lyrics",
        grid: true,
        segments: [
          { chord: "C", text: "---- ----|" },
          { chord: "F", text: "---- (3/4)" },
          { chord: "G", text: "---|" },
        ],
      },
      { kind: "empty" },
      {
        kind: "lyrics",
        grid: false,
        segments: [
          { chord: null, text: "あさの" },
          { chord: "C", text: "ひかり" },
        ],
      },
      { kind: "empty" },
    ]);
  });
});
