"""Application settings (ported from the Flask mrr_ai/config.py).

Lazy via get_settings() so importing the package does not require the env to be present
(tests/tooling import freely; a real run reads .env). Required secrets have no default, so
instantiation fails fast if they are missing. Postgres + Redis + Vertex-only per the plan.
"""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"  # "prod" hard-requires Vertex (BAA)

    # The commit this image was built from, stamped onto every job (services/jobs.create_job).
    # Prompt fingerprints cover prompt TEXT; this covers the deterministic code that is NOT a prompt
    # and is otherwise unattributable - house_style, and the per-row context blocks summarize_engine
    # appends after the fingerprint is computed (_standalone_studies_block, _document_date_block,
    # _deposition_pages_block). Set by the Dockerfile from the GIT_SHA build arg.
    #
    # "unknown" is deliberate: a build made without the arg is honestly labelled rather than
    # silently mislabelled with a value that is not the code that ran.
    build_sha: str = "unknown"

    # Persistence + queue (local self-hosted; no cloud).
    database_url: str  # e.g. postgresql+psycopg://mrr:...@localhost:5432/mrr  (required)
    redis_url: str = "redis://localhost:6379/0"

    # Auth: cookie signing + the carried-over Flask-Security password salt (required).
    secret_key: str
    security_password_salt: str

    # PHI at rest; keep off any network share.
    upload_folder: str = "./uploads"
    tesseract_cmd: str = ""

    # Gemini routing. Vertex is the BAA-covered path; required in production.
    use_vertex: bool = Field(default=False, validation_alias="GOOGLE_GENAI_USE_VERTEXAI")
    gemini_api_key: str = ""
    google_cloud_project: str = ""
    # `global`, not a region. A Vertex 429 is Dynamic Shared Quota - capacity unavailable at that
    # moment rather than an exhausted regional allowance - and the global endpoint draws on a larger
    # pool, so it mitigates the 429s the original `us-central1` was chosen to avoid. That choice
    # rested on this project having no quota in `global`; overturned 2026-08-12, and the server has
    # run `global` since. This default only bites an environment that does not set the variable,
    # which is the one least equipped to diagnose the 429s it would get.
    google_cloud_location: str = "global"
    genai_model: str = ""
    summary_model: str = ""
    verify_model: str = ""
    # Classification is a short, structured enum task - the cheapest tier is enough and cuts
    # cost/latency vs full Flash. A separate knob so a quality regression reverts via env alone.
    classify_model: str = "gemini-2.5-flash-lite"
    # Summary body temperature. Extractive medical summarization wants determinism: an eval on real
    # duplicate sub-docs showed 0.0 makes repeat runs identical (0.8 varied down to ~0.26 similarity)
    # and cuts fabrication. Env-overridable so a regression reverts without a redeploy. (2.5-flash
    # only; Gemini 3 would want its default 1.0.)
    summary_temperature: float = 0.0
    # Output budget for one summary. 2048 cut long category-1 narratives off mid-sentence and the
    # partial reply was stored as if it were finished; 8192 fits the longest real notes with
    # headroom, well under 2.5-flash's 65k output ceiling. Env-overridable so a box can raise it
    # without a redeploy, and a reply that still hits the cap is flagged for manual check.
    summary_max_output_tokens: int = 8192
    # Summary faithfulness verify pass: a second temp-0 call rewrites each summary to drop
    # statements unsupported by / contradicting its OCR source (problem #3), keeping the raw output
    # too. On by default; a regression reverts via env with no redeploy. Distinct from the
    # segmentation verify_* knobs below.
    summary_verify: bool = True
    # Summarization also sends the page IMAGES alongside the OCR text (multimodal): the images
    # recover tables, checkboxes, and handwriting the OCR garbles (an eval on real sub-docs showed it
    # adds missing vitals/allergies with no loss). Env-toggle to revert to OCR-only.
    summary_multimodal: bool = True
    # Cap page images per sub-document so a long record cannot blow the request payload/latency; the
    # full OCR text still covers every page beyond the cap.
    summary_image_max_pages: int = 15
    # DPI for the summary page images (lean JPEG); 120 was enough to read tables/handwriting in the eval.
    summary_image_dpi: int = 120
    # Dynamic thinking (-1) for the summary body. Originally forced: 2.5-pro REJECTS the seam's
    # default thinking_budget=0 with a 400. It stays at -1 under 3.5-flash for a DIFFERENT reason -
    # the 2026-08-14 scoring arm that selected 3.5-flash ran with -1, so the quality measurement only
    # holds at this value. 3.5-flash accepts 0 as well, which makes "the body is a flash tier now, so
    # step this down" a tempting cleanup. Do not, without re-scoring: two silent thinking_budget=0
    # bugs have already shipped in this codebase.
    summary_thinking_budget: int = -1
    # Which vendor answers the summarize stage's calls (body, title, audit). "gemini" is the current
    # behaviour and stays the default: the provider abstraction landed first specifically so it could
    # ship without changing which model runs. Switching this to "openai" additionally requires the
    # per-call model keys and, in production, the ZDR acknowledgement - see services/llm/.
    summary_provider: str = "gemini"
    # Per-call-type models for the three summarize-stage calls. On the GEMINI path `_derive` fills
    # these in (body = summary_model, title and audit = gemini-2.5-flash). This began as a cut from
    # three 2.5-pro calls per row to one; the body moved to 3.5-flash on 2026-08-14, so the saving is
    # smaller now, but the split stands on its own - a title is extraction and an audit is a check,
    # and neither needs the body model whatever that happens to be.
    #
    # On the OPENAI path there is NO default, deliberately: OpenAI's own guidance is to fix an
    # accuracy target on the most capable model and then step down to the cheapest that still hits it.
    # A silent default is how an unvalidated model reaches production, so selecting that provider
    # without setting all three stays a startup error - see the check in `_derive`.
    #
    # They are separate keys because the three calls need different capability: the body call reads
    # page images and applies a long format spec, while the title is extraction from OCR text and the
    # audit is a check. Title and audit may well sit a tier or two below the body.
    summary_body_model: str = ""
    summary_title_model: str = ""
    audit_model: str = ""
    # The model the BODY call falls back to when Vertex will not serve the configured one. Only ever
    # reached after `genai_retry.generate_with_retry` has spent its whole budget and is still getting
    # 429s, and only for the body - the title and audit run flash already, so there is nowhere below
    # them to go. Set to "none" to disable and let the row fail instead - "" cannot mean disabled,
    # because an unset key is also "" and that has to resolve to the default.
    #
    # This exists because the body left 2.5-pro for availability, not quality: on 2026-08-13 Vertex
    # refused 2.5-pro for this project outright, 0 of 8 on the configured endpoint, and every
    # summarize job failed. It recovered by itself the next day. That can recur without warning, so a
    # hard-pinned pro needs somewhere to land. Resolved in `_derive` rather than defaulted here so the
    # openai branch still sees "" for an unset key.
    summary_body_fallback_model: str = ""
    openai_api_key: str = ""
    # PHI gate. A signed BAA is NOT sufficient on its own: OpenAI additionally requires Zero Data
    # Retention (or Modified Abuse Monitoring / Eyes Off) approved on the ORGANIZATION. Confirmed
    # 2026-08-05 as the org default. This flag is an explicit human acknowledgement of that, so a
    # future box cannot start sending records to an org whose retention setting nobody checked.
    openai_zdr_acknowledged: bool = False

    # Concurrency + retry (become RQ worker knobs in P4; caps guard the shared Vertex quota).
    #
    # Row-level concurrency inside ONE summarize job. 2 -> 5 on 2026-08-25, and 5 is not a new number:
    # it is the value `docker-compose.yml`'s own note computes and then does not use. That note says,
    # measured 2026-08-03, "two concurrent chains at ~15.4s per call is exactly 7.8/min ... by Little's
    # Law the saturation point for a 20 rpm budget is 20/60 * 15.4 = ~5 concurrent rows, so 5 fills the
    # existing budget without raising it." The conclusion was 5; the value stayed 2.
    #
    # And the budget it was computed against has since TRIPLED: VERTEX_MAX_RPM was 20 then and is 60
    # now. So the setting is sized for a ceiling that no longer applies.
    #
    # Measured on the box 2026-08-25 over the 43 newest summarize jobs: 11.3h producing 1,851
    # summaries, i.e. 22.0s per summary at 2 lanes, which is ~14.7s per call and matches the 15.4s
    # above. One job therefore draws about 8 calls/min against a 60 rpm ceiling - roughly 14%
    # utilisation. Summarize is the second-largest stage in the pipeline (11.3h against segment's
    # 15.9h), so this is where the remaining wall-clock is.
    #
    # 5 RATHER THAN MORE, and the ceiling is not the reason to stop. The compose note names the real
    # failure mode: `rate_limit.acquire()` abandons its wait after MAX_ACQUIRE_WAIT_S (300s) and
    # proceeds ANYWAY, so enough queued callers stop being rate limited at all. Its own arithmetic put
    # 3 summarize workers x 5 chains against 20 rpm at a ~30s mean wait, well inside the abandon
    # threshold - and at 60 rpm that wait is shorter still. Going past 5 leaves the regime the note
    # measured; 5 is the value it endorsed at a stricter ceiling than we now run.
    #
    # LEFT AT 2, AND THE REASON IS A RACE THIS CHANGE EXPOSED rather than the ceiling.
    #
    # Raising it to 5 was written, tested and reverted on 2026-08-25. `summarize_document` consumes
    # results through `drain_pool`, which is `as_completed` - COMPLETION order, not submission order -
    # and the give-up condition is `generated == 0 and transient_failures >= giveup_after_failures`.
    # So whether a job ENDS or PAUSES depends on whether N failures happen to complete before the
    # first success, and with more lanes in flight that becomes likely rather than rare. Measured: at
    # 5 lanes, `test_summarize_does_not_give_up_once_a_row_has_succeeded` and
    # `test_a_notice_row_is_not_counted_as_proof_the_model_answers` fail on 3 of 6 runs and pass on
    # the other 3.
    #
    # That is the wrong outcome by the tests' own statement of intent: a document where the model IS
    # answering some rows should PAUSE and retry the rest, not be ended as though the model were
    # refusing everything. The bug is latent at 2 as well - it is a race, not a threshold - but the
    # concurrency is what makes it probable.
    #
    # So the throughput is available and it is not takeable yet. Fix the give-up decision first so it
    # does not depend on completion order, then raise this. Reverts by env with no rebuild either way,
    # and `vertex:metrics:*` in Redis is where the pacer records admission.
    #
    # UPDATE: that race is fixed - `summarize_document` now records the give-up as a CANDIDATE and
    # decides after the loop, so the end-versus-pause outcome no longer depends on completion order,
    # pinned at 1/2/5/8 lanes. STILL AT 2 ANYWAY, deliberately. Raising it was tried again on the
    # fixed code and one full-file run in 18 failed in a way that did not reproduce in isolation, and
    # a concurrency default is exactly the kind of change where an unexplained intermittent failure
    # should not be waved through. The blocker named above is cleared; what is missing is a clean
    # reason for that one run, not more throughput evidence.
    pipeline_workers: int = 2
    # Bound on "pause and auto-resume forever": when this many rows have failed transiently and NOT
    # ONE has succeeded, the model is refusing everything and resuming only replays the same wall.
    # Zero successes is the discriminator, not the failure count alone - a blip with some rows getting
    # through must still pause and retry. 3 is deliberately small because zero-of-three is already a
    # strong signal: on 2026-08-13 job 1000173 ground for 96 minutes against a 0/8-admitted model,
    # then died as an unclassified rq.timeouts.JobTimeoutException, so the reviewer waited an hour and
    # a half to be told nothing. Measured 2026-08-14: admission recovered to 8/8 on its own, so this
    # condition is transient and external - which is exactly why a job must not wait it out.
    summarize_giveup_after_failures: int = 3
    classify_workers: int = 4
    # Injury-date reads at the END of segmentation: one isolated vision call per sub-document, so
    # they parallelise like categorization does. Its own knob rather than borrowing another
    # stage's, following segment_window_workers / classify_workers / page_text_workers.
    doi_workers: int = 4
    # RQ per-job wall-clock cap (seconds). The old Flask app ran the pipeline in-process with no
    # cap; RQ's 180s default is far too short - a 200+ page record needs minutes per vision window
    # plus one Vertex call per identified document. The effective cap is SIZE-AWARE:
    # max(job_timeout, page_count * job_timeout_per_page), so a small record still fails fast while a
    # 2600-page record gets hours. Tune via JOB_TIMEOUT / JOB_TIMEOUT_PER_PAGE.
    job_timeout: int = 3600
    job_timeout_per_page: float = 20.0
    # Within-request transient retries at the genai seam. Bumped 6 -> 8 so a brief shared-quota
    # 429 / 5xx burst rides out inside a single call on the NON-resumable paths (segmentation /
    # verify / classify, which have no pause/resume); a sustained outage still exhausts and fails
    # the job with a friendly terminal message rather than hanging.
    genai_max_retries: int = 8
    genai_retry_base_delay: float = 2.0
    genai_retry_max_delay: float = 30.0

    # Per-attempt deadline (ms) for the Vertex/genai client. google-genai defaults to no timeout, so
    # a stalled call blocks forever.
    #
    # This is NOT only a client-side guard: google-genai forwards it to Vertex as the SERVER-side
    # deadline, so exceeding it returns a server 504 DEADLINE_EXCEEDED, not an httpx timeout (proven
    # 2026-08-12 - an 8000ms value produced a server 504 at 6.2s). It therefore caps how long ONE
    # call may legitimately take, and a 504 is deliberately NOT retried because the same deadline
    # binds every attempt (see services/genai_retry).
    #
    # Left at 120s deliberately, and now on evidence rather than assumption. The old comment claimed
    # "120s covers a large vision window"; that was false - an uncapped 241-page window needed 179s.
    # What makes 120s correct is window_max_pages: with windows capped at 160 pages the slowest
    # measured call is 54.5s. A deadline above 120s IS honoured (the 179s call completed under a
    # 600s deadline), so raising this remains available if a capped window ever runs long.
    genai_http_timeout_ms: int = 120000

    # Per-call OCR (Tesseract) wall-clock cap (seconds). A hung/oversized page is killed and
    # skipped rather than blocking a worker thread forever (the concurrent-OCR deadlock backstop;
    # OMP_THREAD_LIMIT=1 in compose is the primary fix).
    ocr_timeout_seconds: int = 120

    # Rasterization DPI for OCR. 200 was never a decision - pdf2image's default applied because
    # _rasterize was called with no dpi at all. Explicit now so it is visible and tunable.
    ocr_base_dpi: int = 200

    # Optional cap on the rendered long edge in pixels; 0 DISABLES it (the default, deliberately).
    # When set, the DPI is lowered so an oversized page fits, and never raised.
    #
    # It is off because capping was measured on 2026-08-19 and DID NOT PAY. On a 2700pt page (7500px
    # at 200 DPI) a 3500px cap cut OCR from 7.4s to 1.5s - 4.2x - but over 20 such pages it lost 6.0%
    # of recognized characters, one page dropping 59%. Raising the cap did not recover it: at 6500px
    # (DPI 135, only 1.7x faster) the loss was still 3.8%. The premise that upsampling an oversized
    # page is pure waste is WRONG - at 72 DPI an 8pt glyph is 8px tall, below what Tesseract needs to
    # resolve, so the extra pixels buy real accuracy.
    #
    # Turning this on needs a proper quality metric first. Character count and difflib similarity are
    # not enough: similarity sat near 70% even at DPI 135, because it punishes line reordering and
    # whitespace rather than measuring accuracy. Word-level recall against a reference is the missing
    # instrument. Until then, spending 8 seconds beats losing text out of a medical record.
    ocr_max_long_edge_px: int = 0

    # Safety margin (seconds) subtracted from the size-aware job_timeout to bound every
    # ThreadPoolExecutor drain (see pool_timeout). The pool wait always fires JUST before RQ's
    # SIGKILL, so no as_completed() waits unbounded, yet it scales with page count.
    future_timeout_margin_seconds: int = 120

    # Resumable summarize (item 7): after this many CONSECUTIVE transient failures (shared-quota
    # 429 / 5xx / disconnect) the run stops mid-batch, saves progress, and schedules a resume this
    # many seconds later - retrying the remaining rows forever until quota frees up. Only transient
    # pressure pauses; a permanent failure ends the run "needs attention" instead.
    summarize_pause_after: int = 3
    summarize_resume_delay: int = 60

    # Thinking tokens are pure overhead for our structured extraction calls, and on 2.5-flash they
    # silently consume max_output_tokens. Default OFF (budget 0); set >0 or -1 (model-dynamic) via
    # env to re-enable if a task regresses. Applied centrally at the genai seam.
    gemini_thinking_budget: int = 0

    # Segmentation is the exception: an A/B on labeled cases showed thinking-OFF regresses strict
    # doc-F1 (it over-segments more), so the segmentation window call keeps dynamic thinking (-1)
    # while every other call inherits gemini_thinking_budget. Env-overridable.
    segment_thinking_budget: int = -1

    # Concurrency for the one-time per-page OCR pass (services/page_text.populate_document).
    # Deliberately its own knob rather than reusing CLASSIFY_WORKERS: this is pure Tesseract CPU on
    # the same box that runs the Vertex pacing work, and OMP_THREAD_LIMIT=1 in compose is what stops
    # concurrent tesseract processes deadlocking.
    #
    # 4 -> 6 on 2026-08-24, on measurement rather than on principle. This pass is roughly HALF the
    # wall-clock of a segment job - the same 297-page record segmented three times on 2026-08-17 took
    # 1,463s, then 810s and 715s once its page text was stored - and segmentation is the largest stage
    # in the pipeline (15.9h across 57 jobs, against 13.7h for summarize). Measured in the api
    # container over 32 sampled pages of a 2,673-page record, rasterizing and OCR-ing in memory:
    #
    #     threads   1      2      4      6      8
    #     seconds  60.2   30.7   17.0   12.7   10.9
    #     speedup  1.00x  1.96x  3.54x  4.75x  5.51x
    #
    # Character output was IDENTICAL at every setting and no page failed, so this is throughput and
    # not corruption, and the tesseract deadlock this comment warns about did not appear even at 8.
    # 4 -> 6 is 1.34x on the pass, so roughly 12% off the segment job.
    #
    # 6 rather than 8, which measured faster, because the box has 8 cores and also runs six RQ
    # workers, postgres, redis, api and web: 6 leaves two cores for everything else. The contention
    # risk was checked rather than assumed - up to FOUR segment jobs have run concurrently on the box
    # (21 overlapping pairs historically), which at 6 threads each would be 24 threads on 8 cores. Per
    # page that shows no penalty today: jobs running alone averaged 3.64 s/page, with 2-3 others 4.47,
    # and the 4+ bucket 2.95, so there is no monotone contention effect to protect. If one appears,
    # PAGE_TEXT_WORKERS reverts this by env with no deploy.
    page_text_workers: int = 6

    # Global Vertex request ceiling (requests/minute) enforced by a Redis token bucket at the seam,
    # so the aggregate rate across every worker process never trips dynamic-shared-quota 429s. Tune
    # empirically: raise until near throttling, then back off ~20%.
    vertex_max_rpm: int = 60
    # Upper bounds for the adaptive pacer (services/llm/pacing.py). These are SAFETY BOUNDS, not
    # tuning knobs: an AIMD controller finds the working rate from 429 feedback and can only ever
    # sit at or below these. A fixed rate was measured to be unworkable - Vertex publishes no
    # remaining-capacity header and no RetryInfo, and the serviceable rate moved more than 4x
    # between 2026-08-03 and 2026-08-05.
    #
    # vertex_max_tpm is a bound, not a published figure: Google does not state a tokens-per-minute
    # limit for dynamic shared quota, and a paired experiment could not establish whether DSQ meters
    # requests or tokens (pool depletion dominated the result). Metering both is correct either way.
    vertex_max_tpm: int = 4_000_000
    # OpenAI publishes both per project and returns them on every response, so these are only the
    # cold-start bound; observe_limits() replaces them with the real values after the first call.
    # Measured on the account 2026-08-03: 5,000 RPM, 1M TPM flagship / 2M mini-nano tiers.
    openai_max_rpm: int = 5_000
    openai_max_tpm: int = 1_000_000

    # Independent segmentation windows run on a small thread pool (each still crosses the seam, so
    # the limiter caps the aggregate). Speed lever; keep modest so it does not dominate the quota.
    segment_window_workers: int = 3

    # Duplicate detection. `dupe_jaccard_threshold` is the candidate finder's word-set cut. The two
    # similarity knobs are char-level difflib scores over DATE-MASKED text and they gate DIFFERENT
    # steps, which is why there are two:
    #   `dupe_similarity_override` is what lets two sub-documents with DIFFERENT dates be considered
    #     copies at all - both as a cross-date admission in cluster_rows and as the escape hatch in
    #     duplicate_gate. #81 raised this default 0.90 -> 0.99 on 2026-08-06, reasoning that the date
    #     now leads the rule so this knob guards a date MISMATCH specifically rather than a missing
    #     title, and should fire only for text that is essentially identical. Adrian's instinct was
    #     100%; that cannot fire, because two scans of one page are OCR'd separately and never come
    #     out character-identical - measured on 22 live clusters, real duplicates bottomed out at
    #     0.994, the worst false positive at 0.823. Masking dates out before scoring is what makes a
    #     high value reachable at all for a re-scan whose only difference is a stamped date.
    #     THAT RAISE NEVER REACHED A CONTAINER - see HISTORY below - and its evidence no longer holds.
    #   `dupe_model_override` skips the confirm call entirely - at that similarity the text has already
    #     answered the question the model would be asked, and the confirm step's silent "these are all
    #     distinct" verdict is a known way to lose a real duplicate. Left at 0.95: it guards a
    #     different question (spend a Vertex call or not) and the evidence for it has not changed.
    #
    # !! THE 0.994 / 0.823 SEPARATION ABOVE DOES NOT SURVIVE. Both figures were measured through
    # difflib's autojunk suppression, which `dedup._min_difflib` stopped applying in #148. The
    # re-derivation was done on the corrected scale on 2026-08-25 and there is no separation left to
    # cut at any value.
    #
    # The label set: `review_rows.dupe_dismissed` records a reviewer REJECTING a duplicate group. A
    # dismissal is a deliberate action so `true` is a reliable false positive, while `false` is this
    # column's default and means nothing on its own. So the set is restricted to the 9 documents where
    # a reviewer dismissed at least one group - they demonstrably worked through that document, which
    # makes the groups they left alone real acceptances. 39 clusters, 19 dismissed and 20 kept,
    # similarity recomputed from stored source_text so every cluster sits on one scale.
    #
    #     corrected scale   worst false positive 1.000   lowest real duplicate 0.529   -> TOTAL OVERLAP
    #     old scale (#81)   worst false positive 0.823   lowest real duplicate 0.994   -> gap
    #
    # 18 of the 19 false positives sit at or above the duplicate floor. Widening the comparison window
    # does not rescue it either - see `dedup._min_difflib`, where 1500/3000/6000/full text all overlap.
    #
    # WHY IT CANNOT WORK, and it is the reviewers' own rule rather than a defect: a duplicate whose
    # second scan OCR'd badly scores LOW ("sometimes the scan quality is different, so we can pick
    # which one is the best"), and a recurring form on different dates scores HIGH and is correctly not
    # a duplicate. Measured examples: a dismissed cluster at 0.993 on full text, a kept one at 0.554.
    # The distinction is whether two documents are the same EVENT, which text similarity does not
    # measure. That is also why the gate leads on `same_date and (same_title or same_category)` and
    # this knob is only the cross-date escape hatch.
    #
    # SO TREAT THIS AS A RECALL DIAL, NOT A DERIVABLE THRESHOLD. On the labelled set, counting only the
    # similarity path into the gate:
    #
    #     threshold   false positives admitted   real duplicates lost
    #        0.90              14                        7
    #        0.95              12                        9
    #        0.97               7                       13
    #        0.99               4                       14
    #        0.995              1                       14
    #
    # 0.99 spends 14 real duplicates to avoid 4 false positives. Whether that is the right trade is a
    # judgement about which error costs more, and the reviewers have answered that in words rather than
    # in data - "don't automatically delete anything, let the reviewer determine which one to keep" -
    # which argues a missed duplicate is worse than a surfaced one. Open on issue #125.
    #
    # HISTORY, and it is why this default is 0.90 rather than 0.99. #67 added
    # `DUPE_SIMILARITY_OVERRIDE: ${DUPE_SIMILARITY_OVERRIDE:-0.90}` to `docker-compose.yml` while 0.90
    # was still the default here, then #81 raised this one to 0.99 and did not touch compose. Because
    # compose passes the key EXPLICITLY, a container reads the compose default and never this file, so
    # 0.99 has never run anywhere: every container has served 0.90 continuously since #67.
    #
    # I previously recorded here that the box `.env` pinned 0.99 on 2026-08-25 and that "the value now
    # running matches this file for the first time". BOTH HALVES WERE WRONG, and the correction is the
    # reason this line changed. Read off the box on 2026-08-25: `.env` line 18 is
    # `DUPE_SIMILARITY_OVERRIDE=0.90`, and the running api resolves `dupe_similarity_override` to 0.9.
    # So production is now EXPLICITLY pinned to the value it had always served, and 0.99 remained a
    # code default that had never been deployed.
    #
    # 0.90 is also the direction the evidence points rather than merely the status quo. The trade table
    # above shows 0.99 spending 14 real duplicates to avoid 4 false positives, and the reviewers' rule
    # is that nothing is deleted automatically - "let the reviewer determine which one to keep" - which
    # makes a missed duplicate the more expensive error. Aligning here changes NO deployed behaviour:
    # compose and the box `.env` both already say 0.90. It only stops the code contradicting them, and
    # it does not preempt the threshold decision still open on #125.
    dupe_jaccard_threshold: float = 0.70
    dupe_similarity_override: float = 0.90
    dupe_model_override: float = 0.95

    # How long the UI waits for a COOPERATIVE stop before offering "Force stop". The cooperative path
    # normally lands within a second, because the retry backoff polls for a cancel between one-second
    # sleep slices - so this is patience for the pathological case (a worker wedged somewhere that
    # reaches no check at all), not an expected wait. Raising it delays the escape hatch; lowering it
    # invites a hard kill that leaves orphan recovery to tidy up a half-finished run.
    job_cancel_grace_seconds: int = 10

    # Segmentation + verification tuning (ported verbatim).
    window_budget_mb: float = 12.5
    window_overlap: int = 30
    # Hard cap on pages in ONE segmentation vision call. window_budget_mb bounds request SIZE; this
    # bounds request DURATION, which bytes cannot - a byte-light record packs a huge page count into
    # one budget-sized window. Document 68cb2500 (~52KB/page) put all 241 of its pages in a single
    # 12.5 MB window and failed 6/6 times, because that call needs longer than the deadline.
    #
    # 100 is a round number chosen to sit clearly BELOW the observed failure onset rather than at the
    # edge of it. Measured on the box 2026-08-12 (scripts/eval/window_duration_curve.py): 80 pages
    # took 20.2s, 120 took 39.9s, 160 took 54.5s, 200 took 106.3s, 241 took 179.0s - all against a
    # 120s deadline. Production windows of 180 and 188 pages errored 2/3 and 1/2, so the failure zone
    # starts near 180; 100 lands around 30s, comfortably clear even with segment_window_workers
    # windows running at once.
    #
    # The cost of a lower cap is more windows per record - more calls, more spend, and more seams
    # where over-segmentation can appear. The overlap plus the ownership merge exist to handle seams,
    # and the recall A/B on the affected labelled cases is a follow-up, not a blocker.
    window_max_pages: int = 100
    verify_merge: bool = True
    verify_use_text: bool = True
    verify_suspect_cap: int = 200
    bundle_summarize_cap: int = 40

    def effective_job_timeout(self, pages: int) -> int:
        """The size-aware RQ wall-clock cap (seconds) for a document of ``pages`` pages: a flat
        floor for small records, scaling by page count for large ones. Single source of the
        formula shared by services.jobs.enqueue and worker.tasks."""
        return max(self.job_timeout, int(pages * self.job_timeout_per_page))

    def pool_timeout(self, pages: int) -> int:
        """The wall-clock ceiling (seconds) for one ThreadPoolExecutor drain: the size-aware job
        timeout minus a margin, so a stalled pool is abandoned JUST before RQ's SIGKILL (which
        would otherwise orphan the job) yet a legitimately long pool on a large record is not cut
        short. Floored at 1 so a tiny job_timeout in a test never yields a non-positive timeout."""
        return max(1, self.effective_job_timeout(pages) - self.future_timeout_margin_seconds)

    @model_validator(mode="after")
    def _derive(self) -> "Settings":
        default_model = "gemini-2.5-flash" if self.use_vertex else "gemini-flash-latest"
        self.genai_model = self.genai_model or default_model
        # Summarization uses 3.5-flash as of 2026-08-14, replacing the 2.5-pro chosen in an earlier
        # A/B for condensing + faithfulness on long records. Two reasons, in order of weight:
        #
        # 1. Operational. On 2026-08-13 Vertex stopped admitting 2.5-pro for this project outright
        #    (0/8 on the configured endpoint, rejections in ~0.1s) and every summarize job failed. It
        #    recovered to 8/8 by 2026-08-14 with nothing changed on our side, so the condition is
        #    external and can recur without warning. 3.5-flash was 8/8 throughout.
        # 2. Quality is a WASH, and deliberately not claimed as an argument for flash. Scored
        #    2026-08-14 against the frozen human baselines, both arms 39/39 on identical rows: pro sat
        #    nearer the human length (1.41x vs 1.59x on category 1) and 2 points higher on point
        #    precision, while flash retained normal findings far better on category 3 (56% vs 33%
        #    against a human 49%) and its audit parsed 39/39 where pro's failed 3 times. At n=30 and
        #    n=9 the precision gaps are inside noise; the length ratio is the one real difference.
        #
        # Known limit of that evidence: only categories 1 and 3 had rows. Nine scored zero, including
        # every long-document category - which is exactly where a model difference would show. Revisit
        # if a record set covering them becomes available.
        #
        # Summary-ONLY - genai_model (segmentation, header/DOI) and classify_model (categorization)
        # are untouched, so neither can regress. SUMMARY_MODEL overrides.
        self.summary_model = self.summary_model or "gemini-3.5-flash"
        self.verify_model = self.verify_model or self.genai_model
        if self.environment == "prod" and not self.use_vertex:
            raise RuntimeError(
                "GOOGLE_GENAI_USE_VERTEXAI must be true in production: PHI may only go to the "
                "BAA-covered Vertex endpoint, never the Developer API."
            )
        self.summary_provider = (self.summary_provider or "gemini").strip().lower()
        if self.summary_provider != "openai":
            # Gemini per-call-type defaults. The body call reads page images and applies a long
            # format spec, so it keeps summary_model. The title is extraction from OCR text and the
            # audit is a check, so both step down to flash. Justified by call reduction alone,
            # independent of which model the body happens to be running.
            #
            # Set HERE rather than as field defaults so the openai branch below still sees "" for an
            # unset key and can refuse to start. A field default would silently satisfy that guard.
            self.summary_body_model = self.summary_body_model or self.summary_model
            self.summary_title_model = self.summary_title_model or "gemini-2.5-flash"
            self.audit_model = self.audit_model or "gemini-2.5-flash"
            # Defaulted ON rather than opt-in: the failure it guards against is an outage of the
            # configured body model, and someone raising SUMMARY_MODEL to a pro tier is exactly the
            # person who will not have thought about it. Harmless when the body already IS this model
            # - summarize_engine skips a fallback that equals the model that just failed.
            _fb = self.summary_body_fallback_model.strip()
            self.summary_body_fallback_model = (
                "" if _fb.lower() in ("none", "off") else (_fb or "gemini-3.5-flash")
            )
        if self.summary_provider == "openai":
            # Fail at startup, not on the first summary. A worker that boots and then errors per row
            # burns a job and leaves the reviewer with a half-processed document.
            missing = [
                name
                for name, value in (
                    ("OPENAI_API_KEY", self.openai_api_key),
                    ("SUMMARY_BODY_MODEL", self.summary_body_model),
                    ("SUMMARY_TITLE_MODEL", self.summary_title_model),
                    ("AUDIT_MODEL", self.audit_model),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "SUMMARY_PROVIDER=openai requires " + ", ".join(missing) + ". There is no "
                    "default model on purpose: pick one by measuring it against the frozen human "
                    "baselines, not by inheriting a guess."
                )
            if self.environment == "prod" and not self.openai_zdr_acknowledged:
                raise RuntimeError(
                    "OPENAI_ZDR_ACKNOWLEDGED must be true to send PHI to OpenAI in production. A "
                    "signed BAA is not sufficient on its own - Zero Data Retention (or Modified "
                    "Abuse Monitoring / Eyes Off) must also be approved on the organization. Check "
                    "Settings > Organization > Data controls > Data retention before setting this."
                )
        return self

    def model_for(self, kind: str) -> str:
        """The model that should answer one summarize-stage call: "body", "title" or "audit".

        One resolver for BOTH providers now: ``_derive`` populates all three keys on the Gemini path
        and refuses to start without them on the OpenAI path, so by the time this is reachable each
        one holds a real model name. It previously ignored the keys entirely on Gemini and returned
        summary_model for all three, which is why the per-call-type settings did nothing there.

        Read ONCE, at job creation, and persisted on the Job (see services/jobs.create_job). Do not
        call this per row: a job resumed after a config change must keep the models it started with,
        or one delivered document ends up written by two different models with no record of which.
        """
        return {
            "body": self.summary_body_model,
            "title": self.summary_title_model,
            "audit": self.audit_model,
        }[kind]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env/.env
