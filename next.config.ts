import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // Pin the tracing root to this project to avoid Next.js picking up a
  // parent-directory lockfile as the workspace root.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
