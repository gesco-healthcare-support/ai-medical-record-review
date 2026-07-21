import type { Metadata } from "next";
import { Inter, Poppins } from "next/font/google";
import "./globals.css";
import "./evaluators-ds.css";
import { Providers } from "./providers";
import { Toaster } from "@/components/ui/sonner";

// Evaluators DS type pairing: Poppins headings (600/700), Inter body. Exposed as
// CSS variables consumed by --font-heading / --font-sans in globals.css.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const poppins = Poppins({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-poppins",
  display: "swap",
});

export const metadata: Metadata = {
  title: "MRR AI",
  description: "Medical Record Review",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${poppins.variable}`}>
      <body>
        <Providers>{children}</Providers>
        <Toaster position="bottom-center" />
      </body>
    </html>
  );
}
