import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host");
  const protocol = requestHeaders.get("x-forwarded-proto") ?? "https";
  const metadataBase = host
    ? new URL(`${protocol}://${host}`)
    : new URL("http://localhost:3000");

  return {
    metadataBase,
    title: "Lians | Make Every AI Decision Answerable",
    description:
      "The system of record and control for consequential AI decisions: memory, authority, verifiable evidence, and change impact across every model and agent.",
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
    openGraph: {
      title: "Make every AI decision answerable.",
      description:
        "Know what AI knew, why it acted, who authorized it, and what changed next—with one portable decision record.",
      type: "website",
      images: [
        {
          url: "/og.png",
          width: 1731,
          height: 909,
          alt: "Lians makes every AI decision answerable with memory, authority, evidence, and impact intelligence",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "Lians | Make Every AI Decision Answerable",
      description:
        "The system of record and control for consequential AI decisions.",
      images: ["/og.png"],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
