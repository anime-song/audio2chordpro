# audio2chordpro

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/anime-song/audio2chordpro/blob/main/notebooks/audio2chordpro_colab.ipynb)

音源、自動採譜（AMT）由来の MIDI、および歌詞テキストから、歌詞の上にコードを配置した **ChordPro 形式の譜面** を生成するツールです（日本語楽曲向け）。

```chordpro
{title:曲名}
{subtitle:歌：歌手　作詞：作詞者　作曲・編曲：作曲者}
{c:BPM=150　4/4拍子　-:8分音符}
{key:C}
[C]---- ----|[F]---- [G]----|
あさの[C]ひかり[Am]に　言葉(こと[F]ば)を[G]のせて
[F]---[G]- ----|
```

Web ブラウザの UI、Google Colab、コマンドライン（CLI）、Python API から利用できます。MIDI が手元にない場合でも、音源から自動でビート・コード・歌メロを推定して作成できます。

---

## 主な特徴

- **歌詞とコードの自動配置**: ボーカル音声の強制アライメントと歌メロ採譜を組み合わせ、発声タイミングに合わせて歌詞の上にコードを配置。
- **歌本・実用向け記譜**:
  - 8分音符グリッドによるシンコペーションの反映
  - 漢字の途中でコードが変わる場合の自動ルビ展開（例: `言葉(こと[G]ば)`）
  - 前奏・間奏・後奏の小節グリッド展開（例: `[C]---- ----|[F]---- [G]----|`）
  - 通常のコード名表記に加え、ディグリー表記（`VIm7` や `IV/V` など。`chord-romanizer` 連携）にも対応
- **Web UI & 段階的編集**: ブラウザ上で音源投入から歌詞検索、手動でのコード・アライメント調整、プレビュー、エクスポートまで完結。
- **差分キャッシュ設計**: 1曲1フォルダのプロジェクト形式。歌詞や書式を変更した際も、重い音響解析はスキップして必要な処理だけを再実行。

---

## クイックスタート

### 1. Google Colab（手軽に試す）

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/anime-song/audio2chordpro/blob/main/notebooks/audio2chordpro_colab.ipynb)

GPU ランタイムを選択し、ノートブックのセルを実行するだけで Web UI が起動します。生成したプロジェクトは Google ドライブに保存できます。

### 2. ローカル Web UI

パッケージマネージャ [uv](https://docs.astral.sh/uv/) を使用します（Python 3.10〜3.13、CUDA 対応 GPU 推奨・CPU でも実行可能）。

```bash
# 依存関係のインストール
uv sync

# サーバ起動（ブラウザで http://127.0.0.1:8000 が開きます）
uv run audio2chordpro serve
```

※ Web UI の画面は GitHub Releases からビルド済みアセットが自動取得されるため、Node.js のインストールは不要です。

#### 画面での基本的な流れ
1. **音源の追加**: MP3 / WAV ファイルをアップロード（解析ジョブがバックグラウンドで開始）
2. **曲情報・歌詞の指定**: 音源タグから自動取得、または [うたてん](https://utaten.com/) から検索して設定（直接入力も可能）
3. **作成・プレビュー**: アライメントと ChordPro の生成
4. **調整・出力**: プレビュー確認、書式変更（コード名 / ディグリー）、手動修正、ファイルのダウンロード

### 3. コマンドライン（CLI）

```bash
# 基本実行（音源 + MIDI + 歌詞 → ChordPro）
uv run audio2chordpro song.mp3 --midi song.mid --lyrics lyrics.txt -o song.cho \
    --title 曲名 --artist 歌手 --lyricist 作詞者 --composer 作曲者

# MIDI なし（tsumugi で音源から自動生成）
uv run audio2chordpro song.mp3 --lyrics lyrics.txt -o song.cho --save-midi song.mid

# 歌詞なし（コード譜・小節グリッドのみ出力）
uv run audio2chordpro song.mp3 --midi song.mid -o chords.cho

# アライメント結果を保存・再利用（再レンダリングを高速化）
uv run audio2chordpro song.mp3 --midi song.mid --lyrics lyrics.txt -o song.cho --save-alignment song.align.json
uv run audio2chordpro song.mp3 --midi song.mid --alignment song.align.json -o song.cho
```

#### 主な CLI オプション

| オプション | デフォルト | 説明 |
|---|---|---|
| `--midi` | – | 入力 MIDI（省略時は [tsumugi](https://github.com/anime-song/tsumugi) で自動生成） |
| `--melody` | `sheetsage` | 歌メロ取得元: `sheetsage`（SheetSage2 による採譜） / `amt`（MIDI の melody トラック） |
| `--beats` | `amt` | 拍取得元: `amt`（MIDI テンポマップ） / `sheetsage`（変拍子対応） |
| `--degree` | `false` | コードをディグリー表記（`VIm7`, `IV/V` 等）で出力（chord-romanizer） |
| `--simplify` | `false` | 異名同音の簡略化（E#, B#, Cb, Fb → F, C, B, E） |
| `--head-outside` | `false` | 行頭コードを括弧の外側に配置（デフォルトは `（[C]はい）`） |
| `--no-tail-grid` | `false` | 行末・行中のコード小節グリッド展開を無効化 |
| `--save-midi` | – | tsumugi で自動生成した MIDI の保存先 |
| `--cache-dir` | `./cache` | 中間キャッシュの保存先 |

---

## 処理の仕組み

```
音源 ──────┬─ ボーカル分離（Demucs）─ CTC（wav2vec2）─┐
           │                                             ├─ 強制アライメント ─ モーラごとの発声時刻
歌詞 ──────┴─ 形態素・モーラ解析（pyopenjtalk） ────────┘        ↑
                                                                 │（歌メロ開始時刻を事前分布として反映）
AMT MIDI ── 拍タイムライン（テンポ正規化・コード表記） ──────────┤
SheetSage2 ─ 歌メロノート ──────────────────────────────────────┘
                                         ↓
            モーラとメロディの DP 対応付け → 8分音符グリッド量子化 → コード配置 → ChordPro 出力
```

1. **拍タイムライン (`timeline.py`)**: MIDI テンポマップを拍単位に正規化。冒頭の異常テンポや変拍子、位相ズレを補正。コードの綴りは MIDI 本来の表記を保持。
2. **歌詞・モーラ解析 (`lyrics.py`)**: 形態素解析と読み（モーラ）への分解。英単語辞書、数字の読み分け、コード挿入用の漢字分割に対応。
3. **強制アライメント (`align.py`, `vocals.py`)**: ボーカル音声を分離し、CTC モデルで音響特徴量を抽出。歌メロの発音開始時刻を事前分布として加味し、モーラ単位の発声時刻を特定。
4. **コード配置・整形 (`render.py`)**: 発声タイミングと歌メロノートを対応付け、8分音符グリッドに量子化。各コード変化点に最も近い音節へ配置し、間奏の小節グリッドやルビ補正を行って出力。

---

## プロジェクト管理とキャッシュ

Web UI および Python API では、楽曲ごとに 1 つのプロジェクトフォルダを作成して状態を管理します。

```
projects/<曲名>/
  project.json         設定・実行ステータス・入力のハッシュ値
  song.json            楽曲メタデータ（曲名、アーティスト、クレジット情報）
  audio/<ファイル名>   元の音源
  lyrics.txt           使用歌詞
  midi/amt.mid         使用した MIDI ファイル
  edits/chords.json    手動修正したコード情報
  alignment.auto.json  自動推定されたアライメント
  alignment.json       手動修正したアライメント（存在する場合に優先）
  output/<曲名>.cho    生成された ChordPro ファイル
  work/                軽量な作業データ（SheetSage2 出力など）
```

- **容量の節約**: プロジェクトフォルダ内には軽量なファイル（数 MB）のみを保存します。
- **一時ファイル（scratch）**: ボーカル分離音声や CTC 特徴量などの大きな中間ファイル（1曲あたり約 200MB）は、`~/.cache/audio2chordpro/scratch/` に音源ハッシュ別で隔離保存され、アライメントの再実行時のみ利用されます。

---

## Python API

```python
from audio2chordpro import Options, SongInfo, transcribe

result = transcribe(
    audio="song.mp3",
    midi="song.mid",
    lyrics=open("lyrics.txt", encoding="utf-8").read(),
    info=SongInfo(title="曲名", artist="歌手"),
    options=Options(cache_dir="cache"),
)

print(result.chordpro)
```

### プロジェクト単位の操作

```python
from audio2chordpro import Project

p = Project.create("projects", "歌手 - 曲名.mp3")
p.run("analysis")          # 音響解析（tsumugi / SheetSage2 / ボーカル分離）
hits = p.search_lyrics()    # 歌詞サイトの検索
p.use_lyrics(hits[0])       # 歌詞とメタデータを適用
p.run()                     # アライメントと ChordPro 生成

# 書式変更時はレンダリングのみ再実行
p.set_options(notation="degree")
p.run()
print(p.chordpro())
```

---

## 開発・Web UI のカスタマイズ

ローカルでフロントエンド（`web/`）を編集・ビルドする場合:

```bash
# サーバを起動（ブラウザ自動起動オフ）
uv run audio2chordpro serve --no-browser

# フロントエンド開発サーバの起動（http://localhost:5173、/api はバックエンドへプロキシ）
cd web
npm install
npm run dev

# 本番用ビルド（audio2chordpro/server/static に出力）
npm run build

# OpenAPI スキーマから TypeScript 型を再生成
npm run gen:api
```

---

## モジュール構成

| モジュール | 役割 |
|---|---|
| `audio2chordpro/pipeline.py` | パイプライン統括（`transcribe`, `prepare`, `render_chordpro`） |
| `audio2chordpro/project.py` | プロジェクト管理（差分実行、入出力の永続化、手動編集反映） |
| `audio2chordpro/server/` | Web API（FastAPI）およびジョブ実行キュー |
| `web/` | Web UI（React + TypeScript + Vite） |
| `audio2chordpro/timeline.py` | MIDI テンポマップの正規化、コード・拍タイムラインの構築、ディグリー変換 |
| `audio2chordpro/chords.py` | コードネームおよび調の解析 |
| `audio2chordpro/lyrics.py` | 歌詞トークナイズ、読み・モーラ分解、表記対応付け |
| `audio2chordpro/vocals.py` | ボーカル分離、CTC 音響特徴量算出 |
| `audio2chordpro/align.py` | 歌メロ事前分布に基づくラティス強制アライメント |
| `audio2chordpro/melody.py` | 歌メロノート抽出（SheetSage2 / MIDI） |
| `audio2chordpro/render.py` | モーラ・ノート対応付け、グリッド配置、ChordPro 文字列生成 |
| `audio2chordpro/song_info.py` | 音源タグ・ファイル名からのメタデータ抽出 |
| `audio2chordpro/providers/` | MIDI 生成プロバイダ（tsumugi）、歌詞検索プロバイダ（うたてん） |

---

## クレジット・ライセンス

- **漢字読みデータ**: [KANJIDIC2](https://www.edrdg.org/wiki/index.php/KANJIDIC_Project) (© EDRDG, CC BY-SA 4.0)
- **CTC 音響モデル**: [reazon-research/japanese-wav2vec2-base-rs35kh](https://huggingface.co/reazon-research/japanese-wav2vec2-base-rs35kh) (Apache-2.0)
- **歌メロ採譜モデル**: [SheetSage2](https://huggingface.co/m-a-p/SheetSage2) (**CC BY-NC 4.0 非商用**)
- **AMT（MIDI 自動生成）**: [tsumugi](https://github.com/anime-song/tsumugi) (MIT), [stem-splitter](https://pypi.org/project/stem-splitter/)
- **要素技術・ライブラリ**: [Demucs](https://github.com/facebookresearch/demucs), [pyopenjtalk-plus](https://github.com/tsukumijima/pyopenjtalk-plus), [alkana](https://github.com/cod-sushi/alkana.py), [chord-romanizer](https://github.com/anime-song/chord-romanizer)
- **Web UI**: [React](https://react.dev/), [React Router](https://reactrouter.com/), [TanStack Query](https://tanstack.com/query), [openapi-fetch](https://openapi-ts.dev/openapi-fetch/) (MIT、ライセンス表記は `THIRD_PARTY_NOTICES.txt`)
