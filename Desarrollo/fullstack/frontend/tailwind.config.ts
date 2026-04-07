import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--bg)",
        surface: "var(--surface)",
        "surface-low": "var(--surface-low)",
        "surface-high": "var(--surface-high)",
        ink: "var(--ink)",
        muted: "var(--muted)",
        line: "var(--line)",
        brand: "var(--brand)",
        success: "var(--ok)",
        warning: "var(--warn)",
        danger: "var(--bad)",
      },
      boxShadow: {
        ambient: "0 12px 48px rgba(0, 0, 0, 0.04)",
        editorial: "0 0 1px rgba(0, 0, 0, 0.1), 0 4px 24px rgba(0, 0, 0, 0.04)",
      },
    },
  },
  plugins: [],
};

export default config;
