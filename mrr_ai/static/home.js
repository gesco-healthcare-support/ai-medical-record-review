/* Landing page: the user's documents with live status, plus the upload entry point.
   Polls the list only while some job is active - an idle page makes no requests. */
"use strict";

const $ = (id) => document.getElementById(id);

const STATUS_LABELS = {
    uploaded: "Uploaded",
    segmenting: "Identifying documents",
    reviewing: "Ready for review",
    summarizing: "Summarizing",
    done: "Summarized",
    error: "Failed",
    interrupted: "Interrupted",
};

function banner(message) {
    const el = $("banner");
    el.textContent = message || "";
    el.classList.toggle("hidden", !message);
}

function cookieValue(name) {
    const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

async function api(url, options = {}) {
    const opts = { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
    if (opts.method && opts.method !== "GET") {
        opts.headers["X-XSRF-Token"] = cookieValue("XSRF-TOKEN");
    }
    const resp = await fetch(url, opts);
    if (resp.status === 401) { window.location = "/login"; throw new Error("signed out"); }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `${url} failed (${resp.status})`);
    return data;
}

let pollTimer = null;

function renderDocs(docs) {
    const body = $("docsBody");
    body.innerHTML = "";
    if (!docs.length) {
        body.innerHTML = '<tr><td colspan="5" class="muted">No documents yet - upload a PDF to begin.</td></tr>';
        return;
    }
    docs.forEach((doc) => {
        const tr = document.createElement("tr");
        const job = doc.active_job;
        const progress = job && job.total ? ` (${job.current}/${job.total})` : "";
        const uploaded = new Date(doc.created_at).toLocaleDateString();
        tr.innerHTML = `
            <td class="doc-name"></td>
            <td>${doc.page_count}</td>
            <td>${uploaded}</td>
            <td><span class="chip chip-${doc.status}">${STATUS_LABELS[doc.status] || doc.status}${progress}</span></td>
            <td class="row-actions">
                <a class="mini-link" href="/review/${doc.id}">${doc.status === "done" ? "View summaries" : "Open"}</a>
                <button class="mini" data-action="delete" data-id="${doc.id}" title="Delete this document and its results">Delete</button>
            </td>`;
        tr.querySelector(".doc-name").textContent = doc.original_filename;  // PHI-safe render
        body.appendChild(tr);
    });
}

async function refresh() {
    let docs;
    try {
        docs = await api("/api/documents");
    } catch (err) {
        banner(err.message);
        return;
    }
    renderDocs(docs);
    clearTimeout(pollTimer);
    if (docs.some((doc) => doc.active_job)) {
        pollTimer = setTimeout(refresh, 2000);
    }
}

$("docsBody").addEventListener("click", async (event) => {
    if (event.target.dataset.action !== "delete") return;
    const id = event.target.dataset.id;
    if (!window.confirm("Delete this document and all of its rows and summaries?")) return;
    banner("");
    try {
        await api(`/api/documents/${id}`, { method: "DELETE" });
    } catch (err) {
        banner(err.message);
    }
    refresh();
});

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
        const created = await api("/api/documents", { method: "POST", body: form });
        if (created.sha256_duplicate) {
            banner("Note: you already uploaded an identical file. Continuing anyway.");
        }
        await api(`/api/documents/${created.id}/segment/start`, { method: "POST" });
        window.location = `/review/${created.id}`;
    } catch (err) {
        banner(err.message);
        $("uploadBtn").disabled = false;
    }
});

refresh();
