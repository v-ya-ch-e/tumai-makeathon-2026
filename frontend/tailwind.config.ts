import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: 'var(--canvas)',
        surface: 'var(--surface)',
        'surface-raised': 'var(--surface-raised)',
        hairline: 'var(--hairline)',
        ink: 'var(--ink)',
        'ink-muted': 'var(--ink-muted)',
        accent: 'var(--accent)',
        'accent-muted': 'var(--accent-muted)',
        good: 'var(--good)',
        warn: 'var(--warn)',
        bad: 'var(--bad)',
      },
      fontFamily: {
        sans: ['Geist', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['Geist Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderRadius: {
        DEFAULT: '6px',
        card: '10px',
        drawer: '14px',
      },
      boxShadow: {
        drawer: '0 10px 30px rgba(34, 28, 23, 0.14)',
      },
    },
  },
  plugins: [],
} satisfies Config
