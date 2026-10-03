import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep the development-only Next.js badge away from the workspace sidebar's
  // bottom-left controls (Log out, theme toggle).
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
