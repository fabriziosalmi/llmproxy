/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "hsl(240 10% 4%)", // Zinc-950 equivalent
        foreground: "hsl(0 0% 98%)",
        card: "hsl(240 10% 9%)", // Zinc-900/50
        border: "rgba(255, 255, 255, 0.1)",
        accent: "hsl(263.4 70% 50.4%)", // Sleek Violet
      },
      borderRadius: {
        lg: "0.5rem",
        md: "calc(0.5rem - 2px)",
        sm: "calc(0.5rem - 4px)",
      },
    },
  },
  plugins: [],
}
