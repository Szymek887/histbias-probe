// Run wizard: "all" checkboxes per topic group. No build step, no dependencies.
document.addEventListener("change", (e) => {
  const all = e.target.closest("[data-check-all]");
  if (!all) return;
  document.querySelectorAll(`[data-group="${all.dataset.checkAll}"]`).forEach((box) => {
    if (!box.disabled) box.checked = all.checked;
  });
  // Setting `checked` fires no event: refresh the summary with the new selection.
  // (hx-sync="this:replace" drops the request htmx already sent with the old values.)
  all.form?.dispatchEvent(new Event("change", { bubbles: true }));
});
