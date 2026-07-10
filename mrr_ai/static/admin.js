/* Admin: category + prompt CRUD over /api/admin. Vanilla JS, no build step. The page is
   already admin-gated server-side; this just drives the table and the two dialogs. */
"use strict";

const $ = (id) => document.getElementById(id);

function cookie(name) {
    const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : "";
}

async function api(url, options = {}) {
    const opts = { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } };
    if (opts.method && opts.method !== "GET") opts.headers["X-XSRF-Token"] = cookie("XSRF-TOKEN");
    if (opts.body && typeof opts.body !== "string") {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(opts.body);
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

function banner(message) {
    $("adminMsg").textContent = message || "";
}

function badge(label, tone) {
    return `<span class="hd-badge hd-badge-${tone}"><span class="hd-dot"></span>${label}</span>`;
}

let categories = [];

async function loadCategories() {
    try {
        categories = await api("/api/admin/categories");
        render();
    } catch (err) {
        banner(err.message);
    }
}

function render() {
    const body = $("adminCategories");
    body.innerHTML = "";
    categories.forEach((cat) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td class="hd-muted">${cat.id}</td>
            <td><span class="hd-name"></span></td>
            <td class="hd-muted">${cat.auto_assign ? "Yes" : "No"}</td>
            <td>${badge(cat.active ? "Active" : "Inactive", cat.active ? "success" : "neutral")}</td>
            <td>${badge(cat.has_summary_prompt ? "Custom" : "General", cat.has_summary_prompt ? "info" : "neutral")}</td>
            <td class="hd-menu-cell hd-admin-actions">
                <button class="ev-btn ev-btn-outline ev-btn-sm" data-edit="${cat.id}">Edit</button>
                <button class="ev-btn ev-btn-outline ev-btn-sm" data-prompt="${cat.id}">Prompt</button>
                <button class="ev-btn ev-btn-ghost ev-btn-sm" data-toggle="${cat.id}">${cat.active ? "Deactivate" : "Activate"}</button>
            </td>`;
        tr.querySelector(".hd-name").textContent = cat.name; // avoid HTML injection in names
        body.appendChild(tr);
    });
}

/* ---------- category dialog ---------- */

function showDialog(id, show) {
    $(id).classList.toggle("hidden", !show);
}

function openCategoryDialog(cat) {
    $("categoryError").textContent = "";
    const creating = !cat;
    $("categoryDialogTitle").textContent = creating ? "Add category" : `Edit category ${cat.id}`;
    $("catId").value = creating ? "" : cat.id;
    $("catId").disabled = !creating; // ids are immutable
    $("catName").value = creating ? "" : cat.name;
    $("catDescription").value = creating ? "" : cat.description || "";
    $("catExamples").value = creating ? "" : (cat.examples || []).join("\n");
    $("catAutoAssign").checked = creating ? true : cat.auto_assign;
    $("catActive").checked = creating ? true : cat.active;
    $("categorySave").dataset.mode = creating ? "create" : cat.id;
    showDialog("categoryDialog", true);
}

async function saveCategory() {
    const mode = $("categorySave").dataset.mode;
    const examples = $("catExamples")
        .value.split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
    const payload = {
        name: $("catName").value.trim(),
        description: $("catDescription").value.trim(),
        examples,
        auto_assign: $("catAutoAssign").checked,
        active: $("catActive").checked,
    };
    try {
        if (mode === "create") {
            payload.id = $("catId").value.trim();
            await api("/api/admin/categories", { method: "POST", body: payload });
        } else {
            await api(`/api/admin/categories/${mode}`, { method: "PATCH", body: payload });
        }
        showDialog("categoryDialog", false);
        await loadCategories();
    } catch (err) {
        $("categoryError").textContent = err.message;
    }
}

async function toggleActive(id) {
    const cat = categories.find((c) => c.id === id);
    if (!cat) return;
    try {
        await api(`/api/admin/categories/${id}`, { method: "PATCH", body: { active: !cat.active } });
        await loadCategories();
    } catch (err) {
        banner(err.message);
    }
}

/* ---------- prompt dialog ---------- */

async function openPromptDialog(id) {
    $("promptError").textContent = "";
    const cat = categories.find((c) => c.id === id);
    try {
        const data = await api(`/api/admin/prompts/${id}`);
        $("promptDialogTitle").textContent = `Summary prompt - ${cat ? cat.name : id}`;
        $("promptDialogSub").textContent = data.custom
            ? "This category has a custom summary prompt."
            : "Inheriting the general prompt; saving creates a custom one for this category.";
        $("promptText").value = data.text || data.effective_text || "";
        $("promptSave").dataset.id = id;
        showDialog("promptDialog", true);
    } catch (err) {
        banner(err.message);
    }
}

async function savePrompt() {
    const id = $("promptSave").dataset.id;
    try {
        await api(`/api/admin/prompts/${id}`, {
            method: "PUT",
            body: { text: $("promptText").value },
        });
        showDialog("promptDialog", false);
        await loadCategories();
    } catch (err) {
        $("promptError").textContent = err.message;
    }
}

/* ---------- wiring ---------- */

$("adminCategories").addEventListener("click", (event) => {
    const target = event.target.closest("[data-edit], [data-prompt], [data-toggle]");
    if (!target) return;
    if (target.dataset.edit) openCategoryDialog(categories.find((c) => c.id === target.dataset.edit));
    else if (target.dataset.prompt) openPromptDialog(target.dataset.prompt);
    else if (target.dataset.toggle) toggleActive(target.dataset.toggle);
});

$("addCategoryBtn").addEventListener("click", () => openCategoryDialog(null));
$("categorySave").addEventListener("click", saveCategory);
$("categoryCancel").addEventListener("click", () => showDialog("categoryDialog", false));
$("categoryDialogClose").addEventListener("click", () => showDialog("categoryDialog", false));
$("promptSave").addEventListener("click", savePrompt);
$("promptCancel").addEventListener("click", () => showDialog("promptDialog", false));
$("promptDialogClose").addEventListener("click", () => showDialog("promptDialog", false));

loadCategories();
