# How the system splits a medical record into individual documents

A plain-English explanation of the "segmentation" step: what we ask the AI to do, and why.

## The problem being solved

A workers' compensation file arrives as one large scan - often several hundred pages - containing
dozens of separate documents run together: progress notes, imaging reports, therapy visits, claim
forms, depositions, medico-legal evaluations. There are no separator pages and no index.

Before anything can be summarized, the file has to be cut into those individual documents. That is
what this step does. Everything downstream depends on it: if two documents are merged into one, the
second one effectively disappears from the review.

It is harder than it sounds. Printed page numbers restart and repeat, because the bundle was
assembled from many sources. Scanners insert blank pages. Fax cover sheets and routing slips travel
with the documents they belong to. A single long report can contain pages that look like a
completely different document - lab tables, a copied letter, a work-status form.

## What we tell the AI

We give it a role: an expert medical-records clerk whose job is to split these files and report
exactly which pages belong to which document.

Then we give it six sets of instructions:

1. **What counts as one document.** One author or facility, one visit or report. We tell it
   explicitly that most documents are short - one to three pages - because the most common mistake
   is lumping several short documents together.

2. **How to count pages.** By position in the file, ignoring any page numbers printed on the pages
   themselves, since those restart throughout the bundle and would mislead it.

3. **That the pages must add up exactly.** Every page belongs to one document and only one. No gaps,
   no overlaps, nothing left over. Blank pages are never treated as documents in their own right;
   they get attached to the document they follow.

4. **How to recognise where a document begins.** New letterhead together with a new title, or a new
   visit date. And, just as importantly, what does NOT start a new document: continuation pages,
   signature pages, lab tables attached to the report above them, a fax cover that belongs to what
   follows it. We also tell it that a long medico-legal evaluation quoting many other records stays
   as one document, while several same-day notes from the same clinic are separate documents.

5. **What to record about each document.** Its title, the date of the visit it describes (not the
   date it was faxed or printed), and whether a human should look at it - which we flag for
   handwriting, checkbox forms, work-status reports and medico-legal evaluations.

6. **What format to answer in.** We do not merely ask for a particular format; the structure is
   enforced technically, so a malformed answer cannot reach the rest of the system.

## The one decision worth understanding

We tell the AI: when you genuinely cannot tell whether a page starts a new document or continues the
previous one, **start a new document.**

That is a deliberate trade-off, and it is the reason the system tends to produce more documents than
strictly necessary. The logic is asymmetric:

- If it splits one document into two, a reviewer sees both and merges them in one click.
- If it merges two documents into one, the second document is never displayed. Nobody knows it was
  there, and it silently never reaches the client.

One of those errors is a minor inconvenience. The other is a missed record. So the system is
deliberately biased toward the inconvenient one.

## What happens after the AI answers

The AI's answer is not taken at face value. Four things happen to it:

- **Large files are processed in overlapping sections.** Each section overlaps the one before it, so
  the AI always has context around a boundary rather than being asked to judge a document that was
  cut in half. A separate step reconciles the overlaps so nothing is counted twice or lost.
- **Coverage is checked.** The pages must account for the whole file exactly. If a section of the
  file fails to process, the job stops and reports it, rather than quietly producing a shorter
  document than the one that was uploaded.
- **A second pass reviews the boundaries** and merges documents that were split unnecessarily. That
  pass can only ever merge, never split - which means it can tidy up over-splitting without any risk
  of hiding a document.
- **Each document is then classified and dated.** If either of those steps has trouble, the document
  is flagged for human review rather than the whole file being failed.

## Something we deliberately do not do

We do not ask the AI how confident it is. We tried. On the two most difficult test files it rated
its own confidence as "high" on 231 of 232 documents - including every single one it got wrong. A
self-rating that is always "high" tells you nothing. Confidence is now worked out from the evidence
instead of being asked for.

## How we know it works

We keep test files where the correct answer was determined by hand, page by page. On our reference
227-page file containing 51 documents, the system identified **every one of the 51 boundaries at
exactly the right page**, with no page unaccounted for and no page counted twice.

On the same file it also proposed 76 documents rather than 51 - the deliberate over-splitting
described above, which reviewers resolve by merging.

## Appendix: the exact instructions

The wording below is what is sent to the AI, reproduced verbatim.

**Role given to the model:**

> You are an expert medical-records clerk. You split scanned workers' compensation medical-record
> files into their component documents and report exact page ranges and metadata.

**Instructions:**

> The document above is one scanned medical-record file from a California workers' compensation case.
> It is a continuous excerpt of a larger record: it may begin in the middle of one document and end
> in the middle of another.
>
> Split the file into its component sub-documents and return one JSON record per sub-document, in
> page order.
>
> **What a sub-document is.** One document produced by one author or facility for one encounter,
> report, or form - the unit a records reviewer would summarize as a single item (a progress report,
> an imaging report, a deposition, a claim form, one therapy visit note, etc.). Most sub-documents
> are SHORT - one to three pages is typical; multi-page spans are the exception (long reports,
> depositions, medico-legal evaluations), not the norm.
>
> **Page numbers.** "Page N" means the N-th page of THIS file, counting from 1. Ignore page numbers
> printed on the pages: scanned bundles restart and repeat their printed numbering, so printed
> numbers do not identify positions in this file.
>
> **Coverage.** Every page belongs to exactly one sub-document: records must be in order, must not
> overlap, and must not leave gaps; together they cover page 1 through the last page. If the file
> starts or ends mid-document, still report that partial document with the page range visible here.
> Blank pages NEVER form their own record: scanners emit blank backsides and separators. Attach a
> blank page to the document BEFORE it; blank pages before the first document belong to the first
> document.
>
> **Where a sub-document starts.** At its first physical page, INCLUDING any fax cover sheet,
> transmittal letter, or routing slip that travels with it. A cover page is never its own record, and
> a document never starts on the page after its cover. Strong start signals: a new letterhead or form
> header together with a new document title; the first page of a form; a new visit/encounter date or
> author within a run of same-type documents (consecutive progress notes from the same clinic are
> SEPARATE records, one per visit). NOT starts: "page N of M" continuation pages; lab tables,
> signature pages, or attachments that belong to the report they follow; a letterhead change INSIDE
> one report. Long medico-legal evaluations (QME/PQME/AME) quote many other records - keep the entire
> evaluation as ONE record. A distinct QME/AME supplemental report is its own record. A report often
> EMBEDS a few pages that look like a different document type (lab tables, an imaging summary, a
> work-status form, a copied letter). If those pages carry the report's date or are referenced by the
> surrounding text, they are part of the report - do not split them out as their own record. A
> document's FIRST or LAST pages often look unlike its body: certification or notary stamps,
> letterhead-only or branding pages, terms-and-conditions or disclaimer pages, distribution/cc lists.
> These belong to the document they accompany - never report them as separate records. Do NOT merge
> two records merely because they share a document type and date: these files routinely contain
> same-day batches of short same-type documents (one per visit, one per body part, one per form), and
> each is its own record.
>
> **Tiebreak:** when you are genuinely unsure whether a page starts a new document or continues the
> previous one, START A NEW RECORD. A reviewer merges a false split in one click, but a document
> hidden inside another record is never seen again.
>
> **Fields** (use "-" whenever a value is unavailable; never null). Title: the document's own title
> or header wording if visible; otherwise the document type. A title of the form "X vs Y" is almost
> always a deposition. Document date: the visit/encounter date of THIS document as MM/DD/YYYY; ignore
> fax, print, and re-send dates, and never report the date of injury here. Manual check: "x" if a
> human should review the document - substantial handwriting (more than a signature), checkbox-style
> forms, work-status reports, or QME/PQME/AME reports; otherwise "-".
