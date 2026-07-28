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

-- ---------------------------------------------------------------------------
-- Added for incoming-call push notifications. Stores one row per browser
-- push subscription (a person can have several -- one per device/browser).
-- Same access model as room_access: RLS on, no policies, so only the
-- signaling server (via the service_role key) can read or write it.
-- ---------------------------------------------------------------------------
create table if not exists push_subscriptions (
  endpoint text primary key,
  email text not null,
  subscription jsonb not null,
  created_at timestamptz not null default now()
);

alter table push_subscriptions enable row level security;
-- No policies added on purpose -- see comment above.

-- ---------------------------------------------------------------------------
-- Added for the Calls hub (Recents / Favorites / Schedule). Unlike the two
-- tables above, these hold each person's *own* data with nothing shared
-- between accounts, so -- unlike room_access/push_subscriptions -- they're
-- opened up directly to the frontend via row-level security instead of being
-- routed through the backend: every policy below is scoped to
-- `auth.uid() = user_id`, so a signed-in user can only ever see or change
-- their own rows. The service_role key (used only by the backend's
-- scheduled-call reminder loop) bypasses RLS as always, so it can still see
-- every user's scheduled calls in order to send reminders.
-- ---------------------------------------------------------------------------
create extension if not exists pgcrypto;

-- Recents: one row per call the user has started or joined.
create table if not exists call_history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  room_code text not null,
  server_url text not null,
  peer_label text,
  created_at timestamptz not null default now()
);
alter table call_history enable row level security;
create policy "call_history_select_own" on call_history
  for select using (auth.uid() = user_id);
create policy "call_history_insert_own" on call_history
  for insert with check (auth.uid() = user_id);
create policy "call_history_delete_own" on call_history
  for delete using (auth.uid() = user_id);

-- Favorites: saved contacts for one-tap calling.
create table if not exists favorites (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  label text not null,
  target_email text not null,
  created_at timestamptz not null default now()
);
alter table favorites enable row level security;
create policy "favorites_select_own" on favorites
  for select using (auth.uid() = user_id);
create policy "favorites_insert_own" on favorites
  for insert with check (auth.uid() = user_id);
create policy "favorites_delete_own" on favorites
  for delete using (auth.uid() = user_id);

-- Scheduled calls: future calls with a generated room code, polled by the
-- backend's reminder loop (see main.py) once their time arrives.
create table if not exists scheduled_calls (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  owner_email text not null,
  title text,
  room_code text not null,
  server_url text not null,
  invited_emails text[] not null default '{}',
  scheduled_at timestamptz not null,
  notified boolean not null default false,
  created_at timestamptz not null default now()
);
alter table scheduled_calls enable row level security;
create policy "scheduled_calls_select_own" on scheduled_calls
  for select using (auth.uid() = user_id);
create policy "scheduled_calls_insert_own" on scheduled_calls
  for insert with check (auth.uid() = user_id);
create policy "scheduled_calls_update_own" on scheduled_calls
  for update using (auth.uid() = user_id);
create policy "scheduled_calls_delete_own" on scheduled_calls
  for delete using (auth.uid() = user_id);
