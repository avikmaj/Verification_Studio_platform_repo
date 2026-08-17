/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The API base URL is public by design: the dashboard is a browser client and
  // read routes are unauthenticated. The bearer token is entered by the user at
  // runtime and kept in memory only — never baked into the bundle.
  env: { NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "" },
};
export default nextConfig;
