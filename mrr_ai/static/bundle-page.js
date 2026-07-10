/* Category-bundle workspace (Diagnostic & Operative / Depositions). The document picker
   is the shared table (doc-table.js) - same as My documents; the only difference is that
   opening a row mounts the shared review editor (window.MRR) in place here instead of
   navigating. Once the doc's rows exist, this page extracts or summarizes just the
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

/* The picker: the shared documents table. Opening a row mounts the editor here. */
const table = DocTable.create({
    onOpen: (id) => selectDocument(id),
    onError: (err) => pickerMsg(err.message),
    allowDelete: false, // pickers must not permanently delete the underlying document
});

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
        const created = await DocTable.api("/api/documents", { method: "POST", body: form });
        pickerMsg("");
        selectDocument(created.id);
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
    // Reviewed/done documents boot without auto-opening the editor; open it so the
    // reviewer can adjust before bundling. New uploads sit on the Identify panel.
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

table.refresh();
