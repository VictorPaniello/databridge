/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Explicit imports (import { describe, it, expect } from "vitest") in
    // every test file instead - keeps tsconfig.json's "types" untouched
    // and makes each test file's dependency on the test runner visible
    // rather than ambient/global.
    globals: false,
  },
});
