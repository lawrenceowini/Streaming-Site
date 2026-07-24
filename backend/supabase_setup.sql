-- Run this once in Supabase's SQL Editor (Project -> SQL Editor -> New query).
-- Stores which room codes exist, who owns each one, and which emails are
-- allowed to join. Only the signaling server (using the service_role key)
-- can read or write this table -- RLS is enabled with no policies, which
-- blocks the anon/authenticated API roles entirely. The service_role key
-- bypasses RLS by design, which is exactly what the backend needs and
-- exactly why that key must never appear in frontend code.

create table if not exists room_access (
  room_code text primary key,
  owner_email text not null,
  allowed_emails text[] not null default '{}',
  created_at timestamptz not null default now()
);

alter table room_access enable row level security;
-- No policies added on purpose -- see comment above.
