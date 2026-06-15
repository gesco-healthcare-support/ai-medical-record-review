# How to add a document category

A category is a number that selects (a) the fuzzy-match group and (b) the summarization
prompt. To add one:

1. **Taxonomy** - in `mrr_ai/groups.py`, add the category number as a key with a list of
   representative document-type titles (used by the fuzzy matcher):

   ```python
   "15": ["My New Report Type", "Another Title Variant"],
   ```

2. **Prompt** - in `mrr_ai/prompts.py`, add a `category_15` entry with the summarization
   instructions for that type.

3. **Routing** - `summarize()` in `mrr_ai/blueprints/summarize.py` selects the prompt by
   number. If you add a number outside the existing 1-14/100 range, extend the
   `if option == N` ladder to map it to `prompts["category_15"]`.

4. **Docs** - update `docs/reference/csv-contract.md` if the category range changes.

5. **Test** - add a case to `tests/unit/test_categorization.py` asserting a representative
   title maps to the new number.

> The lexical fuzzy match is being replaced (B5/B6). Until then, choose titles in step 1
> that closely match what Gemini emits, or the match will fall through to category 100.
