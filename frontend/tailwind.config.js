/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#080a0d",
        surface: "#0d1117",
        "surface-2": "#141c24",
        "surface-3": "#1c2530",
        border: "#243040",
        "border-glow": "#364d66",
        text: "#dce6ef",
        "text-dim": "#6e8aa0",
        "text-subtle": "#405060",
        accent: {
          blue: "#5b9bd5",
          purple: "#9b8aaa",
          pink: "#c4788a",
          green: "#4dc88a",
          orange: "#f0944a",
          red: "#e05252",
          cyan: "#22d3ee",
          "cyan-dim": "#0891b2",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["'Playfair Display'", "Georgia", "serif"],
        mono: ["'JetBrains Mono'", "Menlo", "ui-monospace", "monospace"],
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 220ms ease-out",
      },
    },
  },
  plugins: [],
};
