/* Document-scoped review flow: boot from the document's persisted state, then move
   freely between the three pipeline steps (identify / review / summaries). Rows and
   summary edits autosave. Vanilla JS on purpose: no build step, nothing to break the
   day of a demo. */
"use strict";

const DOC_ID = document.body.dataset.docId;
// PDF.js viewer (vendored): Chrome's built-in viewer ignores #page changes after the
// first load, so row-click navigation NEEDS a viewer with a programmatic page API.
const VIEWER_URL = "/static/vendor/pdfjs/web/viewer.html";

const SUMMARY_PAGE_SIZE = 20;

const S = {
    rows: [],          // {start, end, category, title, date, injury_date, flag, suggest_merge, include}
    categories: [],
    totalPages: 0,
    status: "",
    selected: -1,
    lastViewerPage: 0,
    viewerLoaded: false,
    splitting: -1,     // row index currently showing the inline split form
    watching: null,    // "segment" | "summarize" while a job poll drives the view
    saveTimer: null,
    summaries: [],     // fetched summary listing (all pages; paginated client-side)
    summaryPage: 0,
    editingSummary: -1, // summary idx currently in in-place edit mode
};

const $ = (id) => document.getElementById(id);
const sections = ["step-start", "step-progress", "step-editor", "step-summaries"];

// Human labels for the 13 curated categories (mirrors taxonomy.py names).
const CATEGORY_LABELS = {
    "1": "1 - Progress / follow-up (PR-2)",
    "2": "2 - Comprehensive eval (PR-4)",
    "3": "3 - Diagnostic / imaging",
    "4": "4 - GI procedure H&P",
    "5": "5 - PT / chiro / acupuncture",
    "6": "6 - Daily / SOAP notes",
    "7": "7 - WC claim forms",
    "8": "8 - Operative / pathology",
    "9": "9 - Deposition",
    "10": "10 - Request for authorization",
    "11": "11 - Interval history / MDM",
    "12": "12 - QME/AME supplemental",
    "13": "13 - QME/AME evaluation",
    "100": "100 - General / other",
};

const STEP_ORDER = ["identify", "review", "summaries"];

function show(section, activeStep) {
    sections.forEach((id) => $(id).classList.toggle("hidden", id !== section));
    const defaults = {
        "step-start": "identify", "step-progress": "identify",
        "step-editor": "review", "step-summaries": "summaries",
    };
    // Stepper states are positional: steps before the active one read as done
    // (green check), after it as upcoming - the design's three-state stepper.
    const activeIdx = STEP_ORDER.indexOf(activeStep || defaults[section]);
    document.querySelectorAll(".ev-step").forEach((el) => {
        const idx = STEP_ORDER.indexOf(el.dataset.step);
        el.classList.toggle("done", idx < activeIdx);
        el.classList.toggle("active", idx === activeIdx);
        el.classList.toggle("busy", Boolean(S.watching));
    });
}

function banner(message) {
    const el = $("banner");
    el.textContent = message || "";
    el.classList.toggle("hidden", !message);
}

function cookieValue(name) {
    const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

/* All data flows through the owner-checked documents API. Unsafe methods carry the
   CSRF cookie back as a header (Flask-Security cookie/header pattern). */
async function api(path, options = {}) {
    const opts = { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
    if (opts.method && opts.method !== "GET") {
        opts.headers["X-XSRF-Token"] = cookieValue("XSRF-TOKEN");
    }
    if (opts.json !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(opts.json);
        delete opts.json;
    }
    const resp = await fetch(`/api/documents/${DOC_ID}${path}`, opts);
    if (resp.status === 401) { window.location = "/login"; throw new Error("signed out"); }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `${path} failed (${resp.status})`);
    return data;
}

/* ---------- step navigation (free movement between the three pipeline steps) ---------- */

function gotoStep(step) {
    // While a job poll is driving the view, the progress panel holds the screen -
    // navigating away would fight the auto-advance when the job lands.
    if (S.watching) return;
    banner("");
    if (step === "identify") {
        renderStartPanel();
        show("step-start");
    } else if (step === "review") {
        if (S.rows.length) enterEditor();
        else {
            renderStartPanel("No documents identified yet - run identification first.");
            show("step-start");
        }
    } else if (step === "summaries") {
        loadSummaries().catch((err) => banner(err.message));
    }
}

document.querySelectorAll(".ev-step").forEach((el) => {
    el.addEventListener("click", () => gotoStep(el.dataset.step));
});

function renderStartPanel(hint) {
    const rerun = S.rows.length > 0;
    $("startTitle").textContent = rerun ? "Re-run document identification" : "Ready to identify documents";
    $("startSegment").textContent = rerun ? "Re-run identification" : "Identify documents";
    $("startHint").textContent = hint || (rerun
        ? "Re-running replaces the current document list - including every correction "
          + "you made - with a fresh AI pass over the record."
        : "The record is split into its component documents and categorized. You review "
          + "and correct the result before any summaries are written.");
}

/* ---------- boot: route from the document's persisted state ---------- */

const STAGE_LABELS = {
    starting: "Starting...",
    segmenting: "Reading the record and finding document boundaries",
    categorizing: "Categorizing each document",
    verifying: "Double-checking uncertain boundaries",
    summarizing: "Writing summaries",
};

async function boot() {
    let detail;
    try {
        detail = await api("");
    } catch (err) {
        banner(`Could not load this document: ${err.message}`);
        renderStartPanel();
        show("step-start");
        $("startSegment").disabled = true;
        return;
    }
    S.totalPages = detail.page_count;
    S.categories = detail.categories || [];
    S.rows = detail.rows || [];
    S.status = detail.status;
    $("docFilename").textContent = detail.original_filename || "";

    const job = detail.active_job;
    if (job && job.kind === "segment") return watchSegment();
    if (job && job.kind === "summarize") return watchSummarize();
    if (detail.status === "done") return loadSummaries().catch((err) => banner(err.message));
    if (detail.status === "error") banner("The last run failed - you can start again.");
    if (detail.status === "interrupted") banner("The last run was interrupted - start again.");
    if (S.rows.length) return enterEditor();
    renderStartPanel();
    show("step-start");
}

function pollDocument(title, activeStep) {
    $("progressTitle").textContent = title;
    $("barFill").style.width = "0%";
    show("step-progress", activeStep);
    return new Promise((resolve, reject) => {
        const timer = setInterval(async () => {
            let snap;
            try {
                snap = await api("/status");
            } catch (err) {
                clearInterval(timer);
                return reject(err);
            }
            const job = snap.job || {};
            const pct = job.total ? Math.round((100 * job.current) / job.total) : 5;
            $("barFill").style.width = `${Math.max(pct, 4)}%`;
            const label = STAGE_LABELS[job.stage] || job.stage || "Working";
            $("progressDetail").textContent =
                job.total ? `${label} (${job.current}/${job.total})` : label;
            if (job.state === "done") { clearInterval(timer); resolve(snap); }
            if (job.state === "error") { clearInterval(timer); reject(new Error(job.error)); }
            if (job.state === "interrupted") {
                clearInterval(timer);
                reject(new Error("the run was interrupted"));
            }
        }, 1000);
    });
}

async function watchSegment() {
    S.watching = "segment";
    try {
        await pollDocument("Identifying documents", "identify");
        const detail = await api("");
        S.rows = detail.rows || [];
        S.status = detail.status;
        S.watching = null;
        enterEditor();
    } catch (err) {
        S.watching = null;
        banner(err.message);
        renderStartPanel();
        show("step-start");
    }
}

async function watchSummarize() {
    S.watching = "summarize";
    try {
        await pollDocument("Summarizing documents", "summaries");
        S.status = "done";
        S.watching = null;
        await loadSummaries();
    } catch (err) {
        S.watching = null;
        banner(err.message);
        if (S.rows.length) enterEditor(); else { renderStartPanel(); show("step-start"); }
    }
}

$("startSegment").addEventListener("click", async () => {
    if (S.rows.length && !window.confirm(
        "Re-running identification replaces the current document list AND your "
        + "corrections. Continue?")) {
        return;
    }
    banner("");
    $("startSegment").disabled = true;
    try {
        await api("/segment/start", { method: "POST", json: {} });
        await watchSegment();
    } catch (err) {
        banner(err.message);
        renderStartPanel();
        show("step-start");
    } finally {
        $("startSegment").disabled = false;
    }
});

/* ---------- review & correct ---------- */

function viewerSrc(page) {
    const file = encodeURIComponent(`/api/documents/${DOC_ID}/pdf`);
    return `${VIEWER_URL}?file=${file}#page=${page}`;
}

function enterEditor() {
    show("step-editor");
    if (!S.viewerLoaded) {
        $("pdfFrame").src = viewerSrc(1);
        S.viewerLoaded = true;
        S.lastViewerPage = 1;
    }
    renderTable();
}

function jumpTo(page) {
    if (page === S.lastViewerPage) return;
    S.lastViewerPage = page;
    const frame = $("pdfFrame");
    if (!S.viewerLoaded) {
        frame.src = viewerSrc(page);
        S.viewerLoaded = true;
        return;
    }
    try {
        const viewer = frame.contentWindow.PDFViewerApplication;
        if (viewer && viewer.pdfViewer && viewer.pdfViewer.pagesCount) {
            viewer.page = page;
            return;
        }
    } catch (err) {
        // same-origin, so this only happens while the viewer is still booting
    }
    frame.src = viewerSrc(page); // viewer not ready yet: (re)load opened at the page
}

/* Autosave: corrections must survive switching documents / closing the tab. Saves are
   debounced and only sent for VALID states (invalid intermediate edits stay local). */
const SAVE_CHECK =
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"></path></svg>';

function setSaveState(kind, message) {
    const el = $("saveState");
    el.className = `rc-save ${kind}`;
    if (kind === "saved") el.innerHTML = `${SAVE_CHECK}Saved`;
    else el.textContent = message || "";
}

function scheduleSave() {
    clearTimeout(S.saveTimer);
    setSaveState("dirty", "Unsaved changes...");
    S.saveTimer = setTimeout(saveRows, 800);
}

async function saveRows() {
    if (!S.rows.length || rowErrors().size) return;
    try {
        await api("/rows", { method: "PUT", json: { rows: S.rows } });
        setSaveState("saved");
    } catch (err) {
        setSaveState("error", `Not saved: ${err.message}`);
    }
}

function sortRows() {
    S.rows.sort((a, b) => (a.start - b.start) || (a.end - b.end));
}

function rowErrors() {
    // Mirrors the server rules; gaps are allowed (users skip junk pages on purpose).
    const errors = new Map();
    let previousEnd = 0;
    S.rows.forEach((row, i) => {
        const s = Number(row.start), e = Number(row.end);
        if (!Number.isInteger(s) || !Number.isInteger(e)) errors.set(i, "pages must be numbers");
        else if (s < 1 || e > S.totalPages || s > e)
            errors.set(i, `needs 1 <= start <= end <= ${S.totalPages}`);
        else if (s <= previousEnd) errors.set(i, "overlaps the previous document");
        previousEnd = Math.max(previousEnd, Number.isInteger(e) ? e : previousEnd);
    });
    return errors;
}

function categoryOptions(current) {
    return S.categories.map((c) => {
        const label = CATEGORY_LABELS[c] || c;
        const sel = String(current) === c ? " selected" : "";
        return `<option value="${c}"${sel}>${label}</option>`;
    }).join("");
}

function renderTable() {
    sortRows();
    const errors = rowErrors();
    const body = $("rowsBody");
    body.innerHTML = "";
    let previousEnd = 0;

    S.rows.forEach((row, i) => {
        if (Number(row.start) > previousEnd + 1) {
            const gap = document.createElement("tr");
            gap.className = "gap-row";
            gap.innerHTML = `<td colspan="8">pages ${previousEnd + 1}-${Number(row.start) - 1} not included (skipped at summarization)</td>`;
            body.appendChild(gap);
        }
        previousEnd = Math.max(previousEnd, Number(row.end) || previousEnd);

        const included = row.include !== false;
        // The TITLE row leads its group (title above the fields it describes, row
        // actions on its right per the settled design) and is editable in place;
        // empty titles persist as "-" server-side.
        const actions = S.splitting === i
            ? `at page <input type="number" class="split-page" min="${Number(row.start) + 1}" max="${row.end}" value="${Number(row.start) + 1}" aria-label="First page of the second document">
               <button class="ev-btn ev-btn-sm ev-btn-outline" data-action="split-confirm">Split</button>
               <button class="ev-btn ev-btn-sm ev-btn-ghost" data-action="split-cancel">Cancel</button>`
            : `${row.suggest_merge && i > 0 ? '<button class="ev-btn ev-btn-sm ev-btn-gold" data-action="merge" title="The AI double-checked this boundary and believes it continues the document above">Likely same doc \u2014 merge?</button>' : ""}
               ${i > 0 ? '<button class="ev-btn ev-btn-sm ev-btn-outline" data-action="merge" title="Merge into the document above">Merge up</button>' : ""}
               ${Number(row.end) > Number(row.start) ? '<button class="ev-btn ev-btn-sm ev-btn-outline" data-action="split" title="Split this document into two">Split</button>' : ""}
               <button class="ev-btn ev-btn-sm ev-btn-del" data-action="delete" title="Remove this row">Delete</button>`;

        const titleRow = document.createElement("tr");
        titleRow.className = "doc-row title-row" + (S.selected === i ? " selected" : "")
            + (included ? "" : " skipped");
        titleRow.dataset.idx = i;
        titleRow.innerHTML = `
            <td class="col-num rc-titletd">${i + 1}</td>
            <td colspan="7" class="rc-titletd">
                <div class="rc-titlebar">
                    <input type="text" class="rc-title" data-field="title" placeholder="(untitled document)" aria-label="Document title">
                    <span class="rc-rowactions">${actions}</span>
                </div>
            </td>`;
        titleRow.querySelector("input.rc-title").value =
            row.title && row.title !== "-" ? row.title : "";
        body.appendChild(titleRow);

        const tr = document.createElement("tr");
        tr.className = "doc-row" + (errors.has(i) ? " invalid" : "")
            + (S.selected === i ? " selected" : "") + (included ? "" : " skipped");
        tr.dataset.idx = i;
        tr.innerHTML = `
            <td class="col-num"></td>
            <td><input type="number" class="rc-inp" data-field="start" value="${row.start}" min="1" max="${S.totalPages}"></td>
            <td><input type="number" class="rc-inp" data-field="end" value="${row.end}" min="1" max="${S.totalPages}"></td>
            <td><span class="rc-selwrap"><select class="rc-sel" data-field="category">${categoryOptions(row.category)}</select></span></td>
            <td><input type="text" class="rc-inp" data-field="date" value="${row.date || "-"}"></td>
            <td class="col-check"><input type="checkbox" class="ev-cb" data-field="flag" ${String(row.flag).toLowerCase() === "x" ? "checked" : ""}></td>
            <td class="col-check col-sum"><input type="checkbox" class="ev-cb" data-field="include" ${included ? "checked" : ""}></td>
            <td></td>`;
        body.appendChild(tr);
    });

    const suggested = S.rows.filter((r, i) => r.suggest_merge && i > 0).length;
    const bulk = $("applySuggestions");
    bulk.classList.toggle("hidden", suggested === 0);
    bulk.textContent = `Apply ${suggested} suggested merge${suggested === 1 ? "" : "s"}`;

    const included = S.rows.filter((r) => r.include !== false).length;
    $("docCount").textContent =
        `${S.rows.length} documents / ${S.totalPages} pages`;
    const firstError = errors.size ? `row ${[...errors.keys()][0] + 1}: ${[...errors.values()][0]}` : "";
    $("validationMsg").textContent = firstError;
    const summarizeBtn = $("summarizeBtn");
    summarizeBtn.disabled = errors.size > 0 || included === 0;
    summarizeBtn.textContent = included ? `Summarize ${included} document${included === 1 ? "" : "s"}` : "Summarize";
}

$("rowsBody").addEventListener("change", (event) => {
    const tr = event.target.closest("tr[data-idx]");
    if (!tr) return;
    const row = S.rows[Number(tr.dataset.idx)];
    const field = event.target.dataset.field;
    if (!field) return;
    if (field === "flag") row.flag = event.target.checked ? "x" : "-";
    else if (field === "include") row.include = event.target.checked;
    else if (field === "start" || field === "end") row[field] = Number(event.target.value);
    else row[field] = event.target.value;
    renderTable();
    scheduleSave();
});

$("rowsBody").addEventListener("click", (event) => {
    const tr = event.target.closest("tr[data-idx]");
    if (!tr) return;
    const idx = Number(tr.dataset.idx);
    const action = event.target.dataset.action;
    if (action === "delete") {
        S.rows.splice(idx, 1);
        S.selected = -1;
        S.splitting = -1;
        renderTable();
        scheduleSave();
        return;
    }
    if (action === "merge") {
        // The row above absorbs this one's pages; its metadata (title/category/date) wins.
        S.rows[idx - 1].end = Math.max(S.rows[idx - 1].end, S.rows[idx].end);
        S.rows[idx - 1].flag =
            [S.rows[idx - 1].flag, S.rows[idx].flag].includes("x") ? "x" : "-";
        S.rows.splice(idx, 1);
        S.selected = idx - 1;
        S.splitting = -1;
        renderTable();
        scheduleSave();
        return;
    }
    if (action === "split") {
        S.splitting = idx;
        renderTable();
        return;
    }
    if (action === "split-cancel") {
        S.splitting = -1;
        renderTable();
        return;
    }
    if (action === "split-confirm") {
        // Pages start..k-1 stay here; k..end become a new row that inherits category and
        // dates and is flagged for review (its metadata was extracted from the whole span).
        const row = S.rows[idx];
        const k = Number(tr.querySelector(".split-page").value);
        if (!Number.isInteger(k) || k <= Number(row.start) || k > Number(row.end)) {
            tr.querySelector(".split-page").classList.add("invalid");
            return;
        }
        S.rows.splice(idx + 1, 0, {
            start: k, end: Number(row.end), category: row.category, title: "-",
            date: row.date, injury_date: row.injury_date, flag: "x",
            include: row.include !== false,
        });
        row.end = k - 1;
        S.splitting = -1;
        S.selected = idx + 1;
        renderTable();
        scheduleSave();
        jumpTo(k);
        return;
    }
    if (event.target.tagName === "INPUT" || event.target.tagName === "SELECT") return;
    S.selected = idx;
    renderTable();
    jumpTo(Number(S.rows[idx].start) || 1);
});

$("applySuggestions").addEventListener("click", () => {
    // Apply every AI merge suggestion in one pass (top-down so chains collapse).
    // The human keeps the veto: suggestions are visible as chips before this click.
    for (let i = 1; i < S.rows.length; ) {
        if (S.rows[i].suggest_merge) {
            S.rows[i - 1].end = Math.max(S.rows[i - 1].end, S.rows[i].end);
            S.rows[i - 1].flag =
                [S.rows[i - 1].flag, S.rows[i].flag].includes("x") ? "x" : "-";
            S.rows.splice(i, 1);
        } else {
            i += 1;
        }
    }
    S.selected = -1;
    S.splitting = -1;
    renderTable();
    scheduleSave();
});

/* Insert a missed document anywhere: the user types the page range and the row sorts
   into its ascending position (a document the AI missed is usually mid-file, so
   appending at the end would just move the correction work to the user). */
function closeAddForm() {
    $("addForm").classList.add("hidden");
    $("addRow").classList.remove("hidden");
    $("addStart").classList.remove("invalid");
    $("addEnd").classList.remove("invalid");
}

$("addRow").addEventListener("click", () => {
    const last = S.rows[S.rows.length - 1];
    const start = last ? Math.min(Number(last.end) + 1, S.totalPages) : 1;
    ["addStart", "addEnd"].forEach((id) => { $(id).max = S.totalPages; $(id).value = start; });
    $("addForm").classList.remove("hidden");
    $("addRow").classList.add("hidden");
    $("addStart").focus();
});

$("addCancel").addEventListener("click", closeAddForm);

$("addConfirm").addEventListener("click", () => {
    const start = Number($("addStart").value);
    const end = Number($("addEnd").value);
    const bad = (v) => !Number.isInteger(v) || v < 1 || v > S.totalPages;
    $("addStart").classList.toggle("invalid", bad(start) || start > end);
    $("addEnd").classList.toggle("invalid", bad(end) || start > end);
    if (bad(start) || bad(end) || start > end) return;
    const row = {
        start, end, category: "100", title: "(added manually)",
        date: "-", injury_date: "-", flag: "x", include: true,
    };
    S.rows.push(row);
    sortRows();
    S.selected = S.rows.indexOf(row);
    S.splitting = -1;
    closeAddForm();
    renderTable();
    scheduleSave();
    jumpTo(start);
});

/* ---------- summarize ---------- */

$("summarizeBtn").addEventListener("click", async () => {
    banner("");
    clearTimeout(S.saveTimer);
    try {
        // The rows travel with the request: the server persists this exact editor
        // state before queueing, so what you see is what gets summarized.
        await api("/summarize/start", { method: "POST", json: { rows: S.rows } });
        await watchSummarize();
    } catch (err) {
        banner(err.message);
        show("step-editor");
    }
});

/* ---------- summaries: read, edit in place, exclude, paginate, export ---------- */

async function loadSummaries() {
    S.summaries = await api("/summaries");
    S.summaryPage = 0;
    S.editingSummary = -1;
    renderSummaries();
}

/* The engine bakes decorations into the STORED strings ("[ManualCheck] " and
   "[Diagnostic Study]" title tags, a " (Pages X-Y)" title suffix, a "**DOI**:date,"
   text prefix). Stored data keeps them (the Word export format depends on it - the
   server recomposes every decoration at export time from structured fields); the
   web view lifts them out and shows chips / meta entries instead. */
function parseDisplay(item) {
    const title = (item.summaryTitle || "")
        .replace(/^\s*\[ManualCheck\]\s*/i, "")
        .replace(/\s*\(Pages\s+\d+\s*[-\u2013]\s*\d+\)\s*$/i, "")
        .replace(/\s*\[Diagnostic Study\]\s*$/i, "");
    let text = item.summaryText || "";
    let doi = null;
    const match = text.match(/^\s*\*\*DOI\*\*:\s*([^,]*),?\s*/);
    if (match) {
        doi = match[1].trim();
        text = text.slice(match[0].length);
    }
    return { title, text, doi };
}

function summaryCounts() {
    const excluded = S.summaries.filter((s) => s.excluded).length;
    const n = S.summaries.length;
    const suffix = excluded ? ` \u2014 ${excluded} excluded from export` : "";
    $("summaryCount").textContent = n ? `${n} summar${n === 1 ? "y" : "ies"}${suffix}` : "";
}

// Lucide icons (stroke follows the chip's text color via currentColor).
const CHIP_ICONS = {
    review: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>',
    edit: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"></path></svg>',
};

function buildSummaryCard(item) {
    const { title, text, doi } = parseDisplay(item);
    const card = document.createElement("div");
    card.className = "summary-card" + (item.excluded ? " excluded" : "");
    card.dataset.idx = item.idx;

    const meta = [
        item.summaryDate || "no date",
        `pages ${item.row.start}\u2013${item.row.end}`,
        CATEGORY_LABELS[String(item.row.category)] || item.row.category,
        doi ? `DOI ${doi}` : "",
    ].filter(Boolean).join(" \u00b7 ");

    if (S.editingSummary !== item.idx) {
        const chips = [
            item.manualCheck
                ? `<span class="ev-chip ev-chip-review">${CHIP_ICONS.review}needs review</span>` : "",
            item.edited ? `<span class="ev-chip ev-chip-edit">${CHIP_ICONS.edit}edited</span>` : "",
            item.excluded ? '<span class="ev-chip ev-chip-off">excluded</span>' : "",
        ].join("");
        card.innerHTML = `
            <div class="summary-head">
                <h3 class="sum-heading"></h3>
                ${chips}
                <span class="card-actions">
                    <button class="ev-btn ev-btn-ghost ev-btn-sm" data-action="edit-summary" title="Edit this summary in place">Edit</button>
                    <label class="exclude-toggle" title="Excluded summaries stay here but are left out of the Word export">
                        <input type="checkbox" class="ev-cb" data-action="toggle-exclude" ${item.excluded ? "checked" : ""}> Exclude
                    </label>
                </span>
            </div>
            <div class="meta">${meta}</div>
            <p class="body"></p>`;
        card.querySelector(".sum-heading").textContent = title;
        card.querySelector("p.body").textContent = text;
    } else {
        card.classList.add("editing");
        card.innerHTML = `
            <div class="summary-head">
                <input class="ev-inp sum-title" aria-label="Summary title">
                <input class="ev-inp sum-date" aria-label="Summary date">
            </div>
            <div class="meta">${meta}</div>
            <textarea class="ev-inp sum-text" aria-label="Summary text"></textarea>
            <div class="edit-actions">
                <button class="ev-btn ev-btn-primary" data-action="save-summary">Save</button>
                <button class="ev-btn ev-btn-ghost" data-action="cancel-edit">Cancel</button>
            </div>`;
        card.querySelector(".sum-title").value = title;
        card.querySelector(".sum-date").value = item.summaryDate || "";
        const area = card.querySelector(".sum-text");
        area.value = text;
        // Size the textarea to its content so editing starts with everything visible.
        requestAnimationFrame(() => {
            area.style.height = "auto";
            area.style.height = `${Math.min(area.scrollHeight + 4, 640)}px`;
        });
    }
    return card;
}

function renderPager(pageCount) {
    const pager = $("summaryPagerBottom");
    pager.classList.toggle("hidden", pageCount <= 1);
    if (pageCount <= 1) {
        pager.innerHTML = "";
        return;
    }
    const first = S.summaryPage * SUMMARY_PAGE_SIZE + 1;
    const last = Math.min((S.summaryPage + 1) * SUMMARY_PAGE_SIZE, S.summaries.length);
    pager.innerHTML = `
        <button class="ev-btn ev-btn-outline ev-btn-sm" data-page="prev" ${S.summaryPage === 0 ? "disabled" : ""}>Prev</button>
        <span>Page ${S.summaryPage + 1} of ${pageCount} \u00b7 ${first}\u2013${last} of ${S.summaries.length}</span>
        <button class="ev-btn ev-btn-outline ev-btn-sm" data-page="next" ${S.summaryPage >= pageCount - 1 ? "disabled" : ""}>Next</button>`;
}

function renderSummaries() {
    const list = $("summaryList");
    list.innerHTML = "";
    summaryCounts();
    $("exportBtn").disabled =
        S.summaries.length === 0 || S.summaries.every((s) => s.excluded);

    if (!S.summaries.length) {
        renderPager(0);
        const empty = document.createElement("div");
        empty.className = "summary-empty";
        empty.innerHTML = `
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M14 2v4a2 2 0 0 0 2 2h4"></path><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><line x1="10" y1="9" x2="8" y2="9"></line></svg>
            <p class="empty-title">No summaries yet</p>
            <p>Summaries appear here after you run summarization from Review &amp; correct.</p>`;
        const btn = document.createElement("button");
        btn.className = "ev-btn ev-btn-primary";
        btn.textContent = "Go to Review & correct";
        btn.addEventListener("click", () => gotoStep("review"));
        empty.appendChild(btn);
        list.appendChild(empty);
        show("step-summaries");
        return;
    }

    const pageCount = Math.ceil(S.summaries.length / SUMMARY_PAGE_SIZE);
    S.summaryPage = Math.min(S.summaryPage, pageCount - 1);
    const start = S.summaryPage * SUMMARY_PAGE_SIZE;
    S.summaries.slice(start, start + SUMMARY_PAGE_SIZE)
        .forEach((item) => list.appendChild(buildSummaryCard(item)));
    renderPager(pageCount);
    show("step-summaries");
}

$("summaryPagerBottom").addEventListener("click", (event) => {
    const direction = event.target.dataset.page;
    if (!direction) return;
    S.summaryPage += direction === "next" ? 1 : -1;
    S.editingSummary = -1;
    renderSummaries();
    $("step-summaries").scrollTop = 0;
});

async function saveSummary(idx, patch) {
    $("summarySaveState").textContent = "Saving...";
    try {
        const updated = await api(`/summaries/${idx}`, { method: "PUT", json: patch });
        const pos = S.summaries.findIndex((s) => s.idx === idx);
        if (pos >= 0) S.summaries[pos] = updated;
        S.editingSummary = -1;
        $("summarySaveState").textContent = "Saved";
        renderSummaries();
    } catch (err) {
        $("summarySaveState").textContent = `Not saved: ${err.message}`;
    }
}

$("summaryList").addEventListener("click", (event) => {
    const card = event.target.closest(".summary-card");
    const action = event.target.dataset.action;
    if (!card || !action) return;
    const idx = Number(card.dataset.idx);
    if (action === "edit-summary") {
        S.editingSummary = idx;
        renderSummaries();
    } else if (action === "cancel-edit") {
        S.editingSummary = -1;
        renderSummaries();
    } else if (action === "save-summary") {
        saveSummary(idx, {
            summaryTitle: card.querySelector(".sum-title").value,
            summaryDate: card.querySelector(".sum-date").value,
            summaryText: card.querySelector(".sum-text").value,
        });
    }
});

$("summaryList").addEventListener("change", (event) => {
    if (event.target.dataset.action !== "toggle-exclude") return;
    const card = event.target.closest(".summary-card");
    saveSummary(Number(card.dataset.idx), { excluded: event.target.checked });
});

/* ---------- export dialog (the four Word-header fields) ---------- */

function plural(n) {
    return `${n} summar${n === 1 ? "y" : "ies"}`;
}

function openExportDialog() {
    const included = S.summaries.filter((s) => !s.excluded).length;
    const excluded = S.summaries.length - included;
    $("exportDialogNote").textContent =
        `These details fill the report header. ${plural(included)} will be exported`
        + (excluded ? `; ${excluded} excluded` : "")
        + ". Enter only what the report requires \u2014 no additional PHI.";
    $("exportConfirm").textContent = `Export ${plural(included)}`;
    $("exportError").textContent = "";
    $("exportDialog").classList.remove("hidden");
    $("expPatient").focus();
}

function closeExportDialog() {
    $("exportDialog").classList.add("hidden");
}

$("exportBtn").addEventListener("click", openExportDialog);
$("exportDialogClose").addEventListener("click", closeExportDialog);
$("exportCancel").addEventListener("click", closeExportDialog);
$("exportDialog").addEventListener("click", (event) => {
    if (event.target === $("exportDialog")) closeExportDialog(); // backdrop click only
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("exportDialog").classList.contains("hidden")) {
        closeExportDialog();
    }
});

$("exportConfirm").addEventListener("click", async () => {
    $("exportConfirm").disabled = true;
    $("exportError").textContent = "";
    try {
        const resp = await fetch(`/api/documents/${DOC_ID}/export`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-XSRF-Token": cookieValue("XSRF-TOKEN"),
            },
            body: JSON.stringify({
                patientName: $("expPatient").value,
                patientdob: $("expDob").value,
                QMEorAME: $("expQme").value,
                lawfirm: $("expFirm").value,
            }),
        });
        if (!resp.ok) throw new Error(`export failed (${resp.status})`);
        const blob = await resp.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "summaries.docx";
        a.click();
        URL.revokeObjectURL(a.href);
        closeExportDialog();
    } catch (err) {
        $("exportError").textContent = err.message;
    } finally {
        $("exportConfirm").disabled = false;
    }
});

boot();
