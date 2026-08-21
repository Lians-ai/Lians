import type { Metadata } from "next";
import { Anybody, Bricolage_Grotesque, Instrument_Serif } from "next/font/google";
import "./globals.css";

const display = Bricolage_Grotesque({ variable: "--font-display", subsets: ["latin"] });
const wide = Anybody({ variable: "--font-wide", subsets: ["latin"] });
const serif = Instrument_Serif({ variable: "--font-serif", subsets: ["latin"], weight: "400" });

export const metadata: Metadata = {
  title: "Lians | Make",
  description: "Describe what your group needs. Get a working app you can use and share.",
  icons: { icon: "/favicon.svg" },
  openGraph: {
    title: "Lians | Make",
    description: "Describe what your group needs. Get a working app you can use and share.",
    type: "website",
    images: [{ url: "/og.png", width: 1731, height: 909, alt: "MAKE. by lians" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Lians | Make",
    description: "Describe what your group needs. Get a working app you can use and share.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${display.variable} ${wide.variable} ${serif.variable}`}>{children}</body></html>;
}
