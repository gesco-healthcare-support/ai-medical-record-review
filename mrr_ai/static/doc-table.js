/* Shared "documents table": status badges, filter chips, filename search, sortable
   headers, pagination, and a per-row overflow menu (Open / identify / delete). Used by
   My documents (home.js) and the category-bundle pickers (bundle-page.js) so the two
   render the identical table - the ONLY difference is what opening a row does, supplied
   as config.onOpen. Vanilla JS, no build step.

   Expects these element ids to exist on the page (see templates/_doc_table.html):
   filterChips, searchInput, a .hd-table with thead[data-sort] + tbody#docsBody,
   pageInfo, prevPage, nextPage. */
"use strict";

window.DocTable = (function () {
    const PAGE_SIZE = 20;

    const STATUS_LABELS = {
        uploaded: "Uploaded",
        segmenting: "Identifying documents",
        reviewing: "Ready for review",
        summarizing: "Summarizing",
        done: "Summarized",
        error: "Failed",
        interrupted: "Interrupted",
    };
    const STATUS_TONES = {
        uploaded: "neutral",
        segmenting: "info",
        reviewing: "warning",
        summarizing: "info",
        done: "success",
        error: "danger",
        interrupted: "danger",
    };
    const FILTERS = [
        { key: "all", label: "All", match: () => true },
        { key: "reviewing", label: "Ready for review", match: (d) => d.status === "reviewing" },
        { key: "running", label: "Running", match: (d) => Boolean(d.active_job) },
        { key: "done", label: "Summarized", match: (d) => d.status === "done" },
        { key: "uploaded", label: "Uploaded", match: (d) => d.status === "uploaded" },
        {
            key: "failed",
            label: "Failed",
            match: (d) => d.status === "error" || d.status === "interrupted",
        },
    ];
    const SORT_ACCESSORS = {
        name: (d) => (d.original_filename || "").toLowerCase(),
        pages: (d) => d.page_count || 0,
        uploaded: (d) => d.created_at || "",
        found: (d) => d.rows_count || 0,
        activity: (d) => d.updated_at || "",
    };

    const FILE_ICON =
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"></path><path d="M15 2v5h5"></path></svg>';
    const SORT_ARROW = {
        "-1": '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"></path><path d="m19 12-7 7-7-7"></path></svg>',
        "1": '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"></path><path d="m5 12 7-7 7 7"></path></svg>',
    };

    function cookieValue(name) {
        const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
        return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
    }

    async function api(url, options = {}) {
        const opts = {
            ...options,
            headers: { Accept: "application/json", ...(options.headers || {}) },
        };
        if (opts.method && opts.method !== "GET") {
            opts.headers["X-XSRF-Token"] = cookieValue("XSRF-TOKEN");
        }
        const resp = await fetch(url, opts);
        if (resp.status === 401) {
            window.location = "/login";
            throw new Error("signed out");
        }
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.error || `${url} failed (${resp.status})`);
        return data;
    }

    function relTime(iso) {
        if (!iso) return "—";
        const then = new Date(iso);
        const mins = Math.round((Date.now() - then.getTime()) / 60000);
        if (mins < 1) return "Just now";
        if (mins < 60) return `${mins}m ago`;
        const hours = Math.round(mins / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.round(hours / 24);
        if (days === 1) return "Yesterday";
        if (days < 7) return `${days}d ago`;
        const opts = { month: "short", day: "numeric" };
        if (then.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
        return then.toLocaleDateString(undefined, opts);
    }

    function badge(doc) {
        const job = doc.active_job;
        const progress = job && job.total ? ` (${job.current}/${job.total})` : "";
        const label = (STATUS_LABELS[doc.status] || doc.status) + progress;
        const tone = STATUS_TONES[doc.status] || "neutral";
        return `<span class="hd-badge hd-badge-${tone}"><span class="hd-dot"></span>${label}</span>`;
    }

    function menuHtml(doc, allowDelete) {
        const identifyLabel = doc.rows_count ? "Re-run identification" : "Start identification";
        const identifyDisabled = doc.active_job ? " disabled" : "";
        // Bundle pickers pass allowDelete:false - deleting the underlying document from a
        // "pick a document to bundle" context is surprising and destructive; deletion lives
        // on My documents instead.
        const deleteItem = allowDelete
            ? `
                <div class="hd-menu-divider"></div>
                <button type="button" class="danger" data-action="delete">Delete…</button>`
            : "";
        return `
            <div class="hd-menu" data-menu>
                <button type="button" data-action="open">Open</button>
                <button type="button" data-action="identify"${identifyDisabled}>${identifyLabel}</button>${deleteItem}
            </div>`;
    }

    function create(config) {
        const onOpen = config.onOpen;
        const onLoaded = config.onLoaded || (() => {});
        const allowDelete = config.allowDelete !== false; // default true (My documents)
        const el = (id) => document.getElementById(id);
        const S = {
            docs: [],
            loaded: false,
            filter: "all",
            search: "",
            sortKey: "uploaded",
            sortDir: -1,
            page: 0,
            menuFor: null,
        };
        let pollTimer = null;

        function visibleDocs() {
            const filter = FILTERS.find((f) => f.key === S.filter) || FILTERS[0];
            const query = S.search.trim().toLowerCase();
            const docs = S.docs
                .filter(filter.match)
                .filter((d) => !query || (d.original_filename || "").toLowerCase().includes(query));
            const accessor = SORT_ACCESSORS[S.sortKey] || SORT_ACCESSORS.uploaded;
            docs.sort((a, b) => {
                const [va, vb] = [accessor(a), accessor(b)];
                return (va < vb ? -1 : va > vb ? 1 : 0) * S.sortDir;
            });
            return docs;
        }

        function renderChips() {
            el("filterChips").innerHTML = FILTERS.map((f) => {
                const count = S.docs.filter(f.match).length;
                const active = S.filter === f.key ? " active" : "";
                return `<button type="button" class="hd-chip${active}" data-filter="${f.key}">${f.label} · ${count}</button>`;
            }).join("");
        }

        function renderHead() {
            document.querySelectorAll(".hd-table thead th[data-sort]").forEach((th) => {
                const active = th.dataset.sort === S.sortKey;
                th.classList.toggle("sorted", active);
                const label = th.textContent.trim();
                th.innerHTML = active
                    ? `<span class="hd-sortlabel">${label} ${SORT_ARROW[String(S.sortDir)]}</span>`
                    : label;
            });
        }

        function renderTable() {
            const docs = visibleDocs();
            const pageCount = Math.max(1, Math.ceil(docs.length / PAGE_SIZE));
            S.page = Math.min(S.page, pageCount - 1);
            const start = S.page * PAGE_SIZE;
            const pageDocs = docs.slice(start, start + PAGE_SIZE);

            const body = el("docsBody");
            body.innerHTML = "";
            if (!pageDocs.length) {
                body.innerHTML =
                    '<tr class="hd-norows"><td colspan="7">No documents match this view.</td></tr>';
            }
            pageDocs.forEach((doc) => {
                const tr = document.createElement("tr");
                tr.dataset.id = doc.id;
                const menuOpen = S.menuFor === doc.id;
                tr.innerHTML = `
                    <td><span class="hd-doc">${FILE_ICON}<span class="hd-name"></span></span></td>
                    <td class="hd-muted">${doc.page_count}</td>
                    <td class="hd-muted">${new Date(doc.created_at).toLocaleDateString()}</td>
                    <td class="hd-muted">${doc.rows_count || "—"}</td>
                    <td class="hd-muted">${relTime(doc.updated_at)}</td>
                    <td>${badge(doc)}</td>
                    <td class="hd-menu-cell">
                        <button type="button" class="hd-dots${menuOpen ? " open" : ""}" data-dots
                                aria-label="Actions" aria-expanded="${menuOpen}">⋯</button>
                        ${menuOpen ? menuHtml(doc, allowDelete) : ""}
                    </td>`;
                tr.querySelector(".hd-name").textContent = doc.original_filename; // PHI-safe render
                body.appendChild(tr);
            });

            el("pageInfo").textContent = docs.length
                ? `${start + 1}–${Math.min(start + PAGE_SIZE, docs.length)} of ${docs.length}`
                : "0 of 0";
            el("prevPage").disabled = S.page === 0;
            el("nextPage").disabled = S.page >= pageCount - 1;
        }

        function render() {
            if (!S.loaded) return;
            onLoaded(S.docs);
            renderChips();
            renderHead();
            renderTable();
        }

        async function refresh() {
            let docs;
            try {
                docs = await api("/api/documents");
            } catch (err) {
                if (config.onError) config.onError(err);
                return;
            }
            S.docs = docs;
            S.loaded = true;
            render();
            clearTimeout(pollTimer);
            if (docs.some((doc) => doc.active_job)) pollTimer = setTimeout(refresh, 2000);
        }

        async function runAction(action, id, doc) {
            if (action === "open") {
                onOpen(id, doc);
                return;
            }
            if (action === "identify") {
                const rerun = Boolean(doc && doc.rows_count);
                if (
                    rerun &&
                    !window.confirm(
                        "Re-running identification replaces the current document list AND your " +
                            "corrections. Continue?",
                    )
                ) {
                    return;
                }
                S.menuFor = null;
                try {
                    await api(`/api/documents/${id}/segment/start`, { method: "POST" });
                } catch (err) {
                    if (config.onError) config.onError(err);
                }
                refresh();
                return;
            }
            if (action === "delete") {
                if (!allowDelete) return; // menu never renders Delete when disabled; guard anyway
                if (!window.confirm("Delete this document and all of its rows and summaries?")) {
                    return;
                }
                S.menuFor = null;
                try {
                    await api(`/api/documents/${id}`, { method: "DELETE" });
                } catch (err) {
                    if (config.onError) config.onError(err);
                }
                refresh();
                return;
            }
        }

        el("filterChips").addEventListener("click", (event) => {
            const chip = event.target.closest("[data-filter]");
            if (!chip) return;
            S.filter = chip.dataset.filter;
            S.page = 0;
            S.menuFor = null;
            render();
        });
        el("searchInput").addEventListener("input", () => {
            S.search = el("searchInput").value;
            S.page = 0;
            S.menuFor = null;
            renderTable();
        });
        document.querySelector(".hd-table thead").addEventListener("click", (event) => {
            const th = event.target.closest("th[data-sort]");
            if (!th) return;
            const key = th.dataset.sort;
            if (S.sortKey === key) S.sortDir *= -1;
            else {
                S.sortKey = key;
                S.sortDir = key === "name" ? 1 : -1;
            }
            S.page = 0;
            renderHead();
            renderTable();
        });
        el("prevPage").addEventListener("click", () => {
            S.page -= 1;
            S.menuFor = null;
            renderTable();
        });
        el("nextPage").addEventListener("click", () => {
            S.page += 1;
            S.menuFor = null;
            renderTable();
        });

        el("docsBody").addEventListener("click", (event) => {
            const tr = event.target.closest("tr[data-id]");
            if (!tr) return;
            const id = tr.dataset.id;
            const doc = S.docs.find((d) => d.id === id);
            if (event.target.closest("[data-dots]")) {
                S.menuFor = S.menuFor === id ? null : id;
                renderTable();
                return;
            }
            const action = event.target.dataset.action;
            if (action) {
                runAction(action, id, doc);
                return;
            }
            if (event.target.closest("[data-menu]")) return; // other clicks inside the menu
            onOpen(id, doc);
        });

        document.addEventListener("click", (event) => {
            if (S.menuFor && !event.target.closest(".hd-menu-cell")) {
                S.menuFor = null;
                renderTable();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && S.menuFor) {
                S.menuFor = null;
                renderTable();
            }
        });

        return { refresh, getDocs: () => S.docs, state: S };
    }

    return { create, api, cookieValue };
})();
