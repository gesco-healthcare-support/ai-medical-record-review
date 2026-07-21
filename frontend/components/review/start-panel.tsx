/** Identify start / re-run panel (DS .center-panel). */
export function StartPanel({
  rerun,
  disabled,
  hint,
  onStart,
}: {
  rerun: boolean;
  disabled?: boolean;
  hint?: string;
  onStart: () => void;
}) {
  const defaultHint = rerun
    ? "Re-running replaces the current document list - including every correction you made - with a fresh AI pass over the record."
    : "The record is split into its component documents and categorized. You review and correct the result before any summaries are written.";
  return (
    <section className="panel center-panel">
      <h1>{rerun ? "Re-run document identification" : "Ready to identify documents"}</h1>
      <p className="muted">{hint || defaultHint}</p>
      <button
        type="button"
        className="ev-btn ev-btn-primary ev-btn-lg"
        disabled={disabled}
        onClick={onStart}
      >
        {rerun ? "Re-run identification" : "Identify documents"}
      </button>
    </section>
  );
}
