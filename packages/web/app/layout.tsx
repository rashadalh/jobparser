import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Resume Job Matcher",
  description: "Upload your resume and see only the jobs you actually qualify for, with the evidence behind every match.",
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
