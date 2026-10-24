import type { NextConfig } from "next";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

function loadBackendEnv() {
  const envPath = resolve(process.cwd(), "../backend/.env");
  if (!existsSync(envPath)) return;

  const lines = readFileSync(envPath, "utf-8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const separator = trimmed.indexOf("=");
    if (separator === -1) continue;

    const key = trimmed.slice(0, separator).trim();
    const rawValue = trimmed.slice(separator + 1).trim();
    if (!key || process.env[key]) continue;

    process.env[key] = rawValue.replace(/^(['"])(.*)\1$/, "$2");
  }
}

loadBackendEnv();

const supabaseUrl =
  process.env.NEXT_PUBLIC_SUPABASE_URL ?? process.env.SUPABASE_URL;
const supabaseAnonKey =
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? process.env.SUPABASE_ANON_KEY;

const nextConfig: NextConfig = {
  env: {
    ...(supabaseUrl ? { NEXT_PUBLIC_SUPABASE_URL: supabaseUrl } : {}),
    ...(supabaseAnonKey
      ? { NEXT_PUBLIC_SUPABASE_ANON_KEY: supabaseAnonKey }
      : {}),
  },
  // 기본 위치(bottom-left)가 /chat 모바일 화면의 입력창·면책 문구와 겹쳐 보이는
  // 문제 리포트가 있어 화면 위쪽으로 옮김. 개발 모드 전용 배지라 배포본에는 영향 없음.
  devIndicators: {
    position: "top-right",
  },
};

export default nextConfig;
