#!/usr/bin/env python3
"""Regenerate slides/DECK_SCRIPT.md from the addNotes blocks in build_deck.js.

The deck is the single source of the spoken prose, so a presenter reading the
notes pane and a presenter reading DECK_SCRIPT.md can never diverge. Run this
after editing any slide's notes:

    python3 slides/make_script.py

Word counts are the spoken prose only: anything in [brackets] is a stage
direction or a cumulative timestamp and is excluded. 155 wpm is a realistic rate
for technical material delivered with pauses.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DECK = HERE / "build_deck.js"
OUT = HERE / "DECK_SCRIPT.md"
WPM = 155
# Slides that may be dropped for a shorter running order, by leading number.
CUT = {"12", "15"}


def spoken_words(notes: str) -> int:
    clean = re.sub(r"\[[^\]]*\]", " ", notes)
    return len([w for w in clean.split() if re.search(r"\w", w)])


def main() -> int:
    src = DECK.read_text()
    slides = re.findall(
        r"// =+ (.+?)\n(.*?)(?=\n// =+ |\npres\.writeFile)", src, re.S
    )
    if not slides:
        print("no slides found in build_deck.js", file=sys.stderr)
        return 1

    entries = []
    for name, body in slides:
        match = re.search(r"s\.addNotes\(\s*`(.*?)`\);", body, re.S)
        notes = match.group(1).strip() if match else ""
        entries.append((name, notes, spoken_words(notes)))

    main_entries = [e for e in entries if not e[0].startswith("B")]
    backups = [e for e in entries if e[0].startswith("B")]
    total = sum(e[2] for e in main_entries)
    cut = total - sum(
        e[2] for e in main_entries if e[0].split(".")[0] in CUT
    )
    globus = next((e[2] for e in main_entries if e[0].startswith("11.")), 0)

    head: list[str] = ["# designagent walkthrough — speaking script\n"]
    head.append(
        """Companion to [`DECK_OUTLINE.md`](DECK_OUTLINE.md) (slide structure) and
[`CODE_FOR_DECK.md`](CODE_FOR_DECK.md) (the staged code blocks). Slide numbers and snippet IDs match
across all three. Derived against `main` @ `e8467e6`.

**This file is generated from the `addNotes` blocks in `build_deck.js`.** The deck is the single
source of the spoken prose, so a presenter reading from the notes pane and a presenter reading from
this file never diverge. Regenerate after editing the deck — the command is at the bottom.

**How to read it.** Plain prose is meant to be *said*. Anything in `[brackets]` is a stage direction
or a cumulative timestamp, and is excluded from the word counts.

**Pacing — measured, not estimated.** Counts are the actual spoken prose at 155 words/minute, a
realistic rate for technical material delivered with pauses.
"""
    )
    head.append("| Slide | Spoken | | Slide | Spoken |")
    head.append("|---|---|---|---|---|")
    half = (len(main_entries) + 1) // 2
    for i in range(half):
        left = main_entries[i]
        right = main_entries[i + half] if i + half < len(main_entries) else None
        unit = " min" if i == 0 else ""
        row = f"| {left[0]} | {left[2] / WPM:.1f}{unit} |"
        row += f" | {right[0]} | {right[2] / WPM:.1f} |" if right else " | | |"
        head.append(row)

    head.append("")
    head.append(
        f"**Main path (slides 1–18): {total} words = {total / WPM:.1f} minutes of speech.** "
        f"Backups add {sum(e[2] for e in backups) / WPM:.1f} min if used.\n"
    )
    head.append("| Order | What's in | Speech | Fits |")
    head.append("|---|---|---|---|")
    head.append(
        f"| **A · full** | slides 1–18 | **{total / WPM:.1f}** | a 30-minute slot, questions inline |"
    )
    head.append(
        f"| **B · twenty-five** | drop 12 (task agents) and 15 (frontend) | "
        f"**{cut / WPM:.1f}** | a 25-minute slot with real Q&A |"
    )
    head.append(
        f"| **C · twenty** | B, and fold 11 (Globus) into 10 as one sentence | "
        f"**~{(cut - globus) / WPM:.1f}** | a hard 20 with questions after |"
    )
    head.append(
        """
**Protect 8, 9, 10 and 18.** Those are the seam and the asks, and they are what this room came for.
Slides 12 and 15 are the designated cuts: the visualization-agent decision and the SSE details are
both recoverable in one sentence elsewhere. Do **not** compress 17 (status) — an audience that
catches you overclaiming stops believing the rest, and this deck's whole bet is that the honesty is
the credibility.

**Two things to say out loud even if nothing prompts them:** no HPC endpoint has ever run a task for
this agent (slide 17), and the variants in the worked example are heuristic proposals rather than
ProteinMPNN samples (slide 2).
"""
    )

    parts = ["\n".join(head), "---\n"]
    for name, notes, words in entries:
        parts.append(f"## {name} — *{words / WPM:.1f} min*\n")
        parts.append(notes + "\n")
    parts.append(
        """---

## Regenerating this file

The prose lives in `build_deck.js`. After editing a slide's `addNotes`:

```sh
python3 slides/make_script.py        # rewrites DECK_SCRIPT.md from build_deck.js
```
"""
    )
    OUT.write_text("\n".join(parts))
    print(f"wrote {OUT}")
    print(
        f"  main path: {total} words = {total / WPM:.1f} min  ·  "
        f"order B = {cut / WPM:.1f} min  ·  order C = {(cut - globus) / WPM:.1f} min"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
