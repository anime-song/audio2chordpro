// ビルドした画面に入ったパッケージのライセンス文を THIRD_PARTY_NOTICES.txt にまとめる。
// 画面の JS は圧縮でライセンスのコメントが消えるので、MIT などが求める著作権表示・許諾文はこのファイルで配る
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Plugin } from "vite";

const PACKAGE_DIR = /^(.*[\\/]node_modules[\\/])((?:@[^\\/]+[\\/])?[^\\/]+)[\\/]/;
const LICENSE_FILE = /^(licen[cs]e|copying|notice)(\.|$)/i;
const RULE = "-".repeat(78);

export function thirdPartyNotices(fileName = "THIRD_PARTY_NOTICES.txt"): Plugin {
  return {
    name: "third-party-notices",
    apply: "build",
    generateBundle(_options, bundle) {
      const dirs = new Map<string, string>(); // パッケージ名 → フォルダ
      for (const out of Object.values(bundle)) {
        if (out.type !== "chunk") continue;
        for (const id of out.moduleIds) {
          const m = PACKAGE_DIR.exec(id);
          if (m) dirs.set(m[2].replace(/\\/g, "/"), m[1] + m[2]);
        }
      }
      const sections = [...dirs].sort(([a], [b]) => a.localeCompare(b)).map(([, dir]) => section(dir));
      const head = `audio2chordpro の画面に含まれるサードパーティのソフトウェアとそのライセンス（${sections.length} 件）`;
      this.emitFile({ type: "asset", fileName, source: [head, ...sections].join(`\n\n${RULE}\n\n`) + "\n" });
    },
  };
}

function section(dir: string): string {
  const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  const repo = typeof pkg.repository === "string" ? pkg.repository : pkg.repository?.url;
  const files = readdirSync(dir).filter((f) => LICENSE_FILE.test(f));
  const texts = files.map((f) => readFileSync(join(dir, f), "utf8").trim());
  const lines = [`${pkg.name} ${pkg.version}`, `License: ${pkg.license ?? "（package.json に記載なし）"}`];
  if (repo) lines.push(`Repository: ${repo.replace(/^git\+/, "")}`);
  return [lines.join("\n"), ...(texts.length ? texts : ["（ライセンスのファイルがありません）"])].join("\n\n");
}
