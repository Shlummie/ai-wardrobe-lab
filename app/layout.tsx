import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Wardrobe Lab — Local outfit intelligence",
  description:
    "Build outfits locally, then measure how a target audience may perceive them.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
