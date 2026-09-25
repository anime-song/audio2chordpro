"""音源から AMT の MIDI（コード・調・拍）を作る

    from audio2chordpro.providers.midi import TsumugiMidiProvider

    midi = TsumugiMidiProvider(cache_dir="cache")("song.mp3")   # cache/tsumugi/out/song/merged/song_beat_chord.mid
"""

from .tsumugi import TsumugiMidiProvider

__all__ = ["TsumugiMidiProvider"]
