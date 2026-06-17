"""Tests de la cache local (fase 0): clave por hash de contenido + split/save."""

from app import sonix_transcriber as st


def test_content_cache_key_format(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"hello world")
    key = st.content_cache_key(str(f), "audio.wav")

    stem, _, h = key.partition("__")
    assert stem == "audio"
    assert len(h) == 8
    assert all(c in "0123456789abcdef" for c in h)


def test_same_content_shares_hash(tmp_path):
    a = tmp_path / "a.wav"
    a.write_bytes(b"same bytes")
    b = tmp_path / "b.wav"
    b.write_bytes(b"same bytes")

    ka = st.content_cache_key(str(a), "a.wav")
    kb = st.content_cache_key(str(b), "b.wav")

    # Distinto nombre (stem) pero mismo contenido => mismo hash.
    assert ka.split("__")[0] != kb.split("__")[0]
    assert ka.split("__")[1] == kb.split("__")[1]


def test_diff_content_diff_key_same_name(tmp_path):
    f = tmp_path / "x.wav"
    f.write_bytes(b"one")
    k1 = st.content_cache_key(str(f), "x.wav")
    f.write_bytes(b"two")
    k2 = st.content_cache_key(str(f), "x.wav")

    assert k1 != k2
    assert k1.split("__")[0] == k2.split("__")[0] == "x"


def test_split_cached_and_save_roundtrip(tmp_path):
    cache_dir = str(tmp_path / "cache")
    media = tmp_path / "m.wav"
    media.write_bytes(b"data")
    items = [(str(media), "m__abcd1234")]

    # Nada cacheado todavía.
    to_upload, cached = st._split_cached(items, cache_dir)
    assert to_upload == items
    assert cached == []

    # Tras guardar, el mismo item se sirve desde cache.
    st._save_cache(cache_dir, "m__abcd1234", "texto transcrito")
    to_upload2, cached2 = st._split_cached(items, cache_dir)
    assert to_upload2 == []
    assert cached2 == [("m__abcd1234", "texto transcrito")]
