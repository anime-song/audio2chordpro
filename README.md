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

## パイプライン

```
音源 ─┬─ ボーカル分離（Demucs）─ CTC（wav2vec2）─┐
      │                                             ├─ 強制アライメント ─ モーラごとの発声時刻
歌詞 ─┴─ 読み・モーラ解析（pyopenjtalk + 辞書）───┘        ↑
                                                            │（歌メロ開始時刻を事前分布として付与）
AMT MIDI ── 拍タイムライン（テンポ補正・コード表記）───────┤
SheetSage2 ─ 歌メロノート ─────────────────────────────────┘
                                    ↓
        モーラと歌メロのDP対応付け → 8分音符グリッド整音 → コード配置 → ChordPro 出力
```

1. **拍タイムライン (`timeline.py`)**: AMT テンポマップを拍単位に正規化。倍テンポ・半テンポ、冒頭の異常テンポ、位相ズレ（1拍未満の小節）を補正。調に応じた異名同音（例: G長調の ♭VII7 は F7）の綴りを適用。
2. **読み解析 (`lyrics.py`)**: pyopenjtalk による形態素・読み解析。英単語辞書参照、数字の読み分け（数値読み／1文字読み）、漢字連続の単字分割（コード挿入用）に対応。
3. **強制アライメント (`align.py`)**: wav2vec2 CTC モデル（漢字かな・サブワード混在）に対応したラティス探索。歌メロの発音開始時刻にボーナスを付与して精度を向上。
4. **コード配置 (`render.py`)**: モーラと歌メロノートを DP（動的計画法）でアライメントし、8分音符グリッドに量子化。各コード変化点に最も近い音節の先頭（±8分音符以内）にコードを配置。

## 入力要件

- **音源**: MP3 / WAV
- **AMT MIDI**（省略可。省略時は [tsumugi](https://github.com/anime-song/tsumugi) で音源から作る）: 以下のトラックを含む標準 MIDI ファイル
  - `Predicted Tempo Map`: テンポ、拍子、調（key_signature）
  - `Predicted Chords`: marker イベントに Harte 表記のコード名（例: `A:min7`, `C:maj7/5`）。このトラックに無ければ、他のトラック（テンポマップなど）の marker を使う
  - `melody`: 歌メロトラック（`--melody amt` 指定時に使用）
- **歌詞テキスト**: UTF-8 プレーンテキスト
  - 1行 = ChordPro の1行、空行 = セクション区切り
  - ルビ記法: `漢字(かな)` で読みを指定可能（出力にも反映）
  - 合いの手・コーラス: `（…）` や `(…)` は発声順にアライメント

## インストール

パッケージマネージャに [uv](https://docs.astral.sh/uv/) を使用します。Python 3.10〜3.13、CUDA 対応 GPU を推奨（CPU 実行も可能）。

```bash
uv sync
```

※ Windows / Linux 環境では CUDA 12.6 版 PyTorch が導入されます。

### SheetSage2 モデル（歌メロ採譜）

歌メロの抽出には [SheetSage2](https://huggingface.co/m-a-p/SheetSage2) を使用します（初回実行時に約 230MB 自動取得）。  
※ モデル重みライセンス: **CC BY-NC 4.0（非商用）**  
ローカルモデルを使う場合は `--melody amt`（MIDI トラックを利用）または `--sheetsage-model <DIR>` を指定してください。

## 使い方

### Google Colab

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/anime-song/audio2chordpro/blob/main/notebooks/audio2chordpro_colab.ipynb)

GPU ランタイムを選択して順次実行することで、ブラウザ上で ChordPro を生成・ダウンロードできます。

### CLI

```bash
# 基本実行（音源 + MIDI + 歌詞 → ChordPro）
uv run audio2chordpro song.mp3 --midi song.mid --lyrics lyrics.txt -o song.cho \
    --title 曲名 --artist 歌手 --lyricist 作詞者 --composer 作曲者 --arranger 編曲者

# アライメント結果を保存・再利用（再レンダリングの高速化）
uv run audio2chordpro song.mp3 --midi song.mid --lyrics lyrics.txt -o song.cho --save-alignment song.align.json
uv run audio2chordpro song.mp3 --midi song.mid --alignment song.align.json -o song.cho

# 歌詞なし（コード譜のみ出力）
uv run audio2chordpro song.mp3 --midi song.mid -o chords.cho

# MIDI なし（tsumugi で音源から作る。作った MIDI は --save-midi で保存できる）
uv run audio2chordpro song.mp3 --lyrics lyrics.txt -o song.cho --save-midi song.mid
```

中間処理結果（tsumugi の MIDI、ボーカル分離、CTC、SheetSage2 出力）は `./cache`（`--cache-dir` で変更可）にキャッシュされます。

#### tsumugi（MIDI の自動生成）

`--midi` を省略すると [tsumugi](https://github.com/anime-song/tsumugi) で、ステム分離 → ステムごとの採譜 → ビート・コード・調の推定を行い、
その MIDI（`<曲名>_beat_chord.mid`）を使います。

#### 主なオプション

| オプション | デフォルト | 説明 |
|---|---|---|
| `--melody` | `sheetsage` | 歌メロ取得元: `sheetsage` / `amt`（MIDI の melody トラック） |
| `--beats` | `amt` | 拍取得元: `amt`（MIDI テンポマップ）/ `sheetsage`（変拍子対応） |
| `--simplify` | `false` | 異名同音の簡略表記（E#, B#, Cb, Fb → F, C, B, E） |
| `--head-outside` | `false` | 行頭コードを括弧の外側に配置（デフォルトは `（[C]はい）`） |
| `--no-tail-grid` | `false` | 行末以降・行中のコード小節グリッド展開を無効化 |
| `--save-midi` | – | tsumugi で作った MIDI をコピーする先 |
| `--models-dir` | `--cache-dir` | tsumugi のソース・チェックポイントの置き場（曲ごとに `--cache-dir` を分けても共有できる） |

### Python API

```python
from audio2chordpro import Options, SongInfo, transcribe

result = transcribe(
    audio="song.mp3",
    midi="song.mid",
    lyrics=open("lyrics.txt", encoding="utf-8").read(),
    info=SongInfo(title="曲名", artist="歌手"),
    options=Options(cache_dir="cache"),
)

# ChordPro 文字列の取得
print(result.chordpro)

# アライメントデータ（JSONシリアライズ可能）
alignment_dict = result.alignment.to_dict()
```

アライメント修正後の再レンダリングには `prepare()` と `render_chordpro()` を使用します。
手で直したコードは `prepare(..., chords=[(秒, コード名), ...])` で MIDI のコードの代わりに使えます。

### プロジェクト（段階ごとに実行・途中からやり直し）

1曲を1つのフォルダにまとめ、段階ごとの結果を保存します。歌詞や設定を変えたときは、影響する段階だけをやり直します（UI からの利用を想定）。
重い解析（tsumugi・SheetSage2・ボーカル分離）は音源だけで進むので、歌詞を選ぶ前に始めておけます。

```python
from audio2chordpro import Project

p = Project.create("projects", "歌手 - 曲名.mp3")  # タグ（曲名・歌手・作詞・作曲・歌詞）、無ければファイル名から曲情報を入れる
p.run("analysis")                                  # tsumugi → SheetSage2 → ボーカル分離・CTC
hits = p.search_lyrics()                           # 曲情報で歌詞サイトを検索
p.use_lyrics(hits[0])                              # 歌詞を使い、空いている曲情報も埋める
p.run()                                            # アライメント → ChordPro（まだの段階・古くなった段階だけ）
print(p.chordpro())

p.set_options(simplify=True)                       # 書式を変えたら ChordPro だけやり直す
p.status()                                         # {"midi": "done", "melody": "done", "vocals": "done", "align": "done", "render": "stale"}
```

手元の MIDI を使うときは `p.set_midi("song.mid")`、歌詞を直接入れるときは `p.set_lyrics(text)` を使います。
コードの手直しは `p.save_chords([(秒, コード名), ...])`（元は `p.midi_chords()`、MIDI への書き出しは `p.export_midi()`）、
アライメントの手直しは `p.save_alignment(alignment)` で保存します。手直ししたアライメントは、そのあと歌詞を変えると使われなくなります。

```
projects/<曲名>/
  project.json         設定・各段階を実行したときの入力の指紋・手直しの記録
  song.json            曲情報と各項目の出所（tag / filename / site:<サイト> / manual）
  audio/<元のファイル名>
  lyrics.txt           使う歌詞（lyrics.source.json に出所）
  midi/amt.mid         AMT の MIDI（書き換えない）
  edits/chords.json    手で直したコード
  alignment.auto.json  自動のアライメント（手で直したものは alignment.json）
  output/<曲名>.cho
  work/                tsumugi・ボーカル分離・CTC・SheetSage2 の出力（消しても作り直せる）
```

tsumugi のソース・チェックポイントは全曲で共有する `~/.cache/audio2chordpro`（`Project.create(..., models_dir=...)` で変更可）に置きます。

### 歌詞サイトから歌詞を取得

曲名で歌詞サイトを検索し、候補から選んだ曲の歌詞とメタデータ（歌手・作詞・作曲・編曲）を取得できます。
対応サイトは [うたてん](https://utaten.com/) です。歌ネット・歌time はボット対策（Cloudflare）でプログラムからの取得を受け付けないため対応していません。
取得した歌詞は各サイトの利用規約に従い、個人的な利用の範囲で使ってください（リクエストは1秒以上の間隔をあけます）。

```python
from audio2chordpro import transcribe
from audio2chordpro.providers.lyrics import fetch, search

hits = search("曲名", artist="歌手名")  # 候補（SongHit: 曲名・歌手・作詞・作曲・編曲・歌い出し・URL）
page = fetch(hits[0])  # LyricsPage: info（SongInfo）・lyrics・lyrics_ruby（ふりがな付き）
result = transcribe("song.mp3", "song.mid", page.lyrics, page.info)
```

```bash
uv run python -m audio2chordpro.providers.lyrics 曲名 --artist 歌手名           # 候補の一覧
uv run python -m audio2chordpro.providers.lyrics 曲名 --pick 1 -o lyrics.txt   # 1番目の歌詞を保存
```

## 出力仕様

- **コード配置**: 該当コードへ遷移する音節の直前に配置。シンコペーション（8分音符の食い込み）時は食い込んだ音節の直前に配置。
- **語中でのコード変化**: 漢字の読みの途中にコードが入る場合、語全体をルビ表記化してコードを埋め込み（例: `言葉(こと[G]ば)`）。
- **間奏・イントロ**: 2小節以上の歌唱なし区間は小節グリッド形式で出力（例: `[C]---- ----|[F]---- [G]----|`、`-` は8分音符）。変拍子小節には拍子記号（例: `(3/4)`）を付与。
- **行末の余白**: 最終音節以降にコードが続く場合は小節グリッドを展開。同一小節内のコードはダッシュで補完（例: `…ひかり-[G]- ----|`）。続くグリッドが短ければ（行末と合わせて2小節まで）同じ行に続けて書く（例: `…ひかり-[G]- ----|[Am]---- ----|`）。
- **行中の余白**: 行の途中でも、音節と次の音節の間にコードが2つ以上続く・1.5小節以上空く場合はダッシュと小節線で埋める（例: `…ひかり--[F]- [G]---|---- [C]---あさ…`）。丸ごと空く小節はグリッドの行にして改行。
- **歌の終わりがグリッドの頭と重なるとき**: 最後の音節が小節の頭にあり、その後ろに長く歌わない区間が続く場合は、グリッドをその小節の頭から書き、音節のコードは括弧付きの参考表記にする（例: `…ひか[(C)]り` の次の行に `[C]---- [F]----|…`）。
- **転調**: セクション途中での `{key:…}` 挿入に対応。

## アライメント精度と特性

J-POP / アニソン 8曲（手動作成 ChordPro との比較、コード配置の一致率 F値）:
- **完全一致**: 約 76%
- **許容範囲内（±1モーラ）**: 約 89%

### 精度への寄与度
歌メロ事前分布（約 +12pt） > 8分音符グリッド整音 > SheetSage2 ノート > DP 最適化

### 制限事項
- 複数人のユニゾンやハーモニーが密接に重なる区間
- 超高速な歌唱・ラップ調の早口フレーズ
- 英語歌詞の連続（日本語用発音辞書との差異）
- 小節途中で倍テンポ等に切り替わる極端な AMT テンポマップ

## 拡張インターフェース

- **MIDI プロバイダ (`providers.MidiProvider`)**: 音源から AMT MIDI を自動生成する。[tsumugi](https://github.com/anime-song/tsumugi) による実装が `providers.midi.TsumugiMidiProvider`
- **歌詞プロバイダ (`providers.LyricsProvider`)**: 楽曲メタデータからの歌詞自動取得。歌詞サイトによる実装が `providers.lyrics.SiteLyricsProvider`。サイトを増やすときは `providers/lyrics/` に `LyricsSite` のサブクラス（`search_url` / `parse_search` / `parse_song`）を足して `SITES` に登録する
- **UI / 手動補正**: `Project` の段階・状態（`run` / `status`）と手直しの保存（`save_chords` / `save_alignment`）。`Alignment` は JSON（`to_dict` / `from_dict`）でやり取りできる

## モジュール構成

| モジュール | 役割 |
|---|---|
| `audio2chordpro/pipeline.py` | 統合パイプライン制御（`transcribe`, `prepare`, `align_audio`, `render_chordpro`） |
| `audio2chordpro/project.py` | 1曲1フォルダのプロジェクト：段階ごとの保存・やり直し・手直し |
| `audio2chordpro/timeline.py` | MIDI テンポマップの拍単位正規化・補正 |
| `audio2chordpro/chords.py` | コードネームおよび調の解析・調号に応じた表記統一 |
| `audio2chordpro/lyrics.py` | 歌詞トークナイズ、読み・モーラ分解、表層文字列との対応付け |
| `audio2chordpro/vocals.py` | 音源からのボーカル分離、CTC 音響特徴量算出 |
| `audio2chordpro/align.py` | 歌メロ事前分布に基づくラティス強制アライメント |
| `audio2chordpro/melody.py` | 歌メロノート抽出（MIDI / SheetSage2 ラッパー） |
| `audio2chordpro/render.py` | モーラ・ノート対応付け、音節グリッド配置、ChordPro 文字列生成 |
| `audio2chordpro/song_info.py` | 楽曲メタデータ（音源のタグ・ファイル名から読む）とディレクティブ生成 |
| `audio2chordpro/providers/base.py` | MIDI / 歌詞供給インターフェース定義 |
| `audio2chordpro/providers/midi/` | 音源からの MIDI 生成（`tsumugi.py`） |
| `audio2chordpro/providers/lyrics/` | 歌詞サイトの検索・取得（`base.py` 共通部分、`utaten.py` うたてん） |
| `tests/` | パーサ・タグ読み・コードの手直し・プロジェクトのテスト（`uv run pytest`、ネットにはつながず重い処理は走らせない） |

## クレジット・ライセンス

- **漢字読みデータ**: [KANJIDIC2](https://www.edrdg.org/wiki/index.php/KANJIDIC_Project) (© EDRDG, CC BY-SA 4.0)
- **CTC 音響モデル**: [reazon-research/japanese-wav2vec2-base-rs35kh](https://huggingface.co/reazon-research/japanese-wav2vec2-base-rs35kh) (Apache-2.0)
- **歌メロ採譜モデル**: [SheetSage2](https://huggingface.co/m-a-p/SheetSage2) (CC BY-NC 4.0)
- **AMT（MIDI の自動生成）**: [tsumugi](https://github.com/anime-song/tsumugi) (MIT)。ステム分離に [stem-splitter](https://pypi.org/project/stem-splitter/)
- **要素技術・ライブラリ**: [Demucs](https://github.com/facebookresearch/demucs), [pyopenjtalk-plus](https://github.com/tsukumijima/pyopenjtalk-plus), [alkana](https://github.com/cod-sushi/alkana.py)
