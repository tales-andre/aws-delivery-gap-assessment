"""Build the sample assessment: refresh the score table, render HTML, print PDF.

    python sample-report/build.py            # markdown + html + pdf (pdf needs Edge or Chrome)
    python sample-report/build.py --no-pdf   # markdown + html only

The score table between <!-- score:begin --> and <!-- score:end --> is always
regenerated from scoring.csv, so the numbers in the report cannot drift from
the reviewer's per-criterion statuses.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "evidence-kit"))

from evidence_kit import scoring  # noqa: E402

MD = HERE / "gap-assessment.md"
HTML = HERE / "gap-assessment.html"
PDF = HERE / "gap-assessment.pdf"
CSV = HERE / "scoring.csv"
BLOCK = re.compile(r"(<!-- score:begin -->\n)(.*?)(<!-- score:end -->)", re.S)

BROWSERS = [
    os.environ.get("BROWSER_BIN", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/microsoft-edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

CSS = """
@page { size: Letter; margin: 13mm 14mm 16mm 14mm; }
:root { --ink:#1d232b; --muted:#5b6672; --line:#d5dbe1; --accent:#0b5c8a; --warn:#8a4b00; --bg:#ffffff; }
* { box-sizing: border-box; }
body { font-family: "Segoe UI", Arial, sans-serif; color: var(--ink); background: var(--bg); font-size: 9.1pt; line-height: 1.38; margin: 0; }
main { max-width: 190mm; margin: 0 auto; }
h1 { font-size: 16pt; margin: 6px 0 8px; color: var(--accent); }
h2 { font-size: 11pt; margin: 14px 0 5px; color: var(--accent); border-bottom: 1px solid var(--line); padding-bottom: 2px; break-after: avoid; }
p, ul { margin: 4px 0 6px; }
li { margin: 2px 0; }
table { border-collapse: collapse; width: 100%; margin: 5px 0 8px; font-size: 8.4pt; }
th, td { border: 1px solid var(--line); padding: 3px 5px; vertical-align: top; text-align: left; }
th { background: #eef2f5; }
tr { break-inside: avoid; }
code { font-family: Consolas, monospace; font-size: 8.2pt; background: #f2f4f6; padding: 0 2px; }
.banner { border: 1.5px solid var(--warn); color: var(--warn); background: #fff6e8; font-weight: 600; padding: 6px 9px; border-radius: 4px; }
.footer { position: fixed; bottom: -11mm; left: 0; right: 0; text-align: center; font-size: 7.5pt; color: var(--muted); }
"""


def refresh_score_table() -> str:
    text = MD.read_text(encoding="utf-8")
    table = scoring.markdown_table(scoring.load(CSV))
    new = BLOCK.sub(lambda m: m.group(1) + table + "\n" + m.group(3), text)
    if new != text:
        MD.write_text(new, encoding="utf-8", newline="\n")
    return new


def render_html(md_text: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables"])
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Sample delivery gap assessment</title>"
        f"<style>{CSS}</style></head><body><main>{body}</main>"
        "<div class='footer'>Sample assessment: fictional company and vendor, written to show the method. Not a client deliverable.</div>"
        "</body></html>"
    )


def print_pdf(html_path: Path, pdf_path: Path) -> bool:
    browser = next((b for b in BROWSERS if b and Path(b).exists()), None) or shutil.which("msedge") or shutil.which("chromium")
    if not browser:
        print("No Edge/Chrome found: skipped PDF (set BROWSER_BIN).")
        return False
    subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return pdf_path.exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    md_text = refresh_score_table()
    HTML.write_text(render_html(md_text), encoding="utf-8", newline="\n")
    print(f"wrote {HTML.name}")
    if not a.no_pdf and print_pdf(HTML, PDF):
        print(f"wrote {PDF.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
