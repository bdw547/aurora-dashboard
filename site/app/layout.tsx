import type { Metadata } from "next";
import "./globals.css";

const siteDescription =
  "A showcase for Aurora's Home Assistant panel integration, no-code web configurator, emulator, component library, and end-user benefits.";

export const metadata: Metadata = {
  title: "Aurora Dashboard Showcase",
  description: siteDescription,
  openGraph: {
    title: "Aurora Dashboard Showcase",
    description: siteDescription,
    images: ["/og.png"],
  },
  twitter: {
    card: "summary_large_image",
    title: "Aurora Dashboard Showcase",
    description: siteDescription,
    images: ["/og.png"],
  },
};

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
