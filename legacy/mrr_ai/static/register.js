/* Registration page: live password checklist (8+ characters / number / symbol) and
   confirm-match feedback, gating the submit button. Instant feedback ONLY - the
   server enforces the identical rules (MrrPasswordUtil), so disabling JS just moves
   the rejection to the round trip. */
"use strict";

const ICONS = {
    met: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"></path></svg>',
    unmet: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle></svg>',
    failed: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
};

const RULES = {
    length: (value) => value.length >= 8,
    number: (value) => /\d/.test(value),
    symbol: (value) => /[^A-Za-z0-9]/.test(value),
};

const password = document.getElementById("password");
const confirmField = document.getElementById("password_confirm");
const submit = document.getElementById("registerSubmit");
const mismatch = document.getElementById("confirmMismatch");

function update() {
    const value = password.value;
    let allMet = true;
    document.querySelectorAll("#pwChecklist .auth-check").forEach((item) => {
        const met = RULES[item.dataset.rule](value);
        allMet = allMet && met;
        // Unmet rules turn danger once there is input to judge (design 1d).
        const state = met ? "met" : value ? "failed" : "unmet";
        item.classList.remove("met", "failed", "unmet");
        item.classList.add(state);
        item.querySelector(".auth-check-icon").innerHTML = ICONS[state];
    });
    const confirmValue = confirmField ? confirmField.value : "";
    const matches = !confirmField || confirmValue === value;
    if (mismatch) mismatch.classList.toggle("hidden", matches || !confirmValue);
    submit.disabled = !allMet || !matches || (confirmField && !confirmValue);
}

password.addEventListener("input", update);
if (confirmField) confirmField.addEventListener("input", update);
update();
