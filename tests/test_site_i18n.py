"""Website translations: the page machinery, the language picker and the completeness of every language file."""
import json

import pytest
from fastapi.testclient import TestClient

from app import site_i18n
from app.main import app

client = TestClient(app)
LANGS = [code for code in site_i18n.LANGS if code != "en"]


def test_translate_keeps_page_untouched_without_translations():
    page = '<html lang="en"><body><p>Hello <b>farm</b></p><svg><text>x</text></svg><script>var a = "Hi there";</script></body></html>'
    assert site_i18n.translate(page, lambda key: None) == page


def test_translate_swaps_segments_and_attributes():
    page = ('<div><h2>Hello <em>field</em></h2><img alt="A field" src="x.png"><p translate="no">English</p>'
            '<script>var t = "Hello";</script></div>')
    tr = {"Hello <em>field</em>": "Hola <em>campo</em>", "A field": "Un campo", "English": "Inglés"}
    out = site_i18n.translate(page, tr.get)
    assert "<h2>Hola <em>campo</em></h2>" in out and 'alt="Un campo"' in out
    assert "<p translate=\"no\">English</p>" in out and 'var t = "Hello";' in out


def test_english_is_the_default():
    r = client.get("/what-and-how")
    assert r.status_code == 200 and '<html lang="en"' in r.text


def test_picked_language_is_remembered_across_pages():
    c = TestClient(app)
    r = c.get("/?lang=es")
    assert '<html lang="es" dir="ltr"' in r.text and r.cookies.get(site_i18n.COOKIE) == "es"
    assert '<html lang="es"' in c.get("/team").text
    assert '<html lang="en"' in c.get("/team?lang=en").text


def test_arabic_is_right_to_left():
    assert '<html lang="ar" dir="rtl"' in client.get("/contact?lang=ar").text


def test_english_segments_are_up_to_date():
    """Run `uv run python scripts/i18n_extract.py` after editing a public page."""
    import scripts.i18n_extract as extract

    on_disk = json.loads((site_i18n.LOCALES / "en.json").read_text())
    assert extract.english_segments() == on_disk


@pytest.mark.parametrize("lang", LANGS)
def test_language_file_is_complete_and_keeps_html(lang):
    assert site_i18n.check(lang) == []


def test_a_menu_of_links_is_translated_link_by_link():
    page = '<div class="mnav"><a href="/a" class="on">WHAT &amp; HOW</a>\n <a href="/b">TEAM</a></div>'
    out = site_i18n.translate(page, {"WHAT &amp; HOW": "QUÉ Y CÓMO", "TEAM": "EQUIPO"}.get)
    assert out == '<div class="mnav"><a href="/a" class="on">QUÉ Y CÓMO</a>\n <a href="/b">EQUIPO</a></div>'
