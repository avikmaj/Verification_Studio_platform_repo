import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "UVM Verification Studio",
  description:
    "Regression, coverage and execution dashboard for UVM Verification Studio",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
