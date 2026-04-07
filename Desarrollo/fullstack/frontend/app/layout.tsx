import type { Metadata } from "next";
import { Epilogue, Inter, Space_Grotesk } from "next/font/google";

import { SiteHeader } from "@/components/chrome/SiteHeader";
import "./globals.css";

const display = Epilogue({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "700", "800", "900"],
});

const body = Inter({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["400", "500", "600"],
});

const technical = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-technical",
  weight: ["400", "500", "700"],
});

export const metadata: Metadata = {
  title: "kmp-repair | Editorial Frontend",
  description:
    "Frontend editorial-operativo para explorar casos KMP, ejecutar modos de reparación y validar resultados multi-target.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body className={`${display.variable} ${body.variable} ${technical.variable} antialiased`}>
        <SiteHeader />
        <main className="pb-24 pt-[4.5rem]">{children}</main>
      </body>
    </html>
  );
}
