import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import Header from "@/components/layout/Header";
import Providers from "@/components/layout/Providers";
import { Toaster } from "sonner";

export const metadata: Metadata = {
  title: "India Auto-Trader",
  description: "AI-powered NSE/BSE trading system",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-bg text-text min-h-screen flex">
        <Providers>
          <Sidebar />
          <div className="flex-1 flex flex-col min-w-0 ml-56">
            <Header />
            <main className="flex-1 p-6 overflow-auto">{children}</main>
          </div>
          <Toaster position="bottom-right" theme="dark" richColors />
        </Providers>
      </body>
    </html>
  );
}
