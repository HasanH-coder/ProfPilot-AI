import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

// Component tests run in a real browser (Chromium, through Playwright), so
// layout, scrolling and rendering behave as they do for professors.
// First run: `npx playwright install chromium`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      // Next.js provides this marker module itself; in tests, server code is never loaded.
      "server-only": fileURLToPath(new URL("./tests/mocks/server-only.ts", import.meta.url)),
    },
  },
  optimizeDeps: {
    // Bundling it ahead would cut it off from the running test.
    exclude: ["vitest-browser-react"],
    // Bundled up front, so a first run never loads two copies of React.
    include: [
      "react",
      "react-dom",
      "react-dom/client",
      "@base-ui/react/alert-dialog",
      "@base-ui/react/button",
      "@base-ui/react/checkbox",
      "@base-ui/react/dialog",
      "@base-ui/react/input",
      "@base-ui/react/menu",
      "@base-ui/react/merge-props",
      "@base-ui/react/select",
      "@base-ui/react/separator",
      "@base-ui/react/slider",
      "@base-ui/react/tabs",
      "@base-ui/react/toggle",
      "@base-ui/react/toggle-group",
      "@base-ui/react/use-render",
      "@supabase/ssr",
      "class-variance-authority",
      "cn",
      "lucide-react",
      "next-themes",
    ],
  },
  // Public settings the app reads at build time. Tests never reach these addresses.
  define: {
    "process.env.NEXT_PUBLIC_SUPABASE_URL": JSON.stringify("http://localhost:54321"),
    "process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": JSON.stringify("test-publishable-key"),
    "process.env.NEXT_PUBLIC_API_URL": JSON.stringify("http://localhost:8000"),
  },
  test: {
    include: ["tests/**/*.test.tsx"],
    setupFiles: ["tests/setup.tsx"],
    browser: {
      enabled: true,
      headless: true,
      provider: playwright(),
      instances: [{ browser: "chromium" }],
      viewport: { width: 1280, height: 900 },
      // No screenshot folders in the repository when a test fails.
      screenshotFailures: false,
    },
  },
});
