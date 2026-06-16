/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // Don't fail production container builds on lint/type issues in existing code.
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
}

export default nextConfig
