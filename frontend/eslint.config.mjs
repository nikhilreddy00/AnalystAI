import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Dashboard filters (2026-11-03): ChartRenderer kept useState(original), a copy of its chart
    // prop, so new data never reached the chart. Keep only the user's override in state.
    rules: {
      "no-restricted-syntax": ["error", {
        selector: "CallExpression[callee.name='useState'] > Identifier.arguments[name=/^(original|config|chart|props)$/]",
        message: "Derive from props; keep only what the user changed in state (a copied prop never updates).",
      }],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
