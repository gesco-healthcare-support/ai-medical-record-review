/* My documents: the shared documents table (see doc-table.js) plus this page's own
   upload (button / browse / drag-drop) and first-run empty state.

   Upload does NOT start identification (decision 2026-07-07): a mis-clicked file must
   never spend model quota. Identification starts from the row menu or the doc's page. */
"use strict";

const $ = (id) => document.getElementById(id);

function banner(message) {
    const el = $("banner");
    el.textContent = message || "";
    el.classList.toggle("hidden", !message);
}

const table = DocTable.create({
    onOpen: (id) => {
        window.location = `/review/${id}`;
    },
    onLoaded: (docs) => {
        const empty = docs.length === 0;
        $("emptyView").classList.toggle("hidden", !empty);
        $("docsView").classList.toggle("hidden", empty);
    },
    onError: (err) => banner(err.message),
});

/* ---------- upload (button, browse, drag-drop) ---------- */

function setUploadBusy(busy) {
    ["uploadBtn", "browseBtn"].forEach((id) => {
        const el = $(id);
        if (el) el.disabled = busy;
    });
    $("uploadBtn").textContent = busy ? "Uploading..." : "Upload a record";
}

async function uploadFile(file) {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
        banner("Only PDF files can be uploaded.");
        return;
    }
    banner("");
    setUploadBusy(true);
    try {
        const form = new FormData();
        form.append("pdf", file);
        const created = await DocTable.api("/api/documents", { method: "POST", body: form });
        if (created.sha256_duplicate) {
            banner("Note: you already uploaded an identical file. Continuing anyway.");
        }
        await table.refresh();
    } catch (err) {
        banner(err.message);
    } finally {
        setUploadBusy(false);
        $("pdfInput").value = "";
    }
}

["uploadBtn", "browseBtn"].forEach((id) => {
    $(id).addEventListener("click", () => $("pdfInput").click());
});
$("pdfInput").addEventListener("change", () => uploadFile($("pdfInput").files[0]));

["dropZone", "tableCard"].forEach((id) => {
    const zone = $(id);
    if (!zone) return;
    zone.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("dragging");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("dragging");
        uploadFile(event.dataTransfer.files[0]);
    });
});
$("dropZone").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") $("pdfInput").click();
});

table.refresh();
