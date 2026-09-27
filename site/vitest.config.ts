// Unit tests live next to the code (src/**/*.test.ts); e2e/ is Playwright's.
import { defineConfig } from "vitest/config";

export default defineConfig({ test: { include: ["src/**/*.test.ts"] } });
