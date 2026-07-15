export default function Home() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "var(--gutter)",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <h1 style={{ font: "600 28px/1.2 var(--font-heading), sans-serif", color: "var(--navy-600)" }}>
          MRR AI
        </h1>
        <p style={{ color: "var(--color-text-muted)" }}>
          Medical Record Review - Next.js + FastAPI (re-platform scaffold)
        </p>
      </div>
    </main>
  );
}
