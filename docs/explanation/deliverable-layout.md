# Deliverable layout

The documents the app produces are read by clients, not by the app's users, and the standard they
are held to is the reviewers' own reports: the human-written Medical Record Reviews and covering
memos the reviewers supplied as references. Almost every layout rule below was read off those
references or asked for by a reviewer, and the code comments record which. The second constraint is
that the Word letter and the linked PDF are the same letter in two formats that a reviewer reads
side by side, so wherever they can share a rule they do.

How each file is assembled and delivered is in [Exports and downloads](exports-and-downloads.md);
routes and filenames are in [Export formats](../reference/export-formats.md). This page covers what
the documents look like and why.

## The Word letter

Built by `backend/app/services/reporting.py` `build_mrr_document()` with python-docx. Top to bottom:

1. **Page header.** A separate first-page header carries `RE: <patient name>` and
   `DOB: <date of birth>`; every later page adds `Page <n>`, where `<n>` is a real Word `PAGE` field
   (with a cached `1` for readers that do not evaluate fields). 10 pt.
2. **Evaluation line.** The export dialog's "Evaluation type (QME / AME)" text, bold, underlined,
   centred, 12 pt.
3. **Heading.** `MEDICAL RECORD REVIEW`, bold and underlined, 12 pt. All eight reference
   deliverables write it in capitals.
4. **Opening paragraph**, justified, 12 pt (see [The opening paragraph](#the-opening-paragraph)).
5. **Summary intro**, bold, 12 pt: `The following is a summary of records from <firm>:`, or
   `The following is a summary of those records:` when no firm was given.
6. **Entries.** A borderless two-column table with fixed layout: the date label in the left column,
   and in the right column the header line, `. `, then the body, justified, then an empty paragraph
   so a blank line separates entries even after they are pasted into another document under its
   own style. Entries are sorted by date with undated entries last. 12 pt.
7. **Page accounting**, when it applies: the exclusion sentence in bold, each excluded document type
   on its own plain line, then the duplicates sentence in bold. 12 pt.
8. **Conclusion**: `This concludes the review of submitted records.`, plain, 12 pt.

**Typeface.** Each evaluator reads their reports in their own typeface, so the whole document is set
in the one mapped to the record's doctor in `DOCTOR_FONTS` (eleven evaluators), or Times New Roman
for an unknown or absent doctor. python-docx writes only the font name and the reader's Word resolves
it, so nothing needs installing on the server. The same tuple, `DOCTORS`, feeds the review page's
doctor dropdown.

**Column widths.** The date column is 1.1 in and the body 5.4 in. When any entry's date label needs
a four-digit year, the date column widens to 1.4 in and the body narrows to 5.1 in; the pair always
sums to 6.5 in, and the table grid carries the same widths as the cells. 1.1 in holds a bold 12 pt
`MM/DD/YY` label (a diagnostic entry's date is bold) in every measurable doctor font, the widest
being Tahoma at 59.7 pt against 68.4 pt available. The wider width is derived from a ratio (a
four-digit label is at most 1.286 times a two-digit one across the measurable doctor fonts) rather
than from one font, because five of the eleven fonts could not be measured (comment above
`_DATE_COL`).

## The linked PDF

Built by `backend/app/services/linked_pdf.py` `build_linked_pdf()`: the same letter rendered as HTML
through PyMuPDF's `Story`, followed by the whole uploaded source record.

- **Page.** US Letter (612 x 792 pt); the letter's content area runs from 72 pt at the sides and
  bottom and 90 pt at the top, leaving room for the running header.
- **Running header.** Drawn onto each letter page afterwards: the same `RE:` and `DOB:` lines, plus
  `Page <n>` from page 2, 10 pt in the base-14 Times font (`tiro`). The appended source pages carry
  no header.
- **Letter.** Times New Roman 11 pt; the evaluation line and heading 12 pt, bold, underlined; the
  opening paragraph justified; the summary intro bold; the accounting and conclusion as in Word.
- **Entries** are hanging-indent paragraphs, not table rows: a 90 pt date span, then the header line,
  `. `, then the justified body. Story cannot split a table row across a page break; a long entry in a
  row was clipped (72 of 90 sentences kept) or vanished entirely (a 120-sentence body over 31 pages),
  while the same body as a hanging-indent paragraph keeps everything. Word keeps its real table,
  because Word splits rows across pages itself.
- **Titles are links.** Every header line is bold, underlined and link blue (`#0000EE`), and links to
  the first source page of its sub-document in the appended record.
- **No doctor typeface.** The reviewers said the PDF does not need it. The PDF is rendered by the
  app, so it would need the real font files, and five of the fonts are licensed (comment above
  `DOCTOR_FONTS`).

## The covering memo

Built by `reporting.py` `build_memo_document()`, always in Times New Roman 11 pt:

1. **Addressed block**, each line dropped when its value is empty: `TO:` the doctor's office
   (`DR. <DOCTOR IN CAPITALS>'S OFFICE`, with a typographic apostrophe), `FROM:` the reviewer's
   display name, `RE:` `Review of <patient name>`, `DATE:` today's date in UTC, written like
   `September 27, 2026`.
2. `Greetings.`
3. **Opening**: `We have received <covering-letter clause><N> pages of medical records<sender>.` It
   uses the same clause builders as the letter's opening. N is the accounting's page count, else the
   typed cover-sheet figure, else the file's page count; with none of them the count is left out.
4. **Accounting**: the exclusion sentence in bold, `records from various sources:`, the excluded
   document types one per line in plain text, and the duplicates sentence in bold.
5. `Thank you.`, `Verified by:`, the reviewer's name and the date.

Three things the reviewers' own memo carries are deliberately absent (`build_memo_document()` and
`_memo_body()` docstrings): the page count of our own report, because python-docx does not paginate
and inventing the number is worse than leaving it out; a count of previously reviewed reports,
because nothing stores what an earlier review covered; and a comparison of the cover-sheet count with
the file's count, which the reviewers asked to have removed because the gap is their own routinely
attached pages.

## The bundle cover page

Built by `backend/app/services/bundles.py` `build_cover_pdf()` when a bundle request carries a cover
heading (the Diagnostic & Operative bundle does; Depositions does not). It is its own PDF placed in
front of the combined pages.

- US Letter with 54 pt margins; Times New Roman 11 pt; the heading (for example
  `LIST OF DIAGNOSTIC AND OPERATIVE REPORTS`) 12 pt, bold, centred.
- A bordered three-column table, `Date | PROVIDER | REPORT TITLE`, one row per matched sub-document in
  record order, continuing onto further pages as needed.
- **Column widths** are absolute points derived from shares of 15%, 45% and 40% of the usable width,
  declared on both the header cells and the body cells. Story takes column widths from the first row
  and ignores percentages; with widths on the body cells only, the columns collapsed and the header
  labels overlapped. The padding and inset constants are pinned by
  `test_the_cover_table_spans_the_content_rect`.
- **Cell values** come from the delivered summary for that sub-document when one exists (its
  effective date and presentable title), else from the review row; the `-` placeholder reads as
  empty.
- **Provider and report title** come from `split_deliverable_title()`: the last element of the header
  line is the report title and everything before it is the provider. The provider elements are
  reordered facility-first only when the first element carries a credential (the one unambiguous sign
  it is a person), and are joined with an en dash (U+2013), as in the reviewers' reference list. A
  short abbreviation such as `ST.` is kept whole rather than split at its period.

## House style

### Header lines

- ALL CAPS, in the order `AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE`, produced by the title prompt
  (see [Summarization](summarization.md#title)).
- No street, suite, city-and-state, ZIP or phone pieces (`summarize_engine.py` `without_address()`):
  none of the entry headers in the reviewers' reference deliverables carries one.
- The author first-name-first: a `SURNAME, GIVEN NAMES, CREDENTIAL` opening, copied from a letterhead
  printed that way, becomes `GIVEN NAMES SURNAME, CREDENTIAL` (`tidy_author_and_facility()`).
- A health system named without its site: `KAISER PERMANENTE FONTANA` becomes `KAISER PERMANENTE`
  (`_HEALTH_SYSTEMS` in `summarize_engine.py`). Both rules also apply to titles stored before them, on
  the Summaries tab and in the export, but never to a title a reviewer typed.
- No internal markers: `presentable_title()` strips `[ManualCheck]`, `[Diagnostic Study]` and
  `(Pages X-Y)`. The page range comes back only when the export dialog's page-number box is ticked.
- One spelling per provider across the record (`consistent_authors()`).
- A period and a space (`TITLE_SEPARATOR`) between the header line and the body: 329 dated entries
  across the reference deliverables use a period, none a colon. When the header line already ends in
  a period (`M.D.`), only the space is added (`title_separator()`, both renderers).
- No doubled period inside a header line: `JANE SMITH, M.D.. ACME CLINIC` becomes
  `JANE SMITH, M.D. ACME CLINIC` (`tidy_author_and_facility()`). A comma after a credential is kept.
- In Word the header line is plain (bold only in the diagnostic tier); in the PDF it is always bold
  because it is the link.

### Bodies

- Ordinary sentence case, with an allowlist of acronyms kept in capitals; organisation names in title
  case (`house_style.py`).
- One paragraph, except depositions, which are grouped by transcript pages.
- Only point labels are bold in the model's output, written `**Label**:`.

### Emphasis tiers

Both renderers turn a body into runs through one parser, `reporting.py` `entry_body_segments()`, so
they emphasise the same spans:

| Span in the body | Rendered as | Why |
| --- | --- | --- |
| `**Diagnoses**`, `**Diagnosis**`, `**Work Status**`, `**Treatment Plan**`, `**Return to Clinic**` (`KEY_ENTRY_LABELS`, compared case-insensitively without trailing punctuation) | Label bold and underlined; the text after it bold until the next label | The reviewers' reference report emphasises what was found and what happens next |
| Any other `**...**` span | Underlined only; text after it plain | In the reference report the underlined runs are the labels, so every bold span is treated as a label |
| `*text*` or `_text_` | Italic | |
| Every span of a category 3 entry (`DIAGNOSTIC_CATEGORY`) | Bold throughout: date, header line, separator and body; labels keep their underline | All diagnostic entries in the three reference deliverables are bold throughout, and a reviewer asked for it |

`INLINE_EMPHASIS_RE` does not match across a line break (no `re.DOTALL`). With it, a bullet `* item`
on one line paired with the next line's bullet and italicised everything between; 3 of 3,017 stored
summaries rendered differently from the web view for that reason.

Every non-empty line of a body is its own paragraph in both renderers: `entry_body_segments()`
first passes the text through `_paragraphed()`, which joins the lines with `PARAGRAPH_BREAK`. The
linked PDF used to print a deposition as one block, because in its HTML a plain newline is
whitespace.

### Date labels

- `parsed_date()` accepts `MM/DD/YYYY`, `MM-DD-YYYY`, `MM.DD.YYYY`, `YYYY-MM-DD`, `MM/DD/YY`,
  `MM-DD-YY` and `MM.DD.YY`. Spelled-out months and day-first dates are not guessed at.
- `date_label()` writes `MM/DD/YY`, as a senior reviewer asked, for years 1969 to 2068, and
  `MM/DD/YYYY` outside that window, where a two-digit year would read back a century wrong. An
  unparseable or `-` date is written `Undated`.
- Entries are sorted on the parsed date, not the label; undated entries go last, as the reviewers
  asked.

### The opening paragraph

`reporting.py` `intro_sentence()` builds it from optional parts, and each part is dropped rather than
rendered empty. The template, with the two optional clauses in braces:

```text
I have received {covering-letter clause}<N> pages of medical records{sender clause}. I have reviewed all of the pages received and my opinion is based upon such records.
```

- **Covering-letter clause** (`_letter_clause()`): `a defense advocacy letter dated <date> along with `
  or `an interrogatory letter dated <date> along with `, without `dated <date>` when no date is
  entered, and nothing for `none` or a blank type. The article travels with the label in
  `LETTER_LABELS`.
- **Sender clause** (`_sender_clause()`): ` from <person>, of <firm>` with both, ` from <either>` with
  one, nothing with neither.
- **Labor Code disclosure**: only when the signed-in reviewer has a display name, the paragraph adds
  who did the record organisation (`REVIEWER_CREDENTIAL`) and the statute citation
  (`LABOR_CODE_CITE`). Without a name it is omitted, because a legal assertion with a blank name in
  it is not something to ship.

N is the typed cover-sheet page count when one was entered on the review page, else the PDF's own
page count.

### Page accounting

The closing sentences partition the review rows (`reporting.py` `record_accounting()`) into three
disjoint buckets:

- **Remarked upon**: pages of rows included for summarization.
- **Duplicate copies**: pages of the non-primary members of duplicate groups a reviewer has resolved
  (a group with a primary). An unresolved or dismissed group counts as ordinary rows.
- **Other documents**: the remainder, never negative. Their titles (the excluded rows' titles,
  de-duplicated case-insensitively, first spelling kept) form the list.

The page count they are counted against is the typed cover-sheet figure, except that when the rows
cover more pages than that figure but fit inside the file, the file's page count is used instead, so
a mistyped figure cannot blank the sentences.

`accounting_sentences()` has three shapes and one silence:

| Situation | Exclusion sentence |
| --- | --- |
| No other pages | `Of the <R> pages received, exactly <M> pages were remarked upon.` |
| Other pages with titles | `... remarked upon, as the remaining <O> pages are other documents such as:` then the list |
| Other pages without titles | `... remarked upon, as the remaining <O> pages are other documents.` |
| Rows cover more than the pages received, or no count | Nothing, and no duplicates sentence either |

The duplicates sentence appears only when D is above zero:

```text
In addition, the records included <D> pages of duplicate copies of records already counted above.
```

## Rules that must stay in step

| Rule | Its one home | Who reads it |
| --- | --- | --- |
| Heading, summary intro, conclusion, title separator | `reporting.py` `REVIEW_HEADING`, `summary_intro()`, `CONCLUSION`, `TITLE_SEPARATOR` | Word letter; linked PDF |
| Opening paragraph and its clauses | `reporting.py` `intro_sentence()`, `_letter_clause()`, `_sender_clause()` | Word letter; linked PDF; memo opening (the clauses) |
| Header lines | `reporting.py` `header_lines()` | Word header; linked PDF running header |
| Page accounting and its sentences | `reporting.py` `record_accounting()`, `accounting_sentences()` | Word letter; linked PDF; memo |
| Emphasis tiers | `reporting.py` `entry_body_segments()`, `INLINE_EMPHASIS_RE` | Word letter; linked PDF. The web view has a TypeScript copy, `INLINE_RE` in `frontend/components/review/markdown-text.tsx`, which must match character for character |
| Date label and sort order | `reporting.py` `date_label()`, `parsed_date()` | Word letter; linked PDF |
| Diagnostic tier | `reporting.py` `is_diagnostic()` | Export entries and bundle report entries; both renderers |
| Internal title markers | `summarize_engine.py` `_row_tags()` applies them, `presentable_title()` strips them | Every delivered document. The web view strips its own copy in `frontend/components/review/summaries-view.tsx` `displayTitle()` |
| DOI prefix grammar | `backend/app/services/summary_doi.py` | Export restoration; the web view's copy in `summaries-view.tsx` (`DOI_PREFIX_NEW`, `DOI_PREFIX_LEGACY`) |
| Header-line shape | `summarize_engine.py` `TITLE_PROMPT` | `without_address()`, `consistent_authors()`, `bundles.py` `split_deliverable_title()` all parse that shape |
| "Resolved duplicate" | Stated twice: `record_accounting()` (ORM rows) and `bundles.py` `resolved_clusters()` / `is_resolved_duplicate()` (row dictionaries) | Pinned together by `test_the_two_readings_of_a_resolved_duplicate_agree` |
| Doctor typefaces | `reporting.py` `DOCTOR_FONTS` | Word typeface; the doctor dropdown (`DOCTORS`) |
| Letter types | `reporting.py` `LETTER_TYPES`, `LETTER_LABELS` | Header validation on save; the letter and memo clauses |
| Page number on page 2 onward | No shared code: a first-page header plus a `PAGE` field in Word, a loop in `_draw_running_header()` in the PDF | Keep both by hand |

`backend/tests/test_reporting.py` holds a family of `test_both_renderers_*` tests that pin the Word
and PDF renderers together on these rules.

## Before you change it

- Change client-facing wording in `reporting.py` only, so both renderers and the memo move together;
  never add a second copy of a sentence to `linked_pdf.py`.
- A change to emphasis must also be made in `markdown-text.tsx`, or the review screen stops showing
  what the deliverable prints.
- A change to the header-line shape in `TITLE_PROMPT` breaks the parsers that split it; check
  `without_address()`, `consistent_authors()` and `split_deliverable_title()`.
- Every optional clause is dropped rather than rendered empty. Keep that property: a dangling
  `from .` shipped once.
- Changing the cover table's CSS can move Story's geometry; run the bundle cover tests.
- Changing `DOCTOR_FONTS` changes the dropdown too.

## Related pages

- [Exports and downloads](exports-and-downloads.md)
- [Export formats](../reference/export-formats.md)
- [Summarization](summarization.md)
- [How to change a summary prompt or rule](../how-to/change-a-summary-prompt-or-rule.md)
- [Duplicate detection](duplicate-detection.md)

<!-- reviewed: 2026-09-30 -->
