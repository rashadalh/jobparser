import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Resume Job Matcher",
  description: "Resume-driven job matcher — shows only jobs you qualify for, with cited evidence.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
