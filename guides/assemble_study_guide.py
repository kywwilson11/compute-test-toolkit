#!/usr/bin/env python3
"""Assemble the comprehensive study guide into one markdown file.

A title page + a single "How to Use" (00-cover.md), an unnumbered platform opener,
then 13 numbered chapters. Each chapter file holds only section-level (##) content; the
chapter H1 title is supplied here. We normalize the parts so the LaTeX 'report' build is
clean and consistent:
  * strip any manual "## 1." / "### 1.2" section numbers (report + --number-sections
    auto-numbers them; leaving manual numbers would double up);
  * convert "<Name> chapter §N.M" cross-refs to "<Name> chapter" (by-name, never stale)
    and drop bare "(§N.M)" pointers (the manual numbers don't survive auto-numbering);
  * the platform opener and its sections are unnumbered (it sits before Chapter 1);
  * ASCII-ify non-ASCII inside code (the monospace font can't render the glyph remap).
Output -> study-guide/_assembled.md, rendered by build-study-guide.sh.
"""
import pathlib
import re

GUIDES = pathlib.Path(__file__).resolve().parent
SG = GUIDES / "study-guide"
COVER = "study-guide/00-cover.md"

# (file, H1 chapter title, numbered?)
CHAPTERS = [
    ("study-guide/ch00-platform.md",    "The Platform and the Mission",                              False),
    ("study-guide/ch01-python.md",      "Python for Test Automation",                                True),
    ("study-guide/ch02-bash.md",        "Bash and Shell Scripting",                                  True),
    ("study-guide/ch03-linux.md",       "Linux for Bring-up and Hardware Debug",                     True),
    ("study-guide/ch04-pcie.md",        "PCIe: Architecture, Link Training, AER, and Diagnosis",     True),
    ("study-guide/ch05-nvme.md",        "NVMe and Storage",                                          True),
    ("study-guide/ch06-gpu.md",         "GPUs: Architecture, ECC/RAS, and Manufacturing Test",       True),
    ("study-guide/ch07-memory.md",      "DRAM and Memory (EDAC/RAS)",                                True),
    ("study-guide/ch08-automotive.md",  "Automotive and Serial Buses: GMSL, CAN, Ethernet, I2C/SPI/UART", True),
    ("study-guide/ch09-mfg-test.md",    "Manufacturing Test Principles",                             True),
    ("study-guide/ch10-math.md",        "Math, Probability, and Statistics for Test",                True),
    ("study-guide/ch11-networking.md",  "Networking: Fundamentals to the Manufacturing Floor",       True),
    ("study-guide/ch12-power-safety.md","Power, Bring-up, and Functional Safety",                    True),
    ("study-guide/ch13-control.md",     "Control Systems",                                           True),
    ("study-guide/ch14-embedded.md",    "Embedded Systems for the Compute Test Engineer",            True),
    ("study-guide/ch15-cicd-quality.md","Continuous Integration and Test-Engineering Quality Tooling", True),
]

# Non-ASCII glyphs that render in prose (header.tex maps them to math) but come out blank
# inside code, where the monospace font is used verbatim. ASCII-ify these ONLY in code.
_CODE_TRANS = {"→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "≈": "~=", "∼": "~",
               "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "·": "*", "−": "-",
               "∥": "||", "≪": "<<", "≫": ">>", "…": "..."}

# A section-number token: §, optional space, N or N.M or N.M.K, optional N.M–N.M range.
_NUMREF = r"§\s*\d+(?:\.\d+)*(?:\s*[–-]\s*\d+(?:\.\d+)*)?"


def _ascii_code(s: str) -> str:
    for k, v in _CODE_TRANS.items():
        s = s.replace(k, v)
    return s


def sanitize_code(text: str) -> str:
    """ASCII-ify non-ASCII inside fenced blocks and inline `code` spans; prose untouched."""
    text = re.sub(r"(?ms)^```.*?^```", lambda m: _ascii_code(m.group(0)), text)
    text = re.sub(r"(`+)(.+?)(\1)",
                  lambda m: m.group(1) + _ascii_code(m.group(2)) + m.group(1), text, flags=re.S)
    return text


def strip_heading_numbers(text: str) -> str:
    """Drop a manual leading section number from ##..###### headings ("## 1." / "### 1.2").
    Requires a dot so a title that merely starts with a number ("## 64-bit") is left alone."""
    text = re.sub(r"(?m)^(#{2,6})[ \t]+\d+(?:\.\d+)+[ \t]+", r"\1 ", text)   # 1.2 / 1.2.3
    text = re.sub(r"(?m)^(#{2,6})[ \t]+\d+\.[ \t]+", r"\1 ", text)           # 1.
    return text


def clean_section_refs(text: str) -> str:
    """Manual section numbers don't survive auto-numbering, so de-number the cross-refs:
    keep chapter-name references, drop bare numeric pointers."""
    text = re.sub(r"(\bchapter)\s*\(?" + _NUMREF + r"\)?", r"\1", text)  # "Math chapter §7.6" -> "Math chapter"
    text = re.sub(r"\s*\((?:see\s+)?" + _NUMREF + r"\)", "", text)        # " (§8)" / " (see §7.6)" -> drop
    text = re.sub(_NUMREF, "", text)                                      # any residual bare §N
    text = re.sub(r"[ \t]{2,}", " ", text)                               # tidy doubled spaces
    return text


def mark_unnumbered(body: str) -> str:
    """Append {.unnumbered} to a body's headings (for the pre-Chapter-1 platform opener)."""
    return re.sub(r"(?m)^(#{2,6}[ \t]+\S.*?)[ \t]*$", r"\1 {.unnumbered}", body)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def drop_redundant_title(body: str, title: str) -> str:
    """Some chapters open with a `## <Chapter Title>` heading duplicating the title we add at
    assembly. Drop it; if it was the sole `##` (all content nested under it at `###`), promote
    every deeper heading up one level so sections number as N.M, not N.M.K."""
    lines = body.split("\n")
    idx = next((i for i, ln in enumerate(lines) if re.match(r"^## ", ln)), None)
    if idx is None or _norm(re.sub(r"^##\s+", "", lines[idx])) != _norm(title):
        return body                                  # no leading ##, or it's a real section
    only_h2 = sum(1 for ln in lines if re.match(r"^## ", ln)) == 1
    del lines[idx]
    body = "\n".join(lines)
    if only_h2:
        body = re.sub(r"(?m)^###(#*\s)", r"##\1", body)   # ### -> ##, #### -> ###, ...
    return body


def strip_yaml(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[text.find("\n", end + 1) + 1:]
    return text


def chapter_body(rel: str) -> str:
    """A chapter file's body: YAML and any stray leading H1 removed; manual numbers stripped."""
    t = strip_yaml((GUIDES / rel).read_text()).lstrip("\n")
    if t.startswith("# "):                       # defensive: agents are told not to add one
        t = t.split("\n", 1)[1] if "\n" in t else ""
    return strip_heading_numbers(t).strip("\n")


def main() -> None:
    chunks = [(GUIDES / COVER).read_text().strip("\n") + "\n"]
    present = 0
    for rel, title, numbered in CHAPTERS:
        if not (GUIDES / rel).exists():
            print(f"  [pending] {rel}")
            continue
        present += 1
        body = drop_redundant_title(chapter_body(rel), title)
        if not numbered:                          # platform opener: chapter + its sections unnumbered
            body = mark_unnumbered(body)
        h1 = f"# {title}" + ("" if numbered else " {.unnumbered}")
        chunks.append(f"\n\n{h1}\n\n{body}\n")
    text = clean_section_refs("".join(chunks))
    out = SG / "_assembled.md"
    out.write_text(sanitize_code(text))
    words = len(out.read_text().split())
    print(f"assembled {out}  ({present}/{len(CHAPTERS)} chapters present, ~{words:,} words)")


if __name__ == "__main__":
    main()
