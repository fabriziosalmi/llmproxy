import js from '@eslint/js';
import tseslint from '@typescript-eslint/eslint-plugin';
import tsparser from '@typescript-eslint/parser';
import globals from 'globals';
import prettier from 'eslint-config-prettier';

export default [
    {
        ignores: ['dist/**', 'public/**', 'node_modules/**', 'coverage/**', 'playwright-report/**', 'test-results/**'],
    },
    js.configs.recommended,
    {
        files: ['**/*.{js,mjs,cjs}'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: {
                ...globals.browser,
                Chart: 'readonly',
                Terminal: 'readonly',
                FitAddon: 'readonly',
                WebglAddon: 'readonly',
                tailwind: 'readonly',
            },
        },
        rules: {
            'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
            'no-console': 'off',
            'no-empty': ['warn', { allowEmptyCatch: true }],
            'prefer-const': 'warn',
            'no-var': 'error',
        },
    },
    {
        files: ['**/*.ts'],
        languageOptions: {
            parser: tsparser,
            parserOptions: { ecmaVersion: 2022, sourceType: 'module' },
            globals: { ...globals.browser },
        },
        plugins: { '@typescript-eslint': tseslint },
        rules: {
            ...tseslint.configs.recommended.rules,
            '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
            '@typescript-eslint/no-explicit-any': 'off',
            'no-unused-vars': 'off',
        },
    },
    {
        files: ['**/*.test.{js,ts}', '__tests__/**/*', 'e2e/**/*'],
        languageOptions: {
            globals: {
                ...globals.browser,
                ...globals.node,
                describe: 'readonly',
                it: 'readonly',
                test: 'readonly',
                expect: 'readonly',
                beforeAll: 'readonly',
                afterAll: 'readonly',
                beforeEach: 'readonly',
                afterEach: 'readonly',
                vi: 'readonly',
            },
        },
    },
    {
        files: [
            'vite.config.ts',
            'vitest.config.ts',
            'playwright.config.ts',
            'postcss.config.js',
            'tailwind.config.js',
        ],
        languageOptions: {
            globals: { ...globals.node },
        },
    },
    prettier,
];
