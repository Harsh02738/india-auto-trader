"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useState } from "react";

// ── Sidebar context ────────────────────────────────────────────────────────────
const SidebarCtx = createContext({ open: false, toggle: () => {}, close: () => {} });
export const useSidebar = () => useContext(SidebarCtx);

export default function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
  }));
  const [open, setOpen] = useState(false);

  return (
    <SidebarCtx.Provider value={{ open, toggle: () => setOpen(o => !o), close: () => setOpen(false) }}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </SidebarCtx.Provider>
  );
}
