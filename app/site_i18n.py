"""Translations of the public website (landing, What & how, Data, Team, Limits, Contact, Your data).

The templates stay in English. After a page renders, translate() swaps each block of text for its translation:
a "segment" is the inner HTML of the smallest element that holds text and no block-level children (a <p>, an <h2>,
a <li>...), so inline markup (<strong>, <a>, <em>) is translated together with its sentence. A few attributes
(alt, title, aria-label, meta descriptions, data-i18n-*) are translated too. Anything inside <script>, <style>,
<svg> or an element with translate="no" is left alone.

Segments live in app/locales/en.json ({id: English}), written by scripts/i18n_extract.py; each language has
app/locales/<code>.json ({id: translation}). A segment without a translation stays in English.

The visitor picks a language with ?lang=<code>; it is remembered in a cookie, so every page after that opens in it.
English is the default.
"""
import hashlib
import html
import json
import re
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

LOCALES = Path(__file__).parent / "locales"
COOKIE = "fp_lang"
DEFAULT = "en"

# code -> (name in its own language, short label, text direction)
LANGS = {
    "en": ("English", "EN", "ltr"),
    "es": ("Español", "ES", "ltr"),
    "fr": ("Français", "FR", "ltr"),
    "de": ("Deutsch", "DE", "ltr"),
    "pt": ("Português", "PT", "ltr"),
    "ar": ("العربية", "AR", "rtl"),
    "tr": ("Türkçe", "TR", "ltr"),
    "ru": ("Русский", "RU", "ltr"),
    "hi": ("हिन्दी", "HI", "ltr"),
    "bn": ("বাংলা", "BN", "ltr"),
    "id": ("Bahasa Indonesia", "ID", "ltr"),
    "pcm": ("Naijá (Pidgin)", "PCM", "ltr"),
    "zh": ("中文（简体）", "ZH", "ltr"),
    "ja": ("日本語", "JA", "ltr"),
}
# Google Noto fonts for scripts Inter / IBM Plex Mono don't cover.
FONTS = {"ar": "Noto Sans Arabic", "hi": "Noto Sans Devanagari", "bn": "Noto Sans Bengali",
         "zh": "Noto Sans SC", "ja": "Noto Sans JP"}

# Pages whose HTML is translated. The field report and the chat simulator are not: the bot already answers in
# the farmer's language.
PAGES = ("/", "/what-and-how", "/data-and-science", "/team", "/limits", "/contact", "/privacy")

BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "section", "article", "header", "footer",
         "nav", "main", "aside", "table", "thead", "tbody", "tr", "td", "th", "form", "figure", "figcaption",
         "blockquote", "pre", "details", "summary", "dl", "dt", "dd", "svg", "button", "label", "select", "option"}
SKIP = {"script", "style", "svg", "noscript", "template"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ATTRS = re.compile(r'(\s(?:alt|title|aria-label|placeholder|data-i18n-[a-z-]+)=")([^"]*)(")')
META = re.compile(r'(<meta\s+(?:name|property)="(?:description|og:title|og:description)"\s+content=")([^"]*)(")')


def seg_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _has_letters(text: str) -> bool:
    return any(c.isalpha() for c in html.unescape(re.sub(r"<[^>]+>", "", text)))


class _Node:
    __slots__ = ("tag", "inner_start", "inner_end", "children", "texts", "skip")

    def __init__(self, tag, inner_start, skip):
        self.tag, self.inner_start, self.inner_end, self.skip = tag, inner_start, None, skip
        self.children, self.texts = [], []  # texts: (start, end) of direct text runs


class _Tree(HTMLParser):
    """Builds an element tree with source offsets, so segments can be replaced without re-serializing the page."""

    def __init__(self, src: str):
        super().__init__(convert_charrefs=False)
        self.src = src
        self.lines = [0]
        for m in re.finditer("\n", src):
            self.lines.append(m.end())
        self.root = _Node("#root", 0, False)
        self.stack = [self.root]

    def _off(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._off()
        end = start + len(self.get_starttag_text())
        parent = self.stack[-1]
        skip = parent.skip or tag in SKIP or dict(attrs).get("translate") == "no"
        node = _Node(tag, end, skip)
        parent.children.append(node)
        if tag in VOID:
            node.inner_end = end
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        start = self._off()
        end = start + len(self.get_starttag_text())
        node = _Node(tag, end, True)
        node.inner_end = end
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        pos = self._off()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                for n in self.stack[i:]:
                    n.inner_end = pos
                del self.stack[i:]
                return

    def _text(self, start, length):
        if not self.stack[-1].skip:
            self.stack[-1].texts.append((start, start + length))

    def handle_data(self, data):
        self._text(self._off(), len(data))

    def handle_entityref(self, name):
        self._text(self._off(), len(name) + 2)

    def handle_charref(self, name):
        self._text(self._off(), len(name) + 3)


def _has_text(n: _Node, src: str) -> bool:
    if n.skip:
        return False
    return any(src[a:b].strip() for a, b in n.texts) or any(_has_text(c, src) for c in n.children)


def _has_block(n: _Node) -> bool:
    return any(c.tag in BLOCK or c.skip or _has_block(c) for c in n.children)


def _segments(n: _Node, src: str, out: list):
    """(start, end) source ranges to translate, in document order."""
    if n.skip or (n.inner_end is None and n.tag != "#root"):
        return
    if n.tag != "#root" and _has_text(n, src) and not _has_block(n):
        out.append((n.inner_start, n.inner_end))
        return
    # Mixed content: translate direct text runs on their own, then recurse into children.
    items = [(a, b, None) for a, b in n.texts] + [(c.inner_start, 0, c) for c in n.children]
    for a, b, child in sorted(items, key=lambda t: t[0]):
        if child is None:
            if src[a:b].strip():
                out.append((a, b))
        else:
            _segments(child, src, out)


def translate(page: str, lookup) -> str:
    """Replace every segment and translatable attribute for which lookup(english) returns a string."""
    tree = _Tree(page)
    tree.feed(page)
    tree.close()
    ranges: list = []
    _segments(tree.root, page, ranges)
    parts, last = [], 0
    for a, b in ranges:
        raw = page[a:b]
        key = norm(raw)
        if not key or not _has_letters(key):
            continue
        new = lookup(key)
        if new is None:
            continue
        lead = raw[: len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        parts += [page[last:a], lead, new, trail]
        last = b
    parts.append(page[last:])
    out = "".join(parts)

    def attr(m):
        val = m.group(2)
        if not _has_letters(val):
            return m.group(0)
        new = lookup(norm(val))
        return m.group(0) if new is None else m.group(1) + new.replace('"', "&quot;") + m.group(3)

    out = META.sub(attr, out)
    # Attributes only outside <script>/<style>, and never in translate="no" or <svg> blocks.
    chunks = re.split(r"(?is)(<script\b.*?</script>|<style\b.*?</style>|<svg\b.*?</svg>)", out)
    return "".join(c if i % 2 else ATTRS.sub(attr, c) for i, c in enumerate(chunks))


@lru_cache(maxsize=None)
def table(lang: str) -> dict[str, str]:
    p = LOCALES / f"{lang}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def localize(page: str, lang: str) -> str:
    """Translate a rendered English page into lang and set its lang/dir and fonts."""
    if lang == DEFAULT or lang not in LANGS:
        return page
    t = table(lang)
    out = translate(page, lambda key: t.get(seg_id(key)))
    direction = LANGS[lang][2]
    out = re.sub(r'<html lang="en"', f'<html lang="{lang}" dir="{direction}"', out, count=1)
    font = FONTS.get(lang)
    if font:
        fam = font.replace(" ", "+")
        head = (f'<link href="https://fonts.googleapis.com/css2?family={fam}:wght@400;500;600&display=swap" rel="stylesheet">'
                f'<style>:root{{--sans:"Inter","{font}",system-ui,sans-serif;'
                f'--mono:"IBM Plex Mono","{font}",ui-monospace,monospace}}</style>')
        out = out.replace("</head>", head + "</head>", 1)
    return out


def pick(request) -> str:
    """Language for this request: ?lang= wins, then the cookie, then English."""
    q = request.query_params.get("lang")
    if q in LANGS:
        return q
    c = request.cookies.get(COOKIE)
    return c if c in LANGS else DEFAULT


def collect(page: str) -> list[str]:
    """English segments of a page, in order (used by scripts/i18n_extract.py)."""
    found: list[str] = []
    translate(page, lambda key: found.append(key))
    return found


def check(lang: str) -> list[str]:
    """Problems in a language file: missing ids, unknown ids, or HTML tags that differ from the English segment."""
    src = json.loads((LOCALES / "en.json").read_text())
    p = LOCALES / f"{lang}.json"
    if not p.exists():
        return [f"{p.name} does not exist"]
    tr = json.loads(p.read_text())
    problems = [f"missing {k}: {src[k][:60]!r}" for k in src if k not in tr]
    problems += [f"unknown id {k}" for k in tr if k not in src]
    tag = re.compile(r"<[^>]+>")
    for k, v in tr.items():
        if k in src and sorted(tag.findall(src[k])) != sorted(tag.findall(v)):
            problems.append(f"tags differ in {k}: {src[k][:60]!r}")
    return problems
