/** @type {import('tailwindcss').Config} */
export default {
    // L.3: opt-in light theme. The default theme stays dark — the operator
    // console is dark by design — and `.theme-light` on <html> flips the
    // surface overrides defined in src/ui/tokens.css.
    darkMode: 'class',
    content: [
        './index.html',
        './chat.html',
        './main.js',
        './chat.js',
        './components/**/*.{js,ts}',
        './services/**/*.{js,ts}',
        './src/**/*.{ts,tsx}',
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: [
                    'Inter',
                    '-apple-system',
                    'BlinkMacSystemFont',
                    '"SF Pro Text"',
                    '"Helvetica Neue"',
                    'sans-serif',
                ],
                mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', 'monospace'],
            },
            colors: {
                'apple-blue': '#007aff',
                'apple-green': '#34c759',
                'apple-red': '#ff3b30',
                'apple-gray': '#8e8e93',
            },
        },
    },
    plugins: [],
};
