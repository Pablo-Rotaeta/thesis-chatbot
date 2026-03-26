import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Bilverkstad – Boka tid",
  description: "Boka en tid på bilverkstaden",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="sv">
      <body>{children}</body>
    </html>
  );
}