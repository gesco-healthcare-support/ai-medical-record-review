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

function show(section, activeStep) {
    sections.forEach((id) => $(id).classList.toggle("hidden", id !== section));
    const defaults = {
        "step-start": "identify", "step-progress": "identify",
        "step-editor": "review", "step-summaries": "summaries",
    };
    const active = activeStep || defaults[section];
    document.querySelectorAll(".steps li").forEach((li) => {
        li.classList.toggle("active", li.dataset.step === active);
        li.classList.toggle("busy", Boolean(S.watching));
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

document.querySelectorAll(".steps li").forEach((li) => {
    li.addEventListener("click", () => gotoStep(li.dataset.step));
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
function scheduleSave() {
    clearTimeout(S.saveTimer);
    $("saveState").textContent = "Unsaved changes...";
    S.saveTimer = setTimeout(saveRows, 800);
}

async function saveRows() {
    if (!S.rows.length || rowErrors().size) return;
    try {
        await api("/rows", { method: "PUT", json: { rows: S.rows } });
        $("saveState").textContent = "Saved";
    } catch (err) {
        $("saveState").textContent = `Not saved: ${err.message}`;
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
        // The TITLE row leads its group (title above the fields it describes) and is
        // editable in place; empty titles persist as "-" server-side.
        const titleRow = document.createElement("tr");
        titleRow.className = "doc-row title-row" + (S.selected === i ? " selected" : "")
            + (included ? "" : " skipped");
        titleRow.dataset.idx = i;
        titleRow.innerHTML = `
            <td class="col-num">${i + 1}</td>
            <td colspan="7" class="row-title">
                <input type="text" data-field="title" placeholder="(untitled document)" aria-label="Document title">
            </td>`;
        titleRow.querySelector("input").value =
            row.title && row.title !== "-" ? row.title : "";
        body.appendChild(titleRow);

        const tr = document.createElement("tr");
        tr.className = "doc-row" + (errors.has(i) ? " invalid" : "")
            + (S.selected === i ? " selected" : "") + (included ? "" : " skipped");
        tr.dataset.idx = i;
        tr.innerHTML = `
            <td class="col-num"></td>
            <td><input type="number" data-field="start" value="${row.start}" min="1" max="${S.totalPages}"></td>
            <td><input type="number" data-field="end" value="${row.end}" min="1" max="${S.totalPages}"></td>
            <td><select data-field="category">${categoryOptions(row.category)}</select></td>
            <td><input type="text" class="date-input" data-field="date" value="${row.date || "-"}"></td>
            <td class="col-check"><input type="checkbox" data-field="flag" ${String(row.flag).toLowerCase() === "x" ? "checked" : ""}></td>
            <td class="col-check"><input type="checkbox" data-field="include" ${included ? "checked" : ""}></td>
            <td class="row-actions">
                ${S.splitting === i ? `at page <input type="number" class="split-page" min="${Number(row.start) + 1}" max="${row.end}" value="${Number(row.start) + 1}" aria-label="First page of the second document">
                <button class="mini" data-action="split-confirm">Split</button>
                <button class="mini" data-action="split-cancel">Cancel</button>` : `
                ${row.suggest_merge && i > 0 ? '<button class="mini suggest" data-action="merge" title="The AI double-checked this boundary and believes it continues the document above">Likely same doc - merge?</button>' : ""}
                ${i > 0 ? '<button class="mini" data-action="merge" title="Merge into the document above">Merge up</button>' : ""}
                ${Number(row.end) > Number(row.start) ? '<button class="mini" data-action="split" title="Split this document into two">Split</button>' : ""}
                <button class="mini" data-action="delete" title="Remove this row">Delete</button>`}
            </td>`;
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

/* The engine bakes tags into the STORED strings ("[ManualCheck] ..." titles, a
   "**DOI**:date," text prefix). Stored data keeps them (the Word export format
   depends on it - the server recomposes tags at export time); the web view lifts
   them out and shows a chip / meta entry instead. */
function parseDisplay(item) {
    const title = (item.summaryTitle || "").replace(/^\s*\[ManualCheck\]\s*/i, "");
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
    const suffix = excluded ? ` - ${excluded} excluded from export` : "";
    $("summaryCount").textContent = S.summaries.length
        ? `${S.summaries.length} summaries${suffix}` : "No summaries yet";
}

function buildSummaryCard(item) {
    const { title, text, doi } = parseDisplay(item);
    const card = document.createElement("div");
    card.className = "summary-card" + (item.excluded ? " excluded" : "");
    card.dataset.idx = item.idx;

    const meta = [
        item.summaryDate || "no date",
        `pages ${item.row.start}-${item.row.end}`,
        CATEGORY_LABELS[String(item.row.category)] || item.row.category,
        doi ? `DOI ${doi}` : "",
    ].filter(Boolean).join(" - ");

    if (S.editingSummary !== item.idx) {
        const chips = [
            item.manualCheck ? '<span class="chip chip-review">needs review</span>' : "",
            item.edited ? '<span class="chip chip-edit">edited</span>' : "",
            item.excluded ? '<span class="chip chip-off">excluded</span>' : "",
        ].join("");
        card.innerHTML = `
            <div class="summary-head">
                <h3 class="sum-heading"></h3>
                <span class="chips">${chips}</span>
                <span class="card-actions">
                    <button class="mini" data-action="edit-summary" title="Edit this summary in place">Edit</button>
                    <label class="exclude-toggle" title="Excluded summaries stay here but are left out of the Word export">
                        <input type="checkbox" data-action="toggle-exclude" ${item.excluded ? "checked" : ""}> Exclude
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
                <input class="sum-title" aria-label="Summary title">
                <input class="sum-date" aria-label="Summary date">
            </div>
            <div class="meta">${meta}</div>
            <textarea class="sum-text" aria-label="Summary text"></textarea>
            <div class="edit-actions">
                <button class="primary" data-action="save-summary">Save</button>
                <button class="ghost" data-action="cancel-edit">Cancel</button>
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
    const html = pageCount > 1 ? `
        <button class="mini" data-page="prev" ${S.summaryPage === 0 ? "disabled" : ""}>Prev</button>
        <span>Page ${S.summaryPage + 1} of ${pageCount}</span>
        <button class="mini" data-page="next" ${S.summaryPage >= pageCount - 1 ? "disabled" : ""}>Next</button>` : "";
    ["summaryPager", "summaryPagerBottom"].forEach((id) => {
        $(id).innerHTML = html;
        $(id).classList.toggle("hidden", pageCount <= 1);
    });
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
        empty.innerHTML = "<p>This document has not been summarized yet.</p>";
        const btn = document.createElement("button");
        btn.className = "primary";
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

["summaryPager", "summaryPagerBottom"].forEach((id) => {
    $(id).addEventListener("click", (event) => {
        const direction = event.target.dataset.page;
        if (!direction) return;
        S.summaryPage += direction === "next" ? 1 : -1;
        S.editingSummary = -1;
        renderSummaries();
        $("step-summaries").scrollTop = 0;
    });
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

$("exportBtn").addEventListener("click", async () => {
    banner("");
    $("exportBtn").disabled = true;
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
    } catch (err) {
        banner(err.message);
    } finally {
        $("exportBtn").disabled = false;
    }
});

boot();
