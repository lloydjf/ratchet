"""Assemble the apex site (ratchetci.dev) into one directory. Python standard library only.

Called by ``render_site`` in deploy/vm/lib.sh on every bootstrap and deploy::

    python3 deploy/vm/render_site.py --app-dir /opt/ratchet --out <release dir> \
        [--docs <mkdocs site dir>] --bench bench/results/2026-10-16 \
        --repo-url https://github.com/lloydjf/ratchet --ref <sha> --csp-out <caddy fragment>

Output layout (served by Caddy as static files)::

    index.html, site.css, 404.html                 deploy/vm/www/
    logo.svg                                       docs/assets/logo.svg, themed (also the favicon)
    fonts/*.woff2                                  Inter + JetBrains Mono (dashboard deps)
    docs/                                          the built MkDocs site
    bench/index.html (+ figures, results.json)     the benchmark report, rendered here

The MkDocs theme ships small inline scripts; the site CSP allows exactly those, by hash.
``--csp-out`` receives a Caddy fragment that sets ``{vars.docs_script_src}`` for ``/docs/*``.
Links in the report that point outside its directory are rewritten to the repository at
``--ref`` so they keep working on the web.
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

FONTS = {
    "inter-latin-wght-normal.woff2": "@fontsource-variable/inter/files",
    "jetbrains-mono-latin-wght-normal.woff2": "@fontsource-variable/jetbrains-mono/files",
}
LOGO_STYLE = (
    "<style>:root{fill:#79c0ff}@media (prefers-color-scheme: light){:root{fill:#0969da}}</style>"
)
INLINE_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)
NON_JS_TYPE = re.compile(r"""\btype\s*=\s*["']?(?!(?:text/javascript|module)\b)""", re.I)


# --- Markdown (the subset bench reports use) -------------------------------------------------


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

    def _table(self, rows: list[str]) -> str:
        head = self._cells(rows[0])
        out = ['<div class="table-wrap"><table>']
        if any(head):
            out.append("<thead><tr>")
            out.extend(f"<th>{self.inline(c)}</th>" for c in head)
            out.append("</tr></thead>")
        out.append("<tbody>")
        for row in rows[2:]:
            out.append("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in self._cells(row)))
            out.append("</tr>")
        out.append("</tbody></table></div>")
        return "".join(out)

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
  <title>__TITLE__ · Ratchet</title>
  <meta name="description"
        content="SkillRegress-Bench: how well Ratchet catches regressions in agent skill
                 edits, and what each judge configuration costs.">
  <link rel="icon" href="/logo.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/site.css">
</head>
<body class="report-page">
  <header class="topbar">
    <a class="brand" href="/" aria-label="Ratchet home">
      <img src="/logo.svg" alt="" width="24" height="24"><span>Ratchet</span>
    </a>
    <nav aria-label="Primary">
      <a href="https://demo.ratchetci.dev">Demo</a>
      <a href="/docs/">Docs</a>
      <a href="https://github.com/lloydjf/ratchet">GitHub</a>
    </nav>
  </header>
  <main class="report">
__BODY__
  </main>
  <footer class="foot">
    <span>Benchmark data CC BY 4.0 · code Apache-2.0</span><a href="/">ratchetci.dev</a>
  </footer>
</body>
</html>
"""


def render_bench(src: Path, out: Path, app_dir: Path, repo_url: str, ref: str) -> str:
    report = src / "report.md"
    out.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if _regular(item) and item.suffix.lower() in {".svg", ".png", ".json", ".csv"}:
            shutil.copy2(item, out / item.name)
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
    body = renderer.render(report.read_text(encoding="utf-8"))
    title = renderer.title or "SkillRegress-Bench results"
    (out / "index.html").write_text(_page(BENCH_TEMPLATE, title, body), encoding="utf-8")
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


def write_csp_fragment(path: Path, hashes: list[str]) -> None:
    for source in hashes:  # the fragment is Caddy config: allow nothing but hash sources
        if not re.fullmatch(r"'sha256-[A-Za-z0-9+/]+=*'", source):
            raise SystemExit(f"refusing to write CSP source {source!r}")
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
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--docs", type=Path)
    parser.add_argument("--bench", type=Path)
    parser.add_argument("--repo-url", default="https://github.com/lloydjf/ratchet")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--csp-out", type=Path)
    args = parser.parse_args(argv)

    app_dir: Path = args.app_dir
    out: Path = args.out
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
    print(f"render_site: wrote {out} ({len(hashes)} docs script hashes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
