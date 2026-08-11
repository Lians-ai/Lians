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
    title: "Lians | Decision Evidence Infrastructure for AI",
    description:
      "Record agent evidence, issue signed Decision Receipts, enforce identity-bound runtime policy, and investigate change impact with Lians.",
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
    openGraph: {
      title: "Prove what your AI knew when it acted.",
      description:
        "Universal recording, verifiable Decision Receipts, runtime policy gates, and evidence-led investigations in one control loop.",
      type: "website",
      images: [
        {
          url: "/og-product.png",
          width: 1731,
          height: 909,
          alt: "Lians inspectable memory system connected to verifiable evidence",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "Lians | Decision Evidence Infrastructure",
      description:
        "Verify the recorded evidence boundary, acting authority, and policy basis for an AI action.",
      images: ["/og-product.png"],
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
