import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

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
      "Connect Lians to your AI and open any decision to see what happened, why it happened, who approved it, and what needs attention now.",
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
    openGraph: {
      title: "Make every AI decision answerable.",
      description:
        "See what happened, why it happened, who approved it, and what needs attention next.",
      type: "website",
      images: [
        {
          url: "/og.png",
          width: 1536,
          height: 1024,
          alt: "Lians makes every AI decision answerable",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "Lians | Make Every AI Decision Answerable",
      description:
        "See what happened. Know what to do next.",
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
      <body>{children}</body>
    </html>
  );
}
