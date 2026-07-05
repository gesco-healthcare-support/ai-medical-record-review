/* Review flow: upload -> segment (poll) -> edit rows beside the PDF -> summarize (poll).
   Vanilla JS on purpose: no build step, nothing to break the day of a demo. */
"use strict";

const S = {
    rows: [],          // {start, end, category, title, date, injury_date, flag}
    categories: [],
    totalPages: 0,
    selected: -1,
    lastViewerPage: 0,
};

const $ = (id) => document.getElementById(id);
const sections = ["step-upload", "step-progress", "step-editor", "step-summaries"];

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

function show(section) {
    sections.forEach((id) => $(id).classList.toggle("hidden", id !== section));
    const stepOf = {
        "step-upload": "upload", "step-progress": "segment",
        "step-editor": "review", "step-summaries": "summaries",
    };
    const active = stepOf[section];
    const order = ["upload", "segment", "review", "summaries"];
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

async function api(url, options) {
    const resp = await fetch(url, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `${url} failed (${resp.status})`);
    return data;
}

/* ---------- polling ---------- */

const STAGE_LABELS = {
    starting: "Starting...",
    segmenting: "Reading the record and finding document boundaries",
    categorizing: "Categorizing each document",
    verifying: "Double-checking uncertain boundaries",
    summarizing: "Writing summaries",
};

function pollJob(statusUrl, title) {
    $("progressTitle").textContent = title;
    $("barFill").style.width = "0%";
    show("step-progress");
    return new Promise((resolve, reject) => {
        const timer = setInterval(async () => {
            let snap;
            try {
                snap = await api(statusUrl);
            } catch (err) {
                clearInterval(timer);
                return reject(err);
            }
            const pct = snap.total ? Math.round((100 * snap.current) / snap.total) : 5;
            $("barFill").style.width = `${Math.max(pct, 4)}%`;
            const label = STAGE_LABELS[snap.stage] || snap.stage || "Working";
            $("progressDetail").textContent =
                snap.total ? `${label} (${snap.current}/${snap.total})` : label;
            if (snap.state === "done") { clearInterval(timer); resolve(snap); }
            if (snap.state === "error") { clearInterval(timer); reject(new Error(snap.error)); }
        }, 1000);
    });
}

/* ---------- upload + segment ---------- */

$("pdfInput").addEventListener("change", () => {
    const f = $("pdfInput").files[0];
    $("fileLabel").textContent = f ? f.name : "Choose a PDF...";
});

$("uploadForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    banner("");
    const file = $("pdfInput").files[0];
    if (!file) return;
    $("uploadBtn").disabled = true;
    try {
        const form = new FormData();
        form.append("pdf", file);
        const up = await fetch("/upload", { method: "POST", body: form });
        if (!up.ok) throw new Error(`upload failed (${up.status})`);
        const meta = await up.json();
        S.totalPages = meta.num_pages;

        const start = await api("/api/segment/start", { method: "POST" });
        S.totalPages = start.total_pages || S.totalPages;
        const snap = await pollJob("/api/segment/status", "Identifying documents");
        S.rows = snap.rows;
        S.categories = snap.categories;
        enterEditor();
    } catch (err) {
        banner(err.message);
        show("step-upload");
    } finally {
        $("uploadBtn").disabled = false;
    }
});

/* ---------- editor ---------- */

function enterEditor() {
    show("step-editor");
    $("pdfFrame").src = "/api/pdf#page=1";
    S.lastViewerPage = 1;
    renderTable();
}

function jumpTo(page) {
    if (page === S.lastViewerPage) return;
    S.lastViewerPage = page;
    $("pdfFrame").src = `/api/pdf#page=${page}`;
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
                ${row.suggest_merge && i > 0 ? '<button class="mini suggest" data-action="merge" title="The AI double-checked this boundary and believes it continues the document above">Likely same doc - merge?</button>' : ""}
                ${i > 0 ? '<button class="mini" data-action="merge" title="Merge into the document above">Merge up</button>' : ""}
                <button class="mini" data-action="delete" title="Remove this row">Delete</button>
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
});

$("rowsBody").addEventListener("click", (event) => {
    const tr = event.target.closest("tr[data-idx]");
    if (!tr) return;
    const idx = Number(tr.dataset.idx);
    const action = event.target.dataset.action;
    if (action === "delete") {
        S.rows.splice(idx, 1);
        S.selected = -1;
        renderTable();
        return;
    }
    if (action === "merge") {
        // The row above absorbs this one's pages; its metadata (title/category/date) wins.
        S.rows[idx - 1].end = Math.max(S.rows[idx - 1].end, S.rows[idx].end);
        S.rows[idx - 1].flag =
            [S.rows[idx - 1].flag, S.rows[idx].flag].includes("x") ? "x" : "-";
        S.rows.splice(idx, 1);
        S.selected = idx - 1;
        renderTable();
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
    renderTable();
});

$("addRow").addEventListener("click", () => {
    const last = S.rows[S.rows.length - 1];
    const start = last ? Math.min(Number(last.end) + 1, S.totalPages) : 1;
    S.rows.push({
        start, end: Math.min(start, S.totalPages), category: "100", title: "(added manually)",
        date: "-", injury_date: "-", flag: "x",
    });
    S.selected = S.rows.length - 1;
    renderTable();
});

/* ---------- summarize + export ---------- */

$("summarizeBtn").addEventListener("click", async () => {
    banner("");
    try {
        await api("/api/summarize/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rows: S.rows }),
        });
        const snap = await pollJob("/api/summarize/status", "Summarizing documents");
        renderSummaries(snap.summaries);
    } catch (err) {
        banner(err.message);
        show("step-editor");
    }
});

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

$("exportBtn").addEventListener("click", async () => {
    banner("");
    $("exportBtn").disabled = true;
    try {
        const resp = await fetch("/exportresultstoword", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
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

$("startOver").addEventListener("click", async () => {
    await fetch("/reset", { method: "POST" });
    location.reload();
});

show("step-upload");
