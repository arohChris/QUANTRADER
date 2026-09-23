// Guard covers — 3 checkboxes before LIVE / AUTO ON (frontend_spec §6).
export function wireGuard(dialog, checksSel, confirmBtn, cancelBtn, onSubmit) {
  const boxes = () => dialog.querySelectorAll(checksSel);
  const sync = () => {
    const all = [...boxes()].every((c) => c.checked);
    confirmBtn.disabled = !all;
  };
  boxes().forEach((c) => c.addEventListener("change", sync));
  cancelBtn.addEventListener("click", () => {
    boxes().forEach((c) => {
      c.checked = false;
    });
    sync();
    dialog.close();
  });
  confirmBtn.addEventListener("click", async () => {
    confirmBtn.disabled = true;
    await onSubmit();
    boxes().forEach((c) => {
      c.checked = false;
    });
    sync();
    dialog.close();
  });
  dialog.addEventListener("close", () => {
    boxes().forEach((c) => {
      c.checked = false;
    });
    sync();
  });
  sync();
}
