"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, TrendingUp, BarChart2, Calendar,
  Zap, LineChart, Activity, Bot, X, CandlestickChart
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebar } from "./Providers";

const NAV = [
  { href: "/",           icon: LayoutDashboard,  label: "Dashboard" },
  { href: "/intraday",   icon: CandlestickChart, label: "Intraday"  },
  { href: "/positions",  icon: Activity,          label: "Positions" },
  { href: "/signals",    icon: TrendingUp,         label: "Signals"   },
  { href: "/options",    icon: BarChart2,           label: "F&O"       },
  { href: "/earnings",   icon: Calendar,            label: "Earnings"  },
  { href: "/penny",      icon: Zap,                 label: "Penny"     },
  { href: "/analytics",  icon: LineChart,            label: "Analytics" },
];

export default function Sidebar() {
  const path = usePathname();
  const { open, close } = useSidebar();

  return (
    <>
      {/* Mobile backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/60 z-20 md:hidden"
          onClick={close}
        />
      )}

      <aside className={cn(
        "fixed inset-y-0 left-0 w-64 bg-surface border-r border-border flex flex-col z-30 transition-transform duration-200",
        // Desktop: always visible. Mobile: slide in/out
        "md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full md:translate-x-0"
      )}>
        {/* Logo + mobile close */}
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-accent" />
            <span className="font-mono font-semibold text-sm tracking-wider text-text">
              AUTO-TRADER
            </span>
          </div>
          <button onClick={close} className="md:hidden text-subtle hover:text-text p-1">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
          {NAV.map(({ href, icon: Icon, label }) => {
            const active = path === href || (href !== "/" && path.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                onClick={close}
                className={cn(
                  "flex items-center gap-3 px-3 py-3 rounded-md text-sm transition-colors",
                  active
                    ? "bg-accent/15 text-accent font-medium"
                    : "text-subtle hover:text-text hover:bg-white/5"
                )}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-border">
          <p className="text-xs text-subtle font-mono">Claude Code v2</p>
          <p className="text-xs text-subtle">NSE/BSE Live</p>
        </div>
      </aside>
    </>
  );
}
