/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
      { source: "/v1/:path*", destination: "http://localhost:8000/v1/:path*" },
    ];
  },
};

export default nextConfig;
