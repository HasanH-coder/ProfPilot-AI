// Supabase project settings, read from frontend/.env.local.
// Both values are meant to be public: the browser needs them, and the data
// itself is protected by Row Level Security in the database.
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

export const isSupabaseConfigured = Boolean(url && publishableKey);

export function getSupabaseConfig() {
  if (!url || !publishableKey) {
    throw new Error(
      "Supabase is not configured. Copy frontend/.env.example to frontend/.env.local and add your project's URL and publishable key.",
    );
  }

  return { url, publishableKey };
}
