import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:      "#0a0e17",
        surface: "#111827",
        border:  "#1f2937",
        muted:   "#374151",
        subtle:  "#6b7280",
        text:    "#e5e7eb",
        accent:  "#3b82f6",     // blue — primary action
        bull:    "#22c55e",     // green — price up
        bear:    "#ef4444",     // red — price down
        warn:    "#f59e0b",     // amber — caution
        gold:    "#d97706",     // dark gold — penny highlight
      },
      fontFamily: {
        mono: ["var(--font-mono)", "JetBrains Mono", "monospace"],
        sans: ["var(--font-sans)", "Inter", "sans-serif"],
      },
      animation: {
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        ticker: "ticker 30s linear infinite",
      },
      keyframes: {
        ticker: {
          "0%":   { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
