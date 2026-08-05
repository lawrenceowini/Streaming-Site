-- Run this in Supabase's SQL Editor (Project -> SQL Editor -> New query).
-- Safe to run this whole file again any time it changes -- every statement
-- below is idempotent (tables use IF NOT EXISTS, policies are dropped and
-- recreated, the realtime publication additions are guarded), so re-running
-- it after adding new features elsewhere in this file won't error on the
-- parts you already ran before.

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
drop policy if exists "call_history_select_own" on call_history;
create policy "call_history_select_own" on call_history
  for select using (auth.uid() = user_id);
drop policy if exists "call_history_insert_own" on call_history;
create policy "call_history_insert_own" on call_history
  for insert with check (auth.uid() = user_id);
drop policy if exists "call_history_delete_own" on call_history;
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
drop policy if exists "favorites_select_own" on favorites;
create policy "favorites_select_own" on favorites
  for select using (auth.uid() = user_id);
drop policy if exists "favorites_insert_own" on favorites;
create policy "favorites_insert_own" on favorites
  for insert with check (auth.uid() = user_id);
drop policy if exists "favorites_delete_own" on favorites;
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
drop policy if exists "scheduled_calls_select_own" on scheduled_calls;
create policy "scheduled_calls_select_own" on scheduled_calls
  for select using (auth.uid() = user_id);
drop policy if exists "scheduled_calls_insert_own" on scheduled_calls;
create policy "scheduled_calls_insert_own" on scheduled_calls
  for insert with check (auth.uid() = user_id);
drop policy if exists "scheduled_calls_update_own" on scheduled_calls;
create policy "scheduled_calls_update_own" on scheduled_calls
  for update using (auth.uid() = user_id);
drop policy if exists "scheduled_calls_delete_own" on scheduled_calls;
create policy "scheduled_calls_delete_own" on scheduled_calls
  for delete using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Persistent 1:1 messaging (the "Chats" tab). Three pieces:
--   1. profiles: lets a signed-in user look someone up by email to start a
--      conversation, the same way WhatsApp lets you find someone by phone
--      number. auth.users itself isn't queryable by clients, so this is a
--      thin public mirror of just (id, email), kept in sync automatically.
--   2. conversations: one row per 1:1 pair. The pair is stored in a fixed
--      order (user_a_id < user_b_id) so there's exactly one conversation
--      row per pair, never two.
--   3. messages: the actual messages, RLS-scoped to the two participants.
-- ---------------------------------------------------------------------------

create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique,
  created_at timestamptz not null default now()
);
alter table profiles enable row level security;
-- Anyone signed in can look up anyone else by email (needed to start a
-- conversation with them) -- this only ever exposes an id + email, nothing
-- sensitive, the same information a public "find a contact" search needs.
drop policy if exists "profiles_select_authenticated" on profiles;
create policy "profiles_select_authenticated" on profiles
  for select using (auth.role() = 'authenticated');

-- Keeps profiles in sync automatically whenever someone signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, lower(new.email))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- One-time backfill for accounts created before this feature existed.
insert into public.profiles (id, email)
select id, lower(email) from auth.users
on conflict (id) do nothing;

create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  user_a_id uuid not null references auth.users(id) on delete cascade,
  user_b_id uuid not null references auth.users(id) on delete cascade,
  user_a_email text not null,
  user_b_email text not null,
  last_message_at timestamptz,
  last_message_preview text,
  created_at timestamptz not null default now(),
  constraint conversations_ordered_pair check (user_a_id < user_b_id),
  constraint conversations_unique_pair unique (user_a_id, user_b_id)
);
alter table conversations enable row level security;
drop policy if exists "conversations_select_participant" on conversations;
create policy "conversations_select_participant" on conversations
  for select using (auth.uid() = user_a_id or auth.uid() = user_b_id);
drop policy if exists "conversations_insert_participant" on conversations;
create policy "conversations_insert_participant" on conversations
  for insert with check (auth.uid() = user_a_id or auth.uid() = user_b_id);
drop policy if exists "conversations_update_participant" on conversations;
create policy "conversations_update_participant" on conversations
  for update using (auth.uid() = user_a_id or auth.uid() = user_b_id);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  sender_id uuid not null references auth.users(id) on delete cascade,
  sender_email text not null,
  body text not null,
  status text not null default 'sent', -- sent | delivered | read
  created_at timestamptz not null default now()
);
alter table messages enable row level security;
drop policy if exists "messages_select_participant" on messages;
create policy "messages_select_participant" on messages
  for select using (
    exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );
drop policy if exists "messages_insert_participant" on messages;
create policy "messages_insert_participant" on messages
  for insert with check (
    sender_id = auth.uid()
    and exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );
-- Needed so the *recipient* (not just the sender) can mark a message as
-- delivered/read -- an update, not an insert, and not by the sender.
drop policy if exists "messages_update_participant" on messages;
create policy "messages_update_participant" on messages
  for update using (
    exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );

-- Realtime delivery: Supabase broadcasts inserts/updates on these tables to
-- any subscribed client automatically -- no custom fanout code needed on
-- our backend, unlike the call-signaling WebSocket server. Guarded so
-- re-running this file doesn't error if the table's already added.
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'messages'
  ) then
    alter publication supabase_realtime add table messages;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'conversations'
  ) then
    alter publication supabase_realtime add table conversations;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Media sharing in chat (images/files). Unlike the in-call file transfer
-- (live, peer-to-peer over a DataChannel -- only works while both people are
-- online at the same moment), this is stored in Supabase Storage so it's
-- there whenever the recipient next opens the conversation.
-- ---------------------------------------------------------------------------

alter table messages add column if not exists media_path text;
alter table messages add column if not exists media_mime text;
alter table messages add column if not exists media_name text;
alter table messages add column if not exists media_size bigint;

insert into storage.buckets (id, name, public)
values ('chat-media', 'chat-media', false)
on conflict (id) do nothing;

-- Objects are stored as "{conversation_id}/{random-filename}" -- these
-- policies check that the first path segment is a conversation the
-- requesting user is actually a participant in, same participant check as
-- the messages table itself.
drop policy if exists "chat_media_select_participant" on storage.objects;
create policy "chat_media_select_participant" on storage.objects
  for select using (
    bucket_id = 'chat-media'
    and exists (
      select 1 from conversations c
      where c.id::text = (storage.foldername(name))[1]
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );
drop policy if exists "chat_media_insert_participant" on storage.objects;
create policy "chat_media_insert_participant" on storage.objects
  for insert with check (
    bucket_id = 'chat-media'
    and exists (
      select 1 from conversations c
      where c.id::text = (storage.foldername(name))[1]
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );

-- ---------------------------------------------------------------------------
-- Usernames: people search for and message each other by username instead
-- of email (email is still used internally for push delivery, since
-- push_subscriptions is keyed by email -- this is purely an identity/search
-- layer on top, not a replacement for it).
-- ---------------------------------------------------------------------------

alter table profiles add column if not exists username text;

-- Case-insensitive uniqueness ("Sam" and "sam" can't both exist) while still
-- preserving whatever casing the person actually chose for display.
drop index if exists profiles_username_lower_idx;
create unique index profiles_username_lower_idx on profiles (lower(username))
  where username is not null;

-- profiles only had a select policy before -- this lets someone set/change
-- their own username (and only their own; auth.uid() = id enforces that).
drop policy if exists "profiles_update_own" on profiles;
create policy "profiles_update_own" on profiles
  for update using (auth.uid() = id) with check (auth.uid() = id);

-- Denormalized onto conversations (like the emails already are) so the chat
-- list and thread header can display a username without an extra lookup,
-- and so it still shows correctly even if the other person changes their
-- username later -- it reflects what it was when the conversation started,
-- same as the email columns already do.
alter table conversations add column if not exists user_a_username text;
alter table conversations add column if not exists user_b_username text;

-- ---------------------------------------------------------------------------
-- Profile pictures. Unlike chat-media (private, participants-only), these
-- are public -- a profile picture needs to display in lots of places (the
-- sidebar tab icon, conversation lists, thread headers) and isn't sensitive,
-- so a plain public URL is far simpler than juggling signed URLs everywhere
-- it's shown.
-- ---------------------------------------------------------------------------

alter table profiles add column if not exists avatar_url text;

insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;

drop policy if exists "avatars_public_read" on storage.objects;
create policy "avatars_public_read" on storage.objects
  for select using (bucket_id = 'avatars');

-- Stored as "{user_id}/avatar.<ext>" -- these policies check the first path
-- segment is the uploader's own id, so nobody can overwrite someone else's.
drop policy if exists "avatars_insert_own" on storage.objects;
create policy "avatars_insert_own" on storage.objects
  for insert with check (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] = auth.uid()::text
  );
drop policy if exists "avatars_update_own" on storage.objects;
create policy "avatars_update_own" on storage.objects
  for update using (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- ---------------------------------------------------------------------------
-- Group conversations. A deliberately separate structure from the 1:1
-- conversations/messages tables above -- a group has an open-ended number
-- of members, which doesn't fit the "exactly two people, one fixed pair"
-- shape those tables were built around. Three pieces:
--   1. groups: one row per group (name, optional photo, who created it).
--   2. group_members: who's in each group.
--   3. group_messages: the messages themselves, with per-member read
--      tracking in group_message_reads (each member reads independently,
--      unlike a 1:1 chat where there's only ever one "other side").
-- ---------------------------------------------------------------------------

create table if not exists groups (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  avatar_url text,
  created_by uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table groups enable row level security;

create table if not exists group_members (
  group_id uuid not null references groups(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (group_id, user_id)
);
alter table group_members enable row level security;

-- A policy that checks group_members by querying group_members again (as
-- group_members' own policies naturally need to) causes Postgres to
-- recurse into the very policy it's evaluating -- in bad cases this doesn't
-- even come back as a clean error, the connection just gets dropped, which
-- a browser reports as a generic "failed to fetch" with no useful detail.
-- A SECURITY DEFINER function sidesteps this: it runs with the function
-- owner's privileges, so its internal query bypasses RLS entirely instead
-- of triggering it again.
create or replace function public.is_group_member(p_group_id uuid, p_user_id uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
  select exists (
    select 1 from group_members
    where group_id = p_group_id and user_id = p_user_id
  );
$$;

-- groups policies
drop policy if exists "groups_select_member" on groups;
create policy "groups_select_member" on groups
  for select using (
    public.is_group_member(groups.id, auth.uid())
    -- The creator's own group_members row is added in a separate query
    -- right after this insert, not atomically with it -- without this,
    -- the split-second between "group created" and "creator added as a
    -- member" makes the brand new row briefly invisible to its own
    -- creator, which Supabase reports as an RLS violation on the insert
    -- itself (since insert+select() reads the row right back).
    or groups.created_by = auth.uid()
  );
drop policy if exists "groups_insert_any_authenticated" on groups;
create policy "groups_insert_any_authenticated" on groups
  for insert with check (auth.uid() = created_by);
drop policy if exists "groups_update_member" on groups;
create policy "groups_update_member" on groups
  for update using (
    public.is_group_member(groups.id, auth.uid())
    or groups.created_by = auth.uid()
  );

-- group_members policies
drop policy if exists "group_members_select_member" on group_members;
create policy "group_members_select_member" on group_members
  for select using (public.is_group_member(group_members.group_id, auth.uid()));
-- Three ways a row can legitimately be inserted here: you're adding
-- yourself (accepting membership), you're the group's creator adding its
-- very first members (before any members exist yet -- a plain "is an
-- existing member" check can't pass at that moment), or you're an existing
-- member adding someone new later.
drop policy if exists "group_members_insert" on group_members;
create policy "group_members_insert" on group_members
  for insert with check (
    auth.uid() = user_id
    or exists (select 1 from groups g where g.id = group_id and g.created_by = auth.uid())
    or public.is_group_member(group_members.group_id, auth.uid())
  );
drop policy if exists "group_members_delete_self" on group_members;
create policy "group_members_delete_self" on group_members
  for delete using (auth.uid() = user_id); -- leaving a group

create table if not exists group_messages (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references groups(id) on delete cascade,
  sender_id uuid not null references auth.users(id) on delete cascade,
  sender_email text not null,
  body text not null default '',
  media_path text,
  media_mime text,
  media_name text,
  media_size bigint,
  created_at timestamptz not null default now()
);
alter table group_messages enable row level security;
drop policy if exists "group_messages_select_member" on group_messages;
create policy "group_messages_select_member" on group_messages
  for select using (public.is_group_member(group_messages.group_id, auth.uid()));
drop policy if exists "group_messages_insert_member" on group_messages;
create policy "group_messages_insert_member" on group_messages
  for insert with check (
    sender_id = auth.uid()
    and public.is_group_member(group_messages.group_id, auth.uid())
  );

-- Per-member read tracking -- one row per (message, member) once that
-- member has seen it. Absence of a row means "not yet read by them".
create table if not exists group_message_reads (
  message_id uuid not null references group_messages(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  read_at timestamptz not null default now(),
  primary key (message_id, user_id)
);
alter table group_message_reads enable row level security;
drop policy if exists "group_message_reads_select_member" on group_message_reads;
create policy "group_message_reads_select_member" on group_message_reads
  for select using (
    exists (
      select 1 from group_messages gmsg
      where gmsg.id = group_message_reads.message_id
        and public.is_group_member(gmsg.group_id, auth.uid())
    )
  );
drop policy if exists "group_message_reads_insert_own" on group_message_reads;
create policy "group_message_reads_insert_own" on group_message_reads
  for insert with check (auth.uid() = user_id);

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'group_messages'
  ) then
    alter publication supabase_realtime add table group_messages;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'group_members'
  ) then
    alter publication supabase_realtime add table group_members;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'group_message_reads'
  ) then
    alter publication supabase_realtime add table group_message_reads;
  end if;
end $$;

-- Group media reuses the same private chat-media bucket, stored under
-- "group-{group_id}/..." instead of a conversation id, with the equivalent
-- member-scoped access policies.
drop policy if exists "chat_media_select_group_member" on storage.objects;
create policy "chat_media_select_group_member" on storage.objects
  for select using (
    bucket_id = 'chat-media'
    and (storage.foldername(name))[1] like 'group-%'
    and public.is_group_member(
      replace((storage.foldername(name))[1], 'group-', '')::uuid, auth.uid()
    )
  );
drop policy if exists "chat_media_insert_group_member" on storage.objects;
create policy "chat_media_insert_group_member" on storage.objects
  for insert with check (
    bucket_id = 'chat-media'
    and (storage.foldername(name))[1] like 'group-%'
    and public.is_group_member(
      replace((storage.foldername(name))[1], 'group-', '')::uuid, auth.uid()
    )
  );

-- ---------------------------------------------------------------------------
-- Group photos. Reuses the existing public "avatars" bucket (a group photo
-- isn't sensitive, same reasoning as personal profile pictures), stored
-- under "group-{group_id}/photo.<ext>" instead of a user id folder. Any
-- current member can change it -- consistent with how adding new members
-- already works (any member, not just the creator).
-- ---------------------------------------------------------------------------

drop policy if exists "avatars_insert_group_member" on storage.objects;
create policy "avatars_insert_group_member" on storage.objects
  for insert with check (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] like 'group-%'
    and public.is_group_member(
      replace((storage.foldername(name))[1], 'group-', '')::uuid, auth.uid()
    )
  );
drop policy if exists "avatars_update_group_member" on storage.objects;
create policy "avatars_update_group_member" on storage.objects
  for update using (
    bucket_id = 'avatars'
    and (storage.foldername(name))[1] like 'group-%'
    and public.is_group_member(
      replace((storage.foldername(name))[1], 'group-', '')::uuid, auth.uid()
    )
  );

-- ---------------------------------------------------------------------------
-- Favoriting for Chats and Groups. The existing "favorites" table is for
-- quick-dial calling contacts (by email) and stays as-is. This is a
-- separate, simpler concept: marking a specific conversation or group as a
-- favorite for quick access, per user (so if two people share a
-- conversation, each can favorite it independently of the other).
-- ---------------------------------------------------------------------------

create table if not exists favorite_conversations (
  user_id uuid not null references auth.users(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, conversation_id)
);
alter table favorite_conversations enable row level security;
drop policy if exists "favorite_conversations_select_own" on favorite_conversations;
create policy "favorite_conversations_select_own" on favorite_conversations
  for select using (auth.uid() = user_id);
drop policy if exists "favorite_conversations_insert_own" on favorite_conversations;
create policy "favorite_conversations_insert_own" on favorite_conversations
  for insert with check (auth.uid() = user_id);
drop policy if exists "favorite_conversations_delete_own" on favorite_conversations;
create policy "favorite_conversations_delete_own" on favorite_conversations
  for delete using (auth.uid() = user_id);

create table if not exists favorite_groups (
  user_id uuid not null references auth.users(id) on delete cascade,
  group_id uuid not null references groups(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, group_id)
);
alter table favorite_groups enable row level security;
drop policy if exists "favorite_groups_select_own" on favorite_groups;
create policy "favorite_groups_select_own" on favorite_groups
  for select using (auth.uid() = user_id);
drop policy if exists "favorite_groups_insert_own" on favorite_groups;
create policy "favorite_groups_insert_own" on favorite_groups
  for insert with check (auth.uid() = user_id);
drop policy if exists "favorite_groups_delete_own" on favorite_groups;
create policy "favorite_groups_delete_own" on favorite_groups
  for delete using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Contact info panel (Phase B): blocking, per-chat notification
-- preferences (mute + tone), and clearing a chat's history.
-- ---------------------------------------------------------------------------

create table if not exists blocked_users (
  blocker_id uuid not null references auth.users(id) on delete cascade,
  blocked_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (blocker_id, blocked_id)
);
alter table blocked_users enable row level security;
drop policy if exists "blocked_users_select_own" on blocked_users;
create policy "blocked_users_select_own" on blocked_users
  for select using (auth.uid() = blocker_id);
drop policy if exists "blocked_users_insert_own" on blocked_users;
create policy "blocked_users_insert_own" on blocked_users
  for insert with check (auth.uid() = blocker_id);
drop policy if exists "blocked_users_delete_own" on blocked_users;
create policy "blocked_users_delete_own" on blocked_users
  for delete using (auth.uid() = blocker_id);

-- Enforces blocking at the database level, not just in the UI: if the
-- *other* participant in a conversation has blocked me, my inserts into it
-- are rejected outright, regardless of what client code does.
drop policy if exists "messages_insert_participant" on messages;
create policy "messages_insert_participant" on messages
  for insert with check (
    sender_id = auth.uid()
    and exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
        and not exists (
          select 1 from blocked_users bu
          where bu.blocker_id = (case when c.user_a_id = auth.uid() then c.user_b_id else c.user_a_id end)
            and bu.blocked_id = auth.uid()
        )
    )
  );

-- "Clear chat" deletes the shared message history for both people (this
-- app doesn't keep per-device copies of messages, so there's no way to
-- clear it on just one side without a much bigger data-model change).
drop policy if exists "messages_delete_participant" on messages;
create policy "messages_delete_participant" on messages
  for delete using (
    exists (
      select 1 from conversations c
      where c.id = messages.conversation_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );

-- Per-chat notification preferences: whether to be alerted at all, and
-- which tone to play. Absence of a row means "default: on, default tone".
create table if not exists conversation_notification_prefs (
  user_id uuid not null references auth.users(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  muted boolean not null default false,
  custom_tone text not null default 'default',
  primary key (user_id, conversation_id)
);
alter table conversation_notification_prefs enable row level security;
drop policy if exists "conv_notif_prefs_select_own" on conversation_notification_prefs;
create policy "conv_notif_prefs_select_own" on conversation_notification_prefs
  for select using (auth.uid() = user_id);
drop policy if exists "conv_notif_prefs_insert_own" on conversation_notification_prefs;
create policy "conv_notif_prefs_insert_own" on conversation_notification_prefs
  for insert with check (auth.uid() = user_id);
drop policy if exists "conv_notif_prefs_update_own" on conversation_notification_prefs;
create policy "conv_notif_prefs_update_own" on conversation_notification_prefs
  for update using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Group info panel (Phase C): description, removing members (not just
-- leaving yourself), and creator-only chat clearing.
-- ---------------------------------------------------------------------------

alter table groups add column if not exists description text;

-- Broadens the earlier self-only leave policy: consistent with how adding
-- members already works (any member, not just the creator), any current
-- member can also remove someone else, not only themselves.
drop policy if exists "group_members_delete_self" on group_members;
drop policy if exists "group_members_delete_by_member" on group_members;
create policy "group_members_delete_by_member" on group_members
  for delete using (public.is_group_member(group_members.group_id, auth.uid()));

-- Clearing a group's history is more consequential than a 1:1 chat (it
-- affects everyone in the group, not just the two of you), so this is
-- restricted to whoever created the group.
drop policy if exists "group_messages_delete_creator" on group_messages;
create policy "group_messages_delete_creator" on group_messages
  for delete using (
    exists (
      select 1 from groups g
      where g.id = group_messages.group_id and g.created_by = auth.uid()
    )
  );

-- ---------------------------------------------------------------------------
-- Presence & typing indicators (Phase A). "Online now" is handled entirely
-- client-side via Supabase Realtime Presence (ephemeral, no table needed --
-- it disappears the instant a socket disconnects). This column is only for
-- "last seen" once someone's presence drops: each signed-in tab periodically
-- (and on visibility change / unload) writes its own last_seen_at, covered
-- by the existing profiles_update_own policy above (auth.uid() = id), so no
-- new policy is needed here.
-- ---------------------------------------------------------------------------

alter table profiles add column if not exists last_seen_at timestamptz;

-- ---------------------------------------------------------------------------
-- Message thread polish (Phase B): reply/quote and emoji reactions. Date
-- dividers need nothing here -- they're derived client-side from each
-- message's existing created_at.
-- ---------------------------------------------------------------------------

-- Reply/quote: a message can point at one earlier message in the same
-- conversation/group. "on delete set null" rather than cascade, so deleting
-- the original message a reply pointed at doesn't take the reply down with
-- it -- the frontend just falls back to an "Original message" placeholder.
alter table messages add column if not exists reply_to_message_id uuid references messages(id) on delete set null;
alter table group_messages add column if not exists reply_to_message_id uuid references group_messages(id) on delete set null;

-- One reaction per person per message (tapping a different emoji swaps your
-- previous one, same as WhatsApp) -- enforced by the unique constraint
-- rather than needing an update policy; the frontend deletes-then-inserts
-- or updates the existing row by id.
create table if not exists message_reactions (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references messages(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  emoji text not null,
  created_at timestamptz not null default now(),
  unique (message_id, user_id)
);
alter table message_reactions enable row level security;

drop policy if exists "message_reactions_select_participant" on message_reactions;
create policy "message_reactions_select_participant" on message_reactions
  for select using (
    exists (
      select 1 from messages m
      join conversations c on c.id = m.conversation_id
      where m.id = message_reactions.message_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );

drop policy if exists "message_reactions_insert_own" on message_reactions;
create policy "message_reactions_insert_own" on message_reactions
  for insert with check (
    user_id = auth.uid()
    and exists (
      select 1 from messages m
      join conversations c on c.id = m.conversation_id
      where m.id = message_reactions.message_id
        and (c.user_a_id = auth.uid() or c.user_b_id = auth.uid())
    )
  );

drop policy if exists "message_reactions_update_own" on message_reactions;
create policy "message_reactions_update_own" on message_reactions
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "message_reactions_delete_own" on message_reactions;
create policy "message_reactions_delete_own" on message_reactions
  for delete using (user_id = auth.uid());

create table if not exists group_message_reactions (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references group_messages(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  emoji text not null,
  created_at timestamptz not null default now(),
  unique (message_id, user_id)
);
alter table group_message_reactions enable row level security;

-- Reuses the same is_group_member() SECURITY DEFINER helper as the rest of
-- the groups schema, for the same reason it was introduced there: a plain
-- membership subquery here would hit the same self-referential recursion
-- issue on group_members.
drop policy if exists "group_message_reactions_select_member" on group_message_reactions;
create policy "group_message_reactions_select_member" on group_message_reactions
  for select using (
    exists (
      select 1 from group_messages gm
      where gm.id = group_message_reactions.message_id
        and public.is_group_member(gm.group_id, auth.uid())
    )
  );

drop policy if exists "group_message_reactions_insert_own" on group_message_reactions;
create policy "group_message_reactions_insert_own" on group_message_reactions
  for insert with check (
    user_id = auth.uid()
    and exists (
      select 1 from group_messages gm
      where gm.id = group_message_reactions.message_id
        and public.is_group_member(gm.group_id, auth.uid())
    )
  );

drop policy if exists "group_message_reactions_update_own" on group_message_reactions;
create policy "group_message_reactions_update_own" on group_message_reactions
  for update using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "group_message_reactions_delete_own" on group_message_reactions;
create policy "group_message_reactions_delete_own" on group_message_reactions
  for delete using (user_id = auth.uid());

-- Both reaction tables need to be in the realtime publication for the
-- frontend's live "someone reacted" updates to fire at all (same as every
-- other table this app subscribes to with postgres_changes).
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'message_reactions'
  ) then
    alter publication supabase_realtime add table message_reactions;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'group_message_reactions'
  ) then
    alter publication supabase_realtime add table group_message_reactions;
  end if;
end $$;

