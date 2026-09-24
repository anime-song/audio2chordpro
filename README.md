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
- **AMT MIDI**: 以下のトラックを含む標準 MIDI ファイル
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
```

中間処理結果（ボーカル分離、CTC、SheetSage2 出力）は `./cache`（`--cache-dir` で変更可）にキャッシュされます。

#### 主なオプション

| オプション | デフォルト | 説明 |
|---|---|---|
| `--melody` | `sheetsage` | 歌メロ取得元: `sheetsage` / `amt`（MIDI の melody トラック） |
| `--beats` | `amt` | 拍取得元: `amt`（MIDI テンポマップ）/ `sheetsage`（変拍子対応） |
| `--simplify` | `false` | 異名同音の簡略表記（E#, B#, Cb, Fb → F, C, B, E） |
| `--head-outside` | `false` | 行頭コードを括弧の外側に配置（デフォルトは `（[C]はい）`） |
| `--no-tail-grid` | `false` | 行末以降のコード小節グリッド展開を無効化 |

### Python API

```python
from audio2chordpro import Options, SongInfo, transcribe

result = transcribe(
    audio_path="song.mp3",
    midi_path="song.mid",
    lyrics_text=open("lyrics.txt", encoding="utf-8").read(),
    info=SongInfo(title="曲名", artist="歌手"),
    options=Options(cache_dir="cache"),
)

# ChordPro 文字列の取得
print(result.chordpro)

# アライメントデータ（JSONシリアライズ可能）
alignment_dict = result.alignment.to_dict()
```

アライメント修正後の再レンダリングには `prepare()` と `render_chordpro()` を使用します。

## 出力仕様

- **コード配置**: 該当コードへ遷移する音節の直前に配置。シンコペーション（8分音符の食い込み）時は食い込んだ音節の直前に配置。
- **語中でのコード変化**: 漢字の読みの途中にコードが入る場合、語全体をルビ表記化してコードを埋め込み（例: `言葉(こと[G]ば)`）。
- **間奏・イントロ**: 2小節以上の歌唱なし区間は小節グリッド形式で出力（例: `[C]---- ----|[F]---- [G]----|`、`-` は8分音符）。変拍子小節には拍子記号（例: `(3/4)`）を付与。
- **行末の余白**: 最終音節以降にコードが続く場合は小節グリッドを展開。同一小節内のコードはダッシュで補完（例: `…ひかり-[G]- ----|`）。
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

- **MIDI プロバイダ (`providers.MidiProvider`)**: 音源から AMT MIDI を自動生成するパイプライン（例: [tsumugi](https://github.com/anime-song/tsumugi) 連携など）
- **歌詞プロバイダ (`providers.LyricsProvider`)**: 楽曲メタデータからの歌詞自動取得
- **UI / 手動補正**: `Alignment` オブジェクトの JSON 出力（`to_dict` / `from_dict`）を介して、外部エディタでの修正・再レンダリングが可能

## モジュール構成

| モジュール | 役割 |
|---|---|
| `audio2chordpro/pipeline.py` | 統合パイプライン制御（`transcribe`, `prepare`, `render_chordpro`） |
| `audio2chordpro/timeline.py` | MIDI テンポマップの拍単位正規化・補正 |
| `audio2chordpro/chords.py` | コードネームおよび調の解析・調号に応じた表記統一 |
| `audio2chordpro/lyrics.py` | 歌詞トークナイズ、読み・モーラ分解、表層文字列との対応付け |
| `audio2chordpro/vocals.py` | 音源からのボーカル分離、CTC 音響特徴量算出 |
| `audio2chordpro/align.py` | 歌メロ事前分布に基づくラティス強制アライメント |
| `audio2chordpro/melody.py` | 歌メロノート抽出（MIDI / SheetSage2 ラッパー） |
| `audio2chordpro/render.py` | モーラ・ノート対応付け、音節グリッド配置、ChordPro 文字列生成 |
| `audio2chordpro/song_info.py` | 楽曲メタデータとディレクティブ生成 |
| `audio2chordpro/providers.py` | MIDI / 歌詞供給インターフェース定義 |

## クレジット・ライセンス

- **漢字読みデータ**: [KANJIDIC2](https://www.edrdg.org/wiki/index.php/KANJIDIC_Project) (© EDRDG, CC BY-SA 4.0)
- **CTC 音響モデル**: [reazon-research/japanese-wav2vec2-base-rs35kh](https://huggingface.co/reazon-research/japanese-wav2vec2-base-rs35kh) (Apache-2.0)
- **歌メロ採譜モデル**: [SheetSage2](https://huggingface.co/m-a-p/SheetSage2) (CC BY-NC 4.0)
- **要素技術・ライブラリ**: [Demucs](https://github.com/facebookresearch/demucs), [pyopenjtalk-plus](https://github.com/tsukumijima/pyopenjtalk-plus), [alkana](https://github.com/cod-sushi/alkana.py)
