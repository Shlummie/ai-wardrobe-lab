import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Images are served by the private local API; framework optimization
      // would proxy them through the frontend and add no value here.
      "@next/next/no-img-element": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "dist/**",
    ".vinext/**",
    ".wrangler/**",
    ".venv/**",
    ".venv-catvton/**",
    "models/**",
    "data/**",
    "logs/**",
    "run/**",
    "densepose_/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
