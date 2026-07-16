import type { NextConfig } from "next";

// Same-origin model: in dev, Next proxies API calls to the FastAPI backend so the browser
// sees one origin (no CORS, cookie session works). In prod a reverse proxy does the same.
// Default to 127.0.0.1 (not "localhost") so the Node proxy doesn't resolve to IPv6 ::1 and
// miss a backend bound to IPv4 on Windows.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Lean container runtime: `output: "standalone"` emits .next/standalone with a minimal
  // node server + only the deps it needs (see frontend/Dockerfile).
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
