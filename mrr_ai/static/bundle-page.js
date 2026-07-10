/* Category-bundle workspace (Diagnostic & Operative / Depositions). Reuses the shared
   review editor (window.MRR) for identify + correct, then extracts or summarizes just the
   documents whose category is in this page's set. Vanilla JS, no build step. */
"use strict";

const CFG = {
    label: document.body.dataset.bundleLabel,
    slug: document.body.dataset.bundleSlug,
    categories: JSON.parse(document.body.dataset.bundleCategories || "[]"),
};
const CATS = CFG.categories.map(String);

const el = (id) => document.getElementById(id);
let currentDocId = null;

function xsrf() {
    const hit = document.cookie.split("; ").find((c) => c.startsWith("XSRF-TOKEN="));
    return hit ? decodeURIComponent(hit.slice("XSRF-TOKEN=".length)) : "";
}

function pickerMsg(text) {
    el("bundlePickerMsg").textContent = text || "";
}

async function listDocuments() {
    const resp = await fetch("/api/documents", { headers: { Accept: "application/json" } });
    if (resp.status === 401) {
        window.location = "/login";
        return [];
    }
    if (!resp.ok) throw new Error(`could not load your documents (${resp.status})`);
    return resp.json();
}

const STATUS_LABEL = {
    uploaded: "not yet identified",
    segmenting: "identifying...",
    reviewing: "ready to review",
    summarizing: "summarizing...",
    done: "reviewed",
    error: "last run failed",
    interrupted: "interrupted",
};

async function renderDocList() {
    const list = el("bundleDocList");
    list.textContent = "Loading your documents...";
    let documents;
    try {
        documents = await listDocuments();
    } catch (err) {
        list.textContent = "";
        pickerMsg(err.message);
        return;
    }
    list.innerHTML = "";
    if (!documents.length) {
        list.innerHTML = '<p class="muted">No documents yet - upload a record to begin.</p>';
        return;
    }
    documents.forEach((doc) => {
        const row = document.createElement("div");
        row.className = "bundle-docrow";
        const status = STATUS_LABEL[doc.status] || doc.status;
        const label = document.createElement("span");
        // original_filename is the owner's own PHI-bearing name; shown only to them.
        label.textContent = `${doc.original_filename} - ${doc.page_count} pages - ${status}`;
        const open = document.createElement("button");
        open.className = "ev-btn ev-btn-sm ev-btn-outline";
        open.type = "button";
        open.textContent = "Open";
        open.addEventListener("click", () => selectDocument(doc.id));
        row.append(label, open);
        list.appendChild(row);
    });
}

async function uploadDocument() {
    const input = el("bundleUpload");
    const file = input.files && input.files[0];
    if (!file) {
        pickerMsg("Choose a PDF first.");
        return;
    }
    pickerMsg("Uploading...");
    el("bundleUploadBtn").disabled = true;
    try {
        const form = new FormData();
        form.append("pdf", file);
        const resp = await fetch("/api/documents", {
            method: "POST",
            headers: { "X-XSRF-Token": xsrf() },
            body: form,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.error || `upload failed (${resp.status})`);
        pickerMsg("");
        selectDocument(data.id);
    } catch (err) {
        pickerMsg(err.message);
    } finally {
        el("bundleUploadBtn").disabled = false;
    }
}

async function selectDocument(id) {
    currentDocId = id;
    el("bundle-picker").classList.add("hidden");
    // The shared editor's own "Summarize everything" button is meaningless here - this
    // page summarizes only the matching category. Hide it; we provide our own action.
    const editorSummarize = el("summarizeBtn");
    if (editorSummarize) editorSummarize.style.display = "none";
    try {
        await window.MRR.loadDocument(id);
    } catch (err) {
        window.MRR.banner(err.message);
        return;
    }
    // Persisted-reviewed / done documents boot without auto-opening the editor; open it so
    // the reviewer can adjust before bundling. New uploads sit on the Identify panel.
    if (window.MRR.getRows().length) window.MRR.enterEditor();
}

function showActions(rows) {
    const matched = rows.filter((row) => CATS.includes(String(row.category)));
    el("bundle-actions").classList.remove("hidden");
    const n = matched.length;
    el("bundleActionsHint").textContent = n
        ? `${n} ${CFG.label} document${n === 1 ? "" : "s"} found in this record.`
        : `No ${CFG.label} documents in this record yet - fix categories above if that looks wrong.`;
    el("bundleExtractBtn").disabled = n === 0;
    el("bundleSummarizeBtn").disabled = n === 0;
}

async function downloadFromBundle(action, body, fallbackName) {
    el("bundleResultMsg").textContent = "Working...";
    try {
        const resp = await fetch(`/api/documents/${currentDocId}/bundle/${action}`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-XSRF-Token": xsrf() },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.error || `request failed (${resp.status})`);
        }
        const blob = await resp.blob();
        const disposition = resp.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="?([^"]+)"?/);
        const anchor = document.createElement("a");
        anchor.href = URL.createObjectURL(blob);
        anchor.download = match ? match[1] : fallbackName;
        anchor.click();
        URL.revokeObjectURL(anchor.href);
        el("bundleResultMsg").textContent = "Downloaded.";
    } catch (err) {
        el("bundleResultMsg").textContent = err.message;
    }
}

function extract() {
    downloadFromBundle("pdf", { categories: CATS, label: CFG.slug }, `${CFG.slug}.pdf`);
}

function summarize() {
    downloadFromBundle(
        "summarize",
        {
            categories: CATS,
            label: CFG.slug,
            patientName: el("bundlePatient").value,
            patientdob: el("bundleDob").value,
            QMEorAME: el("bundleQme").value,
            lawfirm: el("bundleFirm").value,
        },
        `${CFG.slug}.docx`,
    );
}

el("bundleUploadBtn").addEventListener("click", uploadDocument);
el("bundleExtractBtn").addEventListener("click", extract);
el("bundleSummarizeBtn").addEventListener("click", summarize);
el("bundleBackBtn").addEventListener("click", () => window.location.reload());

// The editor tells us whenever it has rows ready (initial load or after identification).
window.MRR.setOnReviewed(showActions);

renderDocList();
