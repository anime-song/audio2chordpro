"""うたてんのパーサ（ネットにはつながない。HTML はサイトの構造をまねた架空のもの）"""

from audio2chordpro.providers.lyrics import fetch, same_title
from audio2chordpro.providers.lyrics.utaten import UtaTen

SEARCH_HTML = """
<table class="searchResult artistLyricList">
  <tr><th class="searchResult__head">楽曲・タイトル</th><th>アーティスト</th><th>歌詞・歌い出し</th></tr>
  <tr>
    <td><p class="searchResult__title"><a href="/lyric/ab123/">  朝のうた  </a></p></td>
    <td class="searchResult__artist">
      <p><a href="/artist/1/">  歌手A  </a></p>
      <div class="searchResult__lyricist">
        <p>作詞：<span class="songWriters"><a href="/songWriter/1/">作家B</a></span></p>
        <p>作曲：<span class="songWriters"><a href="/songWriter/2/">作家C</a><a href="/songWriter/3/">作家D</a></span></p>
        <p>編曲：<span class="songWriters"><a href="/songWriter/3/">作家D</a></span></p>
      </div>
    </td>
    <td class="lyricList__beginning"><a href="/lyric/ab123/">あさの ひかり</a></td>
  </tr>
</table>
"""

SONG_HTML = """
<h2 class="newLyricTitle__main">
  朝のうた <span class="newLyricTitle_afterTxt">歌詞</span>
  <span class="newLyricTitle__subTitle">「朝の番組」テーマソング</span>
</h2>
<div class="newLyricTitle__kana">よみ：あさのうた</div>
<dl class="newLyricWork">
  <dt class="newLyricWork__name"><h3><a href="/artist/lyric/1">歌手A</a></h3></dt>
  <dd class="newLyricWork__date">2020.01.01&nbsp;リリース</dd>
  <dt class="newLyricWork__title">作詞</dt><dd class="newLyricWork__body"><a href="/songWriter/1/">作家B</a></dd>
  <dt class="newLyricWork__title">作曲</dt><dd class="newLyricWork__body"><a>作家C</a><a>作家D</a></dd>
  <dt class="newLyricWork__title">編曲</dt><dd class="newLyricWork__body"><a>作家D</a></dd>
</dl>
<div class="lyricBody"><div class="medium"><div class="hiragana">
  あさの<span class="ruby"><span class="rb">光</span><span class="rt">ひかり</span></span>に<br />
<span class="ruby"><span class="rb">言葉</span><span class="rt">ことば</span></span>をのせて<br />
<br />
Hello world<br />
</div></div></div>
"""


def test_parse_search():
    (hit,) = UtaTen().parse_search(SEARCH_HTML)
    assert (hit.title, hit.artist, hit.url) == ("朝のうた", "歌手A", "https://utaten.com/lyric/ab123/")
    assert (hit.lyricist, hit.composer, hit.arranger) == ("作家B", "作家C・作家D", "作家D")
    assert hit.beginning == "あさの ひかり"


def test_parse_song():
    page = UtaTen().parse_song(SONG_HTML, "https://utaten.com/lyric/ab123/")
    assert page.info.title == "朝のうた" and page.info.artist == "歌手A"
    assert (page.info.lyricist, page.info.composer, page.info.arranger) == ("作家B", "作家C・作家D", "作家D")
    assert page.lyrics == "あさの光に\n言葉をのせて\n\nHello world\n"
    assert page.lyrics_ruby == "あさの光(ひかり)に\n言葉(ことば)をのせて\n\nHello world\n"
    assert page.extra == {"subtitle": "「朝の番組」テーマソング", "title_kana": "あさのうた", "release": "2020.01.01"}


def test_fetch_rejects_unknown_site():
    try:
        fetch("https://example.com/lyric/1/")
    except ValueError:
        return
    raise AssertionError("未対応のサイトは ValueError")


def test_same_title():
    assert same_title("朝のうた", "朝のうた")
    assert same_title("朝のうた(「朝の番組」テーマソング)", "朝のうた")
    assert same_title("ＡＳＡ　ｎｏ ＵＴＡ", "asa no uta")
    assert not same_title("朝のうたごえ", "朝のうた")
