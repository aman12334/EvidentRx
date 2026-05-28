import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Enterprise compliance palette
        brand: {
          50:  "#f0f4ff",
          100: "#dce6ff",
          200: "#b9cdff",
          300: "#85a8ff",
          400: "#4d7aff",
          500: "#1a4fff",
          600: "#0033eb",
          700: "#0027c7",
          800: "#0022a0",
          900: "#001880",
        },
        severity: {
          critical: "#dc2626",
          high:     "#ea580c",
          medium:   "#ca8a04",
          low:      "#16a34a",
        },
        surface: {
          DEFAULT: "#ffffff",
          subtle:  "#f8fafc",
          muted:   "#f1f5f9",
          dark:    "#0f172a",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Menlo", "monospace"],
      },
      borderRadius: {
        DEFAULT: "6px",
      },
    },
  },
  plugins: [],
};

export default config;
