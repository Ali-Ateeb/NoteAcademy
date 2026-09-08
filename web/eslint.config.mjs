import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

// Exported as a variable rather than inline so the array has a name in stack
// traces and the anonymous-default-export rule has nothing to say.
const config = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  // next-env.d.ts is written by `next build` and fails next/typescript's own
  // triple-slash rule. Linting a file the framework generates and gitignores
  // only means `npm run lint` passes before a build and fails after one.
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
];

export default config;
