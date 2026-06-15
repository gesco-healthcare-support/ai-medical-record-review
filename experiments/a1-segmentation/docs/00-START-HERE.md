# Start Here (read this first)

This folder is an **experiment**, not production code. Its only job is to answer
one yes/no question with evidence:

> Can a small machine-learning model find where each document starts inside a big
> merged medical-record PDF, **better than the current "cut every 100 pages"
> method**?

If yes, it's worth building for real. If no, we learned that cheaply and move on.

## The problem in plain English

When a patient's case comes in, it's one giant PDF (hundreds to thousands of
pages) made of many separate documents stapled together: doctor notes, MRI
reports, depositions, claim forms, and so on. To summarise it, we first need to
know **where each document begins and ends**.

The current software splits the giant PDF into fixed 100-page blocks and asks an
AI to find documents inside each block. The problem: a document that crosses a
100-page line (say, pages 95-110) gets sliced in half. We are testing a smarter
approach that decides, **for every single page, "does a new document start
here?"** - which can't slice documents by accident.

## How this folder is organised

| Path | What it is |
|------|-----------|
| `docs/00-START-HERE.md` | This file. |
| `docs/01-GLOSSARY.md` | Every metric and term, explained simply. Read this before any results. |
| `docs/02-DATA-AND-EXPANSION.md` | What data we have, the required format, and how to get more. |
| `src/` | The code: OCR, labeling, and the experiment runner. |
| `cache/` | Slow-to-compute intermediate data (OCR text, etc.). Safe to delete; it rebuilds. |
| `outputs/` | One folder per experiment, each with a self-explaining `report.md`. |
| `EXPERIMENT-LOG.md` | A running scoreboard of every experiment we've tried. |

## How to read a result

You should **not** need anyone to interpret results for you. Every experiment
writes an `outputs/<experiment>/report.md` that starts with a one-sentence
plain-English verdict ("this works" / "this is no better than guessing" / etc.),
then explains why, then gives the next step. The raw numbers and their meanings
are all defined in the glossary.

## Current status (latest first)

- **Dataset expanded to 11 cases (~6,950 pages, ~842 boundaries)** and all OCR'd.
  The 8 ROR-converted cases were spot-checked and trusted. See
  `docs/02-DATA-AND-EXPANSION.md`.
- **EXP-002 (MiniLM embeddings + Logistic Regression):** on 3 cases it looked
  promising (MODERATE), but **on all 11 cases it dropped to AT CHANCE** - the
  small-sample signal did not generalise.
- **EXP-001 (TF-IDF + Logistic Regression):** AT CHANCE on both 3 and 11 cases.
- **Bottom line so far:** simple **text-only** per-page classification does not
  solve A1 on the real, diverse data. The likely reason is that the boundary cue
  is **visual/structural** (a new letterhead or form layout), not textual -
  especially in the big EHR dumps where every page shares the same header text.
- **Open next directions** (not yet tried): page-image / layout features; a
  measurement of the current Gemini-per-chunk approach to know the real target;
  sequence models over pages. See `EXPERIMENT-LOG.md` for live numbers.
