"""Tests de los helpers puros del scraper (sin red ni navegador)."""

from app.sonix_transcriber import _href_to_id, _xpath_lower


def test_href_to_id_plain():
    assert _href_to_id("/recordings/abc123") == "abc123"


def test_href_to_id_trailing_slash():
    assert _href_to_id("/recordings/abc123/") == "abc123"


def test_href_to_id_with_query():
    assert _href_to_id("https://my.sonix.ai/recordings/abc123?foo=bar") == "abc123"


def test_href_to_id_with_subpath():
    assert _href_to_id("/recordings/abc123/edit") == "abc123"


def test_xpath_lower_builds_translate_expr():
    expr = _xpath_lower()
    assert expr.startswith("translate(.,")
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" in expr
    assert "abcdefghijklmnopqrstuvwxyz" in expr
