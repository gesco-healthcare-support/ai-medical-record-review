import type { NextConfig } from "next";

// Same-origin model: in dev, Next proxies API calls to the FastAPI backend so the browser
// sees one origin (no CORS, cookie session works). In prod a reverse proxy does the same.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },
};

export default nextConfig;
