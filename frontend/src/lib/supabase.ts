import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  // Fail loudly at import rather than silently signing every user out later.
  throw new Error(
    "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set in frontend/.env.local",
  );
}

export const supabase = createClient(url, anonKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true, // needed for the OAuth redirect back from Google
  },
});

/** Current access token, or "" when signed out. Refreshes automatically. */
export async function getAccessToken(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}
