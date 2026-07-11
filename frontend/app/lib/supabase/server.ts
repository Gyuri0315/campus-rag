import { createServerClient, type CookieOptions } from "@supabase/ssr";

type CookieStore = {
  getAll: () => { name: string; value: string }[];
  set: (name: string, value: string, options?: CookieOptions) => void;
};

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error(
    "Supabase environment variables are missing. Set SUPABASE_URL and SUPABASE_ANON_KEY in backend/.env, or set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY in frontend/.env.local.",
  );
}

export function createServerSupabaseClient(cookieStore: CookieStore) {
  return createServerClient(SUPABASE_URL!, SUPABASE_ANON_KEY!, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server components cannot set cookies; route handlers can.
        }
      },
    },
  });
}
