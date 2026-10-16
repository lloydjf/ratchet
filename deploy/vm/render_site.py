"""Assemble the apex site (ratchetci.dev) into one directory. Python standard library only.

Two callers, same output tree:

* the VM (edge mode ``caddy``): ``render_site`` in deploy/vm/lib.sh on every bootstrap and
  deploy, served by Caddy from /var/www/ratchet-site::

    python3 deploy/vm/render_site.py --app-dir /opt/ratchet --out <release dir> \
        [--docs <mkdocs site dir>] --bench bench/results/2026-10-16 \
        --repo-url https://github.com/lloydjf/ratchet --ref <sha> --csp-out <caddy fragment>

* Cloudflare static assets (edge mode ``tunnel``; .github/workflows/site.yml), or a developer
  machine. No VM paths, users or root needed; ``--app-dir`` defaults to this checkout and
  ``--bench`` to the newest ``bench/results/*/report.md``::

    python3 deploy/vm/render_site.py --out deploy/site/dist --docs site --cloudflare

Output layout::

    index.html, site.css, 404.html                 deploy/vm/www/
    logo.svg                                       docs/assets/logo.svg, themed (also the favicon)
    fonts/*.woff2                                  Inter + JetBrains Mono (dashboard deps)
    docs/                                          the built MkDocs site
    bench/index.html (+ bench.css, fonts, brand,   the benchmark report, rendered here
      figures, results.json)
    _headers, _redirects                           --cloudflare only (Cloudflare's formats)

The MkDocs theme ships small inline scripts; the site CSP allows exactly those, by hash.
``--csp-out`` receives a Caddy fragment that sets ``{vars.docs_script_src}`` for ``/docs/*``;
``--cloudflare`` puts the same hashes into the ``Content-Security-Policy`` of ``_headers``.
That CSP covers the whole site rather than ``/docs/*`` only: Cloudflare joins the values of
a header set by two matching rules with a comma, which browsers read as two policies that
must both pass, so a stricter ``/*`` policy would block the docs scripts. A hash allows only
that exact script, and no other page has inline scripts, so the site-wide list grants
nothing new; ``'unsafe-inline'`` is never used. Links in the report that point outside its
directory are rewritten to the repository at ``--ref`` so they keep working on the web.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import posixpath
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FONTS = {
    "inter-latin-wght-normal.woff2": "@fontsource-variable/inter/files",
    "jetbrains-mono-latin-wght-normal.woff2": "@fontsource-variable/jetbrains-mono/files",
}
LOGO_STYLE = (
    "<style>:root{fill:#79c0ff}@media (prefers-color-scheme: light){:root{fill:#0969da}}</style>"
)
# The apex CSP (the same policy as the Caddyfile's apex block, where {vars.docs_script_src}
# is filled in by the --csp-out fragment). Every font is self-hosted (the docs theme sets
# `font: false` and serves IBM Plex from docs/assets/fonts), so no font host is allowed; the
# docs' repository badge reads the star count from api.github.com.
SITE_CSP = (
    "default-src 'self'; script-src 'self' {vars.docs_script_src}; style-src 'self' "
    "'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self' "
    "https://api.github.com; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'none'"
)
# Same values as the Caddyfile's (security_headers) snippet.
SECURITY_HEADERS = (
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
)
# Relative rules only: Cloudflare's _redirects has no host-level rules, so www.<apex> -> apex
# is a zone Redirect Rule (deploy/vm/dns.md). html_handling "auto-trailing-slash" would answer
# /docs with a 307; these make the canonical form permanent.
REDIRECTS = (
    ("/docs", "/docs/", 301),
    ("/bench", "/bench/", 301),
)
HEADERS_LINE_MAX = 2000  # Cloudflare's per-line limit in _headers
HASH_SOURCE = re.compile(r"'sha256-[A-Za-z0-9+/]+=*'")
INLINE_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)
NON_JS_TYPE = re.compile(r"""\btype\s*=\s*["']?(?!(?:text/javascript|module)\b)""", re.I)


# --- Markdown (the subset bench reports use) -------------------------------------------------

# A table cell that is a figure: a rate, a cost, a latency, a count, optionally "(5/5)".
NUMERIC = re.compile(
    r"(?:n/a|[$-]?\d[\d.,]*(?: s)?)(?: / [$-]?\d[\d.,]*(?: s)?|/\d+)?(?: \((?:n=)?[\d/]+\))?"
)
FALSE_BLOCK = re.compile(r"false-block (?:N0|D1)")
OUTCOME_TAGS = {
    "pass": "pass",
    "improved": "pass",
    "drift": "drift",
    "regression": "block",
    "uncertain": "warn",
    "failed": "neutral",
}
DECISION_TAGS = {"PASS": "pass", "WARN": "warn", "BLOCK": "block"}


def _slug(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\s-]", "", html.unescape(text)).strip().lower()
    return re.sub(r"[\s_]+", "-", text)


class Renderer:
    """Block + inline Markdown: headings, paragraphs, lists, block quotes, fenced code
    (diff lines coloured), tables, links, images, code spans, bold and italic."""

    def __init__(self, link: Callable[[str], str]) -> None:
        self.link = link
        self.title = ""

    def inline(self, text: str) -> str:
        codes: list[str] = []

        def keep(match: re.Match[str]) -> str:
            codes.append(f"<code>{html.escape(match.group(1))}</code>")
            return f"\x00{len(codes) - 1}\x00"

        text = re.sub(r"`([^`]+)`", keep, text)
        text = html.escape(text, quote=True)

        def image(match: re.Match[str]) -> str:
            src = html.escape(self.link(html.unescape(match.group(2))), quote=True)
            return f'<img src="{src}" alt="{match.group(1)}" loading="lazy">'

        def anchor(match: re.Match[str]) -> str:
            href = html.escape(self.link(html.unescape(match.group(2))), quote=True)
            return f'<a href="{href}">{match.group(1)}</a>'

        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", image, text)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", anchor, text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
        return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)

    @staticmethod
    def _cells(row: str) -> list[str]:
        row = row.strip()
        row = row[1:] if row.startswith("|") else row
        row = row[:-1] if row.endswith("|") else row
        cells, cell, in_code = [], "", False
        for char in row:
            if char == "`":
                in_code = not in_code
            if char == "|" and not in_code:
                cells.append(cell.strip())
                cell = ""
            else:
                cell += char
        cells.append(cell.strip())
        return cells

    def _cell(self, header: str, cell: str) -> str:
        """A body cell: figures in tabular mono, the N0/D1 false-block rates in their decision
        colour (0 is a pass, anything else a false block), pair outcomes as colour tags."""
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell).strip()
        body = self.inline(cell)
        classes = []
        if NUMERIC.fullmatch(text):
            classes.append("num")
            if FALSE_BLOCK.fullmatch(header):
                value = re.match(r"-?\d+(?:\.\d+)?", text)
                if value:
                    classes.append("fb-pass" if float(value.group()) == 0 else "fb-block")
        elif len(text) > 60:
            classes.append("long")
        if header == "outcome" and text in OUTCOME_TAGS:
            body = f'<span class="tag {OUTCOME_TAGS[text]}">{body}</span>'
        attr = f' class="{" ".join(classes)}"' if classes else ""
        return f"<td{attr}>{body}</td>"

    def _table(self, rows: list[str]) -> str:
        head = self._cells(rows[0])
        body = [self._cells(row) for row in rows[2:]]
        # A long value repeated in every row (the E6 noise floor in Table 2) is said once,
        # under the table, so the figures stay on one line.
        plain = [[re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c).strip() for c in r] for r in body]
        lifted = [
            j
            for j, text in enumerate(plain[0] if plain else [])
            if len(body) > 1 and len(text) > 60 and all(len(r) > j and r[j] == text for r in plain)
        ]
        notes = [
            f'<p class="table-note"><strong>{self.inline(head[j] if j < len(head) else "")}'
            "</strong>"
            f", the same in every row: {self.inline(plain[0][j])}</p>"
            for j in lifted
        ]
        head = [c for j, c in enumerate(head) if j not in lifted]
        body = [[c for j, c in enumerate(r) if j not in lifted] for r in body]
        wide = " wide" if len(head) > 6 else ""  # results tables break out of the column
        out = [f'<div class="table-wrap{wide}"><table>']
        if any(head):
            out.append("<thead><tr>")
            out.extend(f"<th>{self.inline(c)}</th>" for c in head)
            out.append("</tr></thead>")
        names = [re.sub(r"[`*]", "", c).strip() for c in head]
        out.append("<tbody>")
        for cells in body:
            out.append(
                "<tr>"
                + "".join(
                    self._cell(names[i] if i < len(names) else "", c) for i, c in enumerate(cells)
                )
            )
            out.append("</tr>")
        out.append("</tbody></table></div>")
        return "".join(out + notes)

    @staticmethod
    def _code(lang: str, lines: list[str]) -> str:
        rendered = []
        for line in lines:
            safe = html.escape(line)
            kind = ""
            if lang == "diff":
                if line.startswith(("+++", "---")):
                    kind = "meta"
                elif line.startswith("+"):
                    kind = "add"
                elif line.startswith("-"):
                    kind = "del"
                elif line.startswith("@@"):
                    kind = "hunk"
            rendered.append(f'<span class="{kind}">{safe}</span>' if kind else safe)
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return f"<pre><code{cls}>" + "\n".join(rendered) + "</code></pre>"

    def render(self, markdown: str) -> str:
        lines = markdown.splitlines()
        out: list[str] = []
        i = 0
        separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue
            if stripped.startswith("```"):
                lang = stripped[3:].strip()
                block: list[str] = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    block.append(lines[i])
                    i += 1
                out.append(self._code(lang, block))
                i += 1
                continue
            heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
            if heading:
                level, text = len(heading.group(1)), self.inline(heading.group(2))
                if level == 1 and not self.title:
                    self.title = html.unescape(re.sub(r"<[^>]+>", "", text))
                out.append(f'<h{level} id="{_slug(text)}">{text}</h{level}>')
                i += 1
                continue
            if stripped.startswith("|") and i + 1 < len(lines) and separator.match(lines[i + 1]):
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rows.append(lines[i])
                    i += 1
                out.append(self._table(rows))
                continue
            if stripped.startswith(">"):
                quote = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    quote.append(lines[i].strip()[1:].strip())
                    i += 1
                out.append(f"<blockquote><p>{self.inline(' '.join(quote))}</p></blockquote>")
                continue
            bullet = re.match(r"^(\s*)([-*]|\d+\.)\s+", line)
            if bullet:
                ordered = bullet.group(2)[0].isdigit()
                items: list[str] = []
                while i < len(lines):
                    item = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", lines[i])
                    if item:
                        items.append(item.group(2))
                    elif lines[i].startswith((" ", "\t")) and lines[i].strip() and items:
                        items[-1] += " " + lines[i].strip()
                    else:
                        break
                    i += 1
                tag = "ol" if ordered else "ul"
                out.append(f"<{tag}>" + "".join(f"<li>{self.inline(t)}</li>" for t in items))
                out.append(f"</{tag}>")
                continue
            if re.match(r"^(-{3,}|\*{3,})$", stripped):
                out.append("<hr>")
                i += 1
                continue
            para = []
            while i < len(lines) and lines[i].strip():
                nxt = lines[i].strip()
                if para and (nxt.startswith(("```", "#", "|", ">")) or re.match(r"^[-*]\s", nxt)):
                    break
                para.append(nxt)
                i += 1
            out.append(f"<p>{self.inline(' '.join(para))}</p>")
        return "\n".join(out)


# --- Site assembly ------------------------------------------------------------------------------


def _copytree(src: Path, dst: Path) -> None:
    """Copy a tree without following or keeping symlinks (this runs as root: a link planted
    in a build directory must never publish a file outside it)."""
    shutil.copytree(src, dst, symlinks=True)
    for path in sorted(dst.rglob("*"), reverse=True):
        if path.is_symlink():
            path.unlink()


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _page(template: str, title: str, body: str) -> str:
    return template.replace("__TITLE__", html.escape(title)).replace("__BODY__", body)


BENCH_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark light">
  <title>__TITLE__ · Ratchet</title>
  <meta name="description"
        content="SkillRegress-Bench: how well Ratchet catches regressions in agent skill
                 edits, and what each judge configuration costs.">
  <link rel="icon" href="brand/ratchet-mark.svg" type="image/svg+xml">
  <link rel="preload" href="fonts/IBMPlexSans-400.woff2" as="font" type="font/woff2" crossorigin>
  <link rel="stylesheet" href="bench.css">
</head>
<body>
  <a class="skip" href="#report">Skip to the report</a>
  <header class="masthead">
    <a class="brand" href="/" aria-label="Ratchet home">
      <picture>
        <source srcset="brand/ratchet-mark-inverse.svg" media="(prefers-color-scheme: light)">
        <img src="brand/ratchet-mark.svg" alt="" width="28" height="28">
      </picture>
      <picture>
        <source srcset="brand/ratchet-wordmark-dark.svg" media="(prefers-color-scheme: light)">
        <img class="wordmark" src="brand/ratchet-wordmark-light.svg" alt="" width="80" height="17">
      </picture>
      <svg class="sieve-glyph" viewBox="0 0 28 14" aria-hidden="true" focusable="false">
        <path class="lane" d="M1 3.5H7M1 7.5H13M1 11.5H25" fill="none" stroke-width="1"/>
        <path class="col-0" d="M7 1V13" stroke-width="1.25"/>
        <path class="col-n" d="M13 1V13" stroke-width="1.25"/>
        <path class="col-s" d="M19 1V13" stroke-width="1.25"/>
        <path class="col-u" d="M25 1V13" stroke-width="1.25"/>
        <circle class="dot-block" cx="7" cy="3.5" r="2"/>
        <circle class="dot-pass" cx="13" cy="7.5" r="2"/>
        <circle class="dot-block" cx="25" cy="11.5" r="2"/>
      </svg>
      <span class="section">Benchmark</span>
    </a>
    <nav aria-label="Primary">
      <a href="https://demo.ratchetci.dev">Demo</a>
      <a href="/docs/">Docs</a>
      <a href="https://github.com/lloydjf/ratchet">GitHub</a>
    </nav>
  </header>
  <main class="report" id="report">
__BODY__
__DATASET__
  </main>
  <footer class="foot">
    <span>Benchmark data CC BY 4.0 · code Apache-2.0</span><a href="/">ratchetci.dev</a>
  </footer>
</body>
</html>
"""

# The benchmark page's own stylesheet: the docs' graphite tokens (dark first, light when the
# visitor prefers it), IBM Plex self-hosted beside it, hairlines, no shadows, no scripts.
BENCH_CSS = """@font-face { font-family: "IBM Plex Sans"; font-weight: 400; font-style: normal;
  font-display: swap; src: url("fonts/IBMPlexSans-400.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 400; font-style: italic;
  font-display: swap; src: url("fonts/IBMPlexSans-400-italic.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 500; font-style: normal;
  font-display: swap; src: url("fonts/IBMPlexSans-500.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 600 700; font-style: normal;
  font-display: swap; src: url("fonts/IBMPlexSans-600.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-weight: 400; font-style: normal;
  font-display: swap; src: url("fonts/IBMPlexMono-400.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-weight: 500 700; font-style: normal;
  font-display: swap; src: url("fonts/IBMPlexMono-500.woff2") format("woff2"); }

:root {
  --page: #201e1b; --panel: #272522; --raised: #2f2c29; --hair: #3a3733; --hair-strong: #514d48;
  --ink: #eceae6; --ink-2: #a8a49e; --ink-3: #8a857e; --steel: #9bb4c9;
  --pass: #3fb950; --warn: #d29922; --block: #ff7b72; --drift: #58a6ff;
  --nano: #7ee787; --super: #d2a8ff; --ultra: #ffa657; --scroll: #4a4641;
  --sans: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
  --measure: 70ch;
  color-scheme: dark;
}
@media (prefers-color-scheme: light) {
  :root {
    --page: #f3f1ec; --panel: #ffffff; --raised: #e9e6e0; --hair: #e6e3dd; --hair-strong: #cfcbc4;
    --ink: #1d1b18; --ink-2: #5e5a54; --ink-3: #6f6b66; --steel: #3e5f7a;
    --pass: #166c2f; --warn: #7a5200; --block: #b01d27; --drift: #0859b9;
    --nano: #1a6d32; --super: #6a3fc0; --ultra: #953c00; --scroll: #cfcbc4;
    color-scheme: light;
  }
}

*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
  scrollbar-color: var(--scroll) transparent; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 16px/26px var(--sans); caret-color: var(--steel);
}
::selection { background: color-mix(in srgb, var(--steel) 32%, transparent); }
a { color: var(--steel); text-decoration: underline 1px;
  text-decoration-color: color-mix(in srgb, var(--steel) 45%, transparent);
  text-underline-offset: 3px; }
a:hover { color: var(--ink); text-decoration-color: currentColor; }
:focus-visible { outline: 2px solid var(--steel); outline-offset: 2px; }
.skip { position: absolute; left: 16px; top: -48px; padding: 4px 8px; background: var(--panel);
  border: 1px solid var(--hair); border-radius: 6px; }
.skip:focus { top: 8px; }

.masthead {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  min-height: 48px; padding: 0 24px; background: var(--panel); border-bottom: 1px solid var(--hair);
}
.brand { display: inline-flex; align-items: center; gap: 10px; color: var(--ink);
  text-decoration: none; }
.brand:hover { color: var(--ink); }
.brand img { display: block; }
.brand .wordmark { height: 17px; width: auto; }
.sieve-glyph { display: block; width: 28px; height: 14px; flex: none; }
.sieve-glyph .lane { stroke: var(--hair-strong); }
.sieve-glyph .col-0 { stroke: var(--ink-3); }
.sieve-glyph .col-n { stroke: var(--nano); }
.sieve-glyph .col-s { stroke: var(--super); }
.sieve-glyph .col-u { stroke: var(--ultra); }
.sieve-glyph .dot-pass { fill: var(--pass); }
.sieve-glyph .dot-block { fill: var(--block); }
.brand .section { color: var(--ink-2); font-size: 15px; padding-left: 10px;
  border-left: 1px solid var(--hair); line-height: 18px; }
.masthead nav { display: flex; gap: 4px; }
.masthead nav a { color: var(--ink-2); text-decoration: none; font-size: 14px;
  padding: 4px 8px; border-radius: 6px; }
.masthead nav a:hover { color: var(--ink); background: var(--raised); }

.report { max-width: 1120px; margin: 0 auto; padding: 40px 24px 64px; }
.report > .table-wrap.wide { width: min(1392px, 100vw - 32px);
  margin-left: calc(50% - min(696px, 50vw - 16px)); }
.report > :is(p, ul, ol, blockquote, h1, h2, h3) { max-width: var(--measure); }
h1, h2, h3 { font-weight: 600; color: var(--ink); text-wrap: balance; }
h1 { font-size: 32px; line-height: 40px; letter-spacing: -0.015em; margin: 0 0 24px; }
h2 { font-size: 22px; line-height: 30px; margin: 48px 0 12px; padding-top: 24px;
  border-top: 1px solid var(--hair); max-width: none; }
h3 { font-size: 18px; line-height: 26px; margin: 0; }
p, ul, ol { margin: 0 0 16px; }
li { margin-bottom: 4px; }
strong { font-weight: 600; }
code, pre { font-family: var(--mono); font-variant-ligatures: none; }
code { font-size: 0.85em; background: var(--raised); padding: 1px 4px; overflow-wrap: anywhere; }
pre { margin: 0 0 16px; overflow-x: auto; background: var(--raised); border: 1px solid var(--hair);
  scrollbar-color: var(--scroll) transparent; scrollbar-width: thin; }
pre code { display: block; padding: 14px 16px; background: none; font-size: 13px; line-height: 20px;
  overflow-wrap: normal; white-space: pre; width: max-content; min-width: 100%; }
pre .add { color: var(--pass); }
pre .del { color: var(--block); }
pre .hunk { color: var(--steel); }
pre .meta { color: var(--ink-2); }
blockquote { margin: 0 0 16px; padding: 0 0 0 16px; border-left: 1px solid var(--hair-strong);
  color: var(--ink-2); }
blockquote p { margin: 0; }
hr { border: 0; border-top: 1px solid var(--hair); margin: 32px 0; }

.table-wrap { margin: 0 0 24px; overflow-x: auto; border: 1px solid var(--hair);
  scrollbar-color: var(--scroll) transparent; scrollbar-width: thin; }
table { border-collapse: collapse; width: 100%; font-size: 14px; line-height: 20px;
  font-variant-numeric: tabular-nums; }
th, td { padding: 8px 10px; text-align: left; vertical-align: top;
  border-top: 1px solid var(--hair); }
thead th { border-top: 0; background: var(--panel); font-weight: 600; vertical-align: bottom; }
tbody tr:hover { background: color-mix(in srgb, var(--raised) 50%, transparent); }
table:not(:has(thead)) tr:first-child td { border-top: 0; }
table:not(:has(thead)) td:first-child { color: var(--ink-2); white-space: nowrap; width: 9rem; }
td:not(.num) { min-width: 9ch; }
td:first-child { white-space: nowrap; }
td.num { font-family: var(--mono); font-size: 12.5px; white-space: nowrap;
  padding-left: 9px; padding-right: 9px; }
.table-note { margin-top: -12px; color: var(--ink-2); font-size: 14px; line-height: 22px; }
thead + tbody td.long { min-width: 44ch; }
td a { color: inherit; text-decoration: none; }
td a:hover { color: var(--steel); text-decoration: underline 1px; }
td.fb-pass { color: var(--pass); }
td.fb-block { color: var(--block); }
td.fb-pass a, td.fb-block a { color: inherit; }

.tag { display: inline-block; font-family: var(--mono); font-size: 12px; line-height: 18px;
  font-weight: 500; padding: 0 6px; color: var(--ink-2);
  border: 1px solid color-mix(in srgb, currentColor 42%, transparent); white-space: nowrap; }
.tag a { color: inherit; text-decoration: none; }
.tag.pass { color: var(--pass); }
.tag.warn { color: var(--warn); }
.tag.block { color: var(--block); }
.tag.drift { color: var(--drift); }

figure { margin: 8px 0 32px; max-width: 640px; }
figure img { display: block; width: 100%; height: auto; border: 1px solid var(--hair); }

details.example { border-top: 1px solid var(--hair); }
details.example:last-of-type { border-bottom: 1px solid var(--hair); }
details.example > summary { display: flex; align-items: baseline; gap: 12px; padding: 12px 0;
  cursor: pointer; list-style: none; }
details.example > summary::-webkit-details-marker { display: none; }
details.example > summary::before { content: ""; flex: none; width: 7px; height: 7px;
  border-right: 1.5px solid var(--ink-2); border-bottom: 1.5px solid var(--ink-2);
  transform: translateY(-3px) rotate(-45deg); transition: transform 160ms ease-out; }
details.example[open] > summary::before { transform: translateY(-4px) rotate(45deg); }
details.example > summary:hover h3 { color: var(--steel); }
.example-body { padding: 0 0 24px 19px; }
.example-body > :is(p, blockquote) { max-width: var(--measure); }

.foot { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap;
  padding: 16px 24px; border-top: 1px solid var(--hair); background: var(--panel);
  color: var(--ink-2); font-size: 12px; line-height: 16px; }
.foot a { color: var(--ink-2); }

@media (max-width: 720px) {
  .masthead { padding: 0 16px; }
  .masthead nav a { padding: 4px 6px; }
  .brand .section { display: none; }
  figure { overflow-x: auto; }
  figure img { min-width: 560px; }
  .report { padding: 24px 16px 48px; }
  h1 { font-size: 26px; line-height: 34px; }
  .example-body { padding-left: 0; }
}
@media (prefers-reduced-motion: reduce) {
  details.example > summary::before { transition: none; }
}
"""

BENCH_FONTS = (
    "IBMPlexSans-400.woff2",
    "IBMPlexSans-400-italic.woff2",
    "IBMPlexSans-500.woff2",
    "IBMPlexSans-600.woff2",
    "IBMPlexMono-400.woff2",
    "IBMPlexMono-500.woff2",
)
BENCH_BRAND = (
    "ratchet-mark.svg",
    "ratchet-mark-inverse.svg",
    "ratchet-wordmark-light.svg",
    "ratchet-wordmark-dark.svg",
)
STRONG_DECISION = re.compile(
    r"<strong>(?P<inner>(?:<a href=\"[^\"]*\">)?(?P<word>BLOCK|WARN|PASS)(?:</a>)?)</strong>"
)


def _bench_body(body: str) -> str:
    """Presentation passes over the rendered report: decisions in prose as colour tags, the
    figure as a <figure>, and each worked example as a collapsible <details>."""
    body = STRONG_DECISION.sub(
        lambda m: (
            f'<strong class="tag {DECISION_TAGS[m.group("word")]}">{m.group("inner")}</strong>'
        ),
        body,
    )
    body = re.sub(r"<p>(<img [^>]*>)</p>", r"<figure>\1</figure>", body)
    body = body.replace(
        "<th>all-Ultra mean</th>", '<th title="all-Ultra counterfactual, mean">Ultra mean</th>'
    )
    if 'class="table-note"' in body:  # the repeated E6 column was folded into a note
        body = body.replace(
            "against the E6 noise floor in the last column",
            "against the E6 noise floor stated under the table",
        )
    start = body.find('<h2 id="worked-examples">')
    if start == -1:
        return body
    end = body.find("<h2 ", start + 1)
    end = len(body) if end == -1 else end
    section = body[start:end]
    first = section.find("<h3 ")
    if first == -1:
        return body
    examples = []
    for part in re.split(r"(?=<h3 )", section[first:]):
        match = re.match(r"(<h3 [^>]*>.*?</h3>)\n?(.*)", part, re.S)
        if match:
            examples.append(
                f'<details class="example"><summary>{match.group(1)}</summary>\n'
                f'<div class="example-body">\n{match.group(2).strip()}\n</div></details>\n'
            )
    return body[:start] + section[:first] + "".join(examples) + body[end:]


def _dataset(repo_url: str, ref: str, results: bool) -> str:
    repo = repo_url.rstrip("/")
    text = (
        '<h2 id="dataset">Dataset</h2>\n<p>The data behind these numbers (skills, tasks, world, '
        "workspaces, edits, labels, fixtures and results) is "
        f'<a href="{html.escape(repo)}/tree/{html.escape(ref)}/bench">bench/</a> in the '
        "repository, CC BY 4.0, described in its "
        f'<a href="{html.escape(repo)}/blob/{html.escape(ref)}/bench/DATASHEET.md">datasheet</a>.'
    )
    if results:
        text += ' This run as data: <a href="results.json">results.json</a>.'
    return text + "</p>"


def render_bench(src: Path, out: Path, app_dir: Path, repo_url: str, ref: str) -> str:
    report = src / "report.md"
    out.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if _regular(item) and item.suffix.lower() in {".svg", ".png", ".json", ".csv"}:
            shutil.copy2(item, out / item.name)
    for folder, names, origin in (
        ("fonts", BENCH_FONTS, app_dir / "dashboard" / "public" / "fonts"),
        ("brand", BENCH_BRAND, app_dir / "docs" / "assets" / "brand"),
    ):
        (out / folder).mkdir(exist_ok=True)
        for name in names:
            if _regular(origin / name):
                shutil.copy2(origin / name, out / folder / name)
            else:
                print(f"render_site: {origin / name} not found", file=sys.stderr)
    (out / "bench.css").write_text(BENCH_CSS, encoding="utf-8")
    rel_src = src.resolve().relative_to(app_dir.resolve()).as_posix()

    def link(target: str) -> str:
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^/", target, re.I):
            return target
        path, _, frag = target.partition("#")
        joined = posixpath.normpath(posixpath.join(rel_src, path))
        if posixpath.dirname(joined) == rel_src and (out / posixpath.basename(joined)).exists():
            return target
        blob = f"{repo_url.rstrip('/')}/blob/{ref}/{joined}"
        return blob + (f"#{frag}" if frag else "")

    renderer = Renderer(link)
    body = _bench_body(renderer.render(report.read_text(encoding="utf-8")))
    title = renderer.title or "SkillRegress-Bench results"
    template = BENCH_TEMPLATE.replace(
        "__DATASET__", _dataset(repo_url, ref, (out / "results.json").is_file())
    )
    (out / "index.html").write_text(_page(template, title, body), encoding="utf-8")
    return title


def script_hashes(docs: Path) -> list[str]:
    """CSP sources for every distinct inline executable script in the built docs."""
    found: set[str] = set()
    for page in docs.rglob("*.html"):
        for match in INLINE_SCRIPT.finditer(page.read_text(encoding="utf-8", errors="replace")):
            attrs = match.group("attrs")
            if re.search(r"\bsrc\s*=", attrs, re.I) or NON_JS_TYPE.search(attrs):
                continue  # external file (allowed by 'self') or data (JSON is never executed)
            digest = hashlib.sha256(match.group("body").encode("utf-8")).digest()
            found.add("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    return sorted(found)


def _check_hashes(hashes: list[str]) -> None:
    for source in hashes:  # the output is server config: allow nothing but hash sources
        if not HASH_SOURCE.fullmatch(source):
            raise SystemExit(f"refusing to write CSP source {source!r}")


def site_csp(hashes: list[str]) -> str:
    _check_hashes(hashes)
    csp = SITE_CSP.replace("{vars.docs_script_src}", " ".join(hashes))
    return re.sub(r" +;", ";", re.sub(r" +", " ", csp))


def write_cloudflare_files(out: Path, hashes: list[str]) -> None:
    """``_headers`` (security headers + the CSP with the docs hashes) and ``_redirects`` for
    Cloudflare static assets. ``404.html`` comes from deploy/vm/www; the docs have their own
    ``docs/404.html``, which Cloudflare's ``404-page`` handling serves under /docs/."""
    lines = ["# Generated by deploy/vm/render_site.py --cloudflare; do not edit.", "/*"]
    lines += [f"  {name}: {value}" for name, value in SECURITY_HEADERS]
    lines.append(f"  Content-Security-Policy: {site_csp(hashes)}")
    too_long = [line for line in lines if len(line) > HEADERS_LINE_MAX]
    if too_long:  # Cloudflare would drop the rule; fail the build instead of shipping no CSP
        raise SystemExit(f"_headers line over {HEADERS_LINE_MAX} characters: {too_long[0][:80]}")
    (out / "_headers").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rules = ["# Generated by deploy/vm/render_site.py --cloudflare; do not edit."]
    rules += [f"{src} {dst} {code}" for src, dst, code in REDIRECTS]
    (out / "_redirects").write_text("\n".join(rules) + "\n", encoding="utf-8")


def latest_bench(app_dir: Path) -> Path | None:
    """The newest dated run, bench/results/<YYYY-MM-DD...>/report.md (smoke runs are skipped)."""
    reports = sorted(
        path
        for path in (app_dir / "bench" / "results").glob("*/report.md")
        if re.match(r"\d{4}-\d{2}-\d{2}", path.parent.name)
    )
    return reports[-1].parent if reports else None


def write_csp_fragment(path: Path, hashes: list[str]) -> None:
    _check_hashes(hashes)
    lines = ["# Generated by deploy/vm/render_site.py on every deploy; do not edit."]
    if hashes:
        lines.append(f'vars /docs/* docs_script_src "{" ".join(hashes)}"')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--app-dir", type=Path, default=REPO_ROOT, help="the checkout (default: this one)"
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory (replaced)")
    parser.add_argument("--docs", type=Path, help="the built MkDocs site (mkdocs build)")
    parser.add_argument(
        "--bench", type=Path, help="report directory (default: newest bench/results/*)"
    )
    parser.add_argument("--repo-url", default="https://github.com/lloydjf/ratchet")
    parser.add_argument("--ref", default="main", help="commit for links out of the report")
    parser.add_argument("--csp-out", type=Path, help="Caddy fragment with the docs hashes")
    parser.add_argument(
        "--cloudflare", action="store_true", help="also write _headers and _redirects"
    )
    args = parser.parse_args(argv)

    app_dir: Path = args.app_dir.resolve()
    out: Path = args.out
    if args.bench is None:
        args.bench = latest_bench(app_dir)
    if out.resolve() in (app_dir, *app_dir.parents):
        raise SystemExit(f"refusing to replace {out}: it contains the checkout")
    if out.exists():
        shutil.rmtree(out)
    _copytree(app_dir / "deploy" / "vm" / "www", out)
    logo = app_dir / "docs" / "assets" / "logo.svg"
    if _regular(logo):
        # The source mark is fill="currentColor"; as an image it gets the accent per theme.
        svg = logo.read_text(encoding="utf-8").replace(' fill="currentColor"', "", 1)
        head, sep, rest = svg.partition(">")
        (out / "logo.svg").write_text(head + sep + LOGO_STYLE + rest, encoding="utf-8")

    fonts = out / "fonts"
    fonts.mkdir(exist_ok=True)
    modules = app_dir / "dashboard" / "node_modules"
    for name, folder in FONTS.items():
        src = modules / folder / name
        if _regular(src):
            shutil.copy2(src, fonts / name)
        else:
            print(f"render_site: {name} not found (system fonts used)", file=sys.stderr)

    hashes: list[str] = []
    if args.docs and args.docs.is_dir():
        _copytree(args.docs, out / "docs")
        hashes = script_hashes(out / "docs")
    else:
        print("render_site: no docs site; /docs/ is not published", file=sys.stderr)

    if args.bench and _regular(args.bench / "report.md"):
        title = render_bench(args.bench, out / "bench", app_dir, args.repo_url, args.ref)
        print(f"render_site: /bench/ <- {args.bench} ({title})", file=sys.stderr)
    else:
        print(
            f"render_site: no report.md in {args.bench}; /bench/ is not published", file=sys.stderr
        )

    if args.csp_out:
        write_csp_fragment(args.csp_out, hashes)
    if args.cloudflare:
        write_cloudflare_files(out, hashes)
    print(f"render_site: wrote {out} ({len(hashes)} docs script hashes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
