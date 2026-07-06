/* Document-scoped review flow: boot from the document's persisted state -> segment
   (poll) -> edit rows beside the PDF (autosaved) -> summarize (poll) -> export.
   Vanilla JS on purpose: no build step, nothing to break the day of a demo. */
"use strict";

const DOC_ID = document.body.dataset.docId;

const S = {
    rows: [],          // {start, end, category, title, date, injury_date, flag, suggest_merge}
    categories: [],
    totalPages: 0,
    selected: -1,
    lastViewerPage: 0,
    splitting: -1,     // row index currently showing the inline split form
    saveTimer: null,
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
    const order = ["identify", "review", "summaries"];
    document.querySelectorAll(".steps li").forEach((li) => {
        const idx = order.indexOf(li.dataset.step);
        li.classList.toggle("active", li.dataset.step === active);
        li.classList.toggle("done", idx < order.indexOf(active));
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
        show("step-start");
        $("startSegment").disabled = true;
        return;
    }
    S.totalPages = detail.page_count;
    S.categories = detail.categories || [];
    S.rows = detail.rows || [];

    const job = detail.active_job;
    if (job && job.kind === "segment") return watchSegment();
    if (job && job.kind === "summarize") return watchSummarize();
    if (detail.status === "done") return loadSummaries();
    if (detail.status === "error") banner("The last run failed - you can start again.");
    if (detail.status === "interrupted") banner("The last run was interrupted - start again.");
    if (S.rows.length) return enterEditor();
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
    try {
        await pollDocument("Identifying documents", "identify");
        const detail = await api("");
        S.rows = detail.rows || [];
        enterEditor();
    } catch (err) {
        banner(err.message);
        show("step-start");
    }
}

async function watchSummarize() {
    try {
        await pollDocument("Summarizing documents", "summaries");
        await loadSummaries();
    } catch (err) {
        banner(err.message);
        if (S.rows.length) enterEditor(); else show("step-start");
    }
}

$("startSegment").addEventListener("click", async () => {
    banner("");
    $("startSegment").disabled = true;
    try {
        await api("/segment/start", { method: "POST", json: {} });
        await watchSegment();
    } catch (err) {
        banner(err.message);
        show("step-start");
    } finally {
        $("startSegment").disabled = false;
    }
});

/* ---------- editor ---------- */

function enterEditor() {
    show("step-editor");
    $("pdfFrame").src = `/api/documents/${DOC_ID}/pdf#page=1`;
    S.lastViewerPage = 1;
    renderTable();
}

function jumpTo(page) {
    if (page === S.lastViewerPage) return;
    S.lastViewerPage = page;
    $("pdfFrame").src = `/api/documents/${DOC_ID}/pdf#page=${page}`;
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
            gap.innerHTML = `<td colspan="7">pages ${previousEnd + 1}-${Number(row.start) - 1} not included (skipped at summarization)</td>`;
            body.appendChild(gap);
        }
        previousEnd = Math.max(previousEnd, Number(row.end) || previousEnd);

        const tr = document.createElement("tr");
        tr.className = "doc-row" + (errors.has(i) ? " invalid" : "") + (S.selected === i ? " selected" : "");
        tr.dataset.idx = i;
        tr.innerHTML = `
            <td>${i + 1}</td>
            <td><input type="number" data-field="start" value="${row.start}" min="1" max="${S.totalPages}"></td>
            <td><input type="number" data-field="end" value="${row.end}" min="1" max="${S.totalPages}"></td>
            <td><select data-field="category">${categoryOptions(row.category)}</select></td>
            <td><input type="text" data-field="date" value="${row.date || "-"}"></td>
            <td style="text-align:center"><input type="checkbox" data-field="flag" ${String(row.flag).toLowerCase() === "x" ? "checked" : ""}></td>
            <td class="row-actions">
                ${S.splitting === i ? `at page <input type="number" class="split-page" min="${Number(row.start) + 1}" max="${row.end}" value="${Number(row.start) + 1}" aria-label="First page of the second document">
                <button class="mini" data-action="split-confirm">Split</button>
                <button class="mini" data-action="split-cancel">Cancel</button>` : `
                ${row.suggest_merge && i > 0 ? '<button class="mini suggest" data-action="merge" title="The AI double-checked this boundary and believes it continues the document above">Likely same doc - merge?</button>' : ""}
                ${i > 0 ? '<button class="mini" data-action="merge" title="Merge into the document above">Merge up</button>' : ""}
                ${Number(row.end) > Number(row.start) ? '<button class="mini" data-action="split" title="Split this document into two">Split</button>' : ""}
                <button class="mini" data-action="delete" title="Remove this row">Delete</button>`}
            </td>`;
        const title = row.title && row.title !== "-" ? row.title : "";
        if (title) {
            const meta = document.createElement("tr");
            meta.className = "doc-row" + (S.selected === i ? " selected" : "");
            meta.dataset.idx = i;
            meta.innerHTML = `<td></td><td colspan="6" class="row-title">${title.replaceAll("<", "&lt;")}</td>`;
            body.appendChild(tr);
            body.appendChild(meta);
        } else {
            body.appendChild(tr);
        }
    });

    const suggested = S.rows.filter((r, i) => r.suggest_merge && i > 0).length;
    const bulk = $("applySuggestions");
    bulk.classList.toggle("hidden", suggested === 0);
    bulk.textContent = `Apply ${suggested} suggested merge${suggested === 1 ? "" : "s"}`;
    $("docCount").textContent = `${S.rows.length} documents / ${S.totalPages} pages`;
    const firstError = errors.size ? `row ${[...errors.keys()][0] + 1}: ${[...errors.values()][0]}` : "";
    $("validationMsg").textContent = firstError;
    $("summarizeBtn").disabled = errors.size > 0 || S.rows.length === 0;
}

$("rowsBody").addEventListener("change", (event) => {
    const tr = event.target.closest("tr[data-idx]");
    if (!tr) return;
    const row = S.rows[Number(tr.dataset.idx)];
    const field = event.target.dataset.field;
    if (!field) return;
    if (field === "flag") row.flag = event.target.checked ? "x" : "-";
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

/* Add a missed document anywhere: the user types the page range and the row sorts
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
        date: "-", injury_date: "-", flag: "x",
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

/* ---------- summarize + export ---------- */

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

async function loadSummaries() {
    const summaries = await api("/summaries");
    renderSummaries(summaries);
}

function renderSummaries(summaries) {
    const list = $("summaryList");
    list.innerHTML = "";
    summaries.forEach((item) => {
        const card = document.createElement("div");
        card.className = "summary-card";
        const flagged = item.manualCheck
            ? '<span class="flagged">needs review</span> - ' : "";
        card.innerHTML = `
            <h3></h3>
            <div class="meta">${flagged}${item.summaryDate || "no date"} -
                pages ${item.row.start}-${item.row.end} -
                ${CATEGORY_LABELS[String(item.row.category)] || item.row.category}</div>
            <p class="body"></p>`;
        card.querySelector("h3").textContent = item.summaryTitle;
        card.querySelector("p.body").textContent = item.summaryText;
        list.appendChild(card);
    });
    $("summaryCount").textContent = `${summaries.length} summaries`;
    show("step-summaries");
}

$("backToEditor").addEventListener("click", () => {
    if (S.rows.length) enterEditor();
    else show("step-start");
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
