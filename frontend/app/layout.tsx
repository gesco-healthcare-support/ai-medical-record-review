import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MRR AI",
  description: "Medical Record Review",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
