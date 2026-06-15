# The Page-Map CSV Contract

The 6-column CSV is the interface between segmentation/categorization and summarization.
It can be produced automatically by `/getPages` (Gemini) or authored by hand; `/summarize`
and the report routes consume it identically.

## Format

One row per sub-document, no header:

```
start_page,end_page,category,doc_date,injury_date,manual_flag
```

| # | Column | Meaning | Example | Notes |
|---|--------|---------|---------|-------|
| 1 | `start_page` | First page of the sub-document (1-based) | `10` | integer |
| 2 | `end_page` | Last page, inclusive | `14` | integer |
| 3 | `category` | Category number, selects the summarization prompt | `1` | 1-14 or `100` |
| 4 | `doc_date` | Document/encounter date | `4/20/2023` | `MM/DD/YYYY` or `-` |
| 5 | `injury_date` | Date of injury | `2/13/2023` | `MM/DD/YYYY` or `-` |
| 6 | `manual_flag` | Needs manual review | `x` | `x` to flag, else `-` |

`/summarize` requires **exactly 6 columns** per row; rows that do not parse are skipped.

## Example

```
10,14,1,4/20/2023,2/13/2023,-
28,30,3,4/6/2023,-,-
221,225,7,10/28/2024,3/22/2024,x
```

## Categories

Category numbers map to document-type groups in `mrr_ai/groups.py` and to prompts in
`mrr_ai/prompts.py` (`category_01` ... `category_14`, `category_100`). See the taxonomy
source in [Categories Jan 25, 2025.docx](./). Category `100` is the catch-all
("everything else" / administrative).

## How rows are produced

- **Automatic:** `/getPages` -> Gemini returns `{id,s,e,t,d,i,m}` per sub-document; the
  title `t` is fuzzy-matched to a category number; the row is emitted.
- **Manual:** staff author the CSV directly (the historically more accurate path).
