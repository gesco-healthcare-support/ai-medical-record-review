/** Job progress panel (DS .center-panel + .bar), shown while identification/summarization runs. */
export function ProgressPanel({
  title,
  pct,
  detail,
}: {
  title: string;
  pct: number;
  detail: string;
}) {
  return (
    <section className="panel center-panel">
      <h1>{title}</h1>
      <div className="bar">
        <div className="bar-fill" style={{ width: `${Math.max(pct, 4)}%` }} />
      </div>
      <p className="muted">{detail}</p>
    </section>
  );
}
