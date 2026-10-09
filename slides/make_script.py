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
CUT = {"9", "19", "20"}
# Order C drops two more: the layer map and the state slide are both recoverable
# in a sentence, and neither is a claim about one of the three dimensions.
CUT_C = CUT | {"5", "8"}


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
    cut_c = total - sum(
        e[2] for e in main_entries if e[0].split(".")[0] in CUT_C
    )

    head: list[str] = ["# PEAT walkthrough — speaking script\n"]
    head.append(
        """Companion to [`DECK_OUTLINE.md`](DECK_OUTLINE.md) (slide structure) and
[`CODE_FOR_DECK.md`](CODE_FOR_DECK.md) (the staged code blocks). Slide numbers and snippet IDs match
across all three. Derived against `main` @ `7e72b74`.

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
        f"**Main path (slides 1–23): {total} words = {total / WPM:.1f} minutes of speech.** "
        f"Backups add {sum(e[2] for e in backups) / WPM:.1f} min if used.\n"
    )
    head.append("| Order | What's in | Speech | Fits |")
    head.append("|---|---|---|---|")
    head.append(
        f"| **A · full** | slides 1–23, all three acts | **{total / WPM:.1f}** | "
        "a 30-minute slot with questions at the end |"
    )
    head.append(
        f"| **B · questions inline** | drop 9 (the lake), 19 (frontend) and 20 (deployment) | "
        f"**{cut / WPM:.1f}** | the 30-minute slot as briefed, or a 25 with questions after |"
    )
    head.append(
        f"| **C · twenty** | B, and drop 5 (the layer map) and 8 (state) | "
        f"**{cut_c / WPM:.1f}** | a hard 20 with questions after |"
    )
    head.append(
        """
**Protect 3 (the three dimensions), 13, 14 and 15 (the seam), 16 (measured and not measured), 22
(status) and 23 (asks).** The seam and the asks are what this room came for; 3 and 16 are what make
the performance claims honest rather than decorative. The three dividers are 20 seconds each and
cheap to keep — if the clock goes, cut a content slide, not the frame.

Do **not** compress 16 or 22. An audience that catches you overclaiming stops believing the rest,
and those two slides are where this deck does its volunteering.

**Three things to say out loud even if nothing prompts them:** no stage of the protocol has run on a
cluster, so every figure in `specs.py` is an estimate (slides 6 and 22); the concurrency figure is a
mean in-flight depth and not a speedup, because there is no serial baseline (slides 12 and 16); and
the variants in the worked example are heuristic proposals, not ProteinMPNN samples (slide 2).
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
        f"order B = {cut / WPM:.1f} min  ·  order C = {cut_c / WPM:.1f} min"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
