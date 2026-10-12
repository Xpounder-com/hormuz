import type { NextConfig } from 'next';
import { BASE_PATH } from './lib/site.mjs';

const nextConfig: NextConfig = {
  output: 'export',
  basePath: BASE_PATH,
  trailingSlash: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  experimental: { cpus: 2 },
};

export default nextConfig;
