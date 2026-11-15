-- EV CANTOS: tabelas da nuvem. Rode UMA vez no Supabase: SQL Editor > New query > cole tudo > Run.
-- Pode rodar de novo sem problema (usa IF NOT EXISTS / DROP POLICY IF EXISTS).
--
-- Compartilhado entre as pessoas: alerts (sinais), scans, scan_games (jogos vistos).
-- Pessoal (uma linha por pessoa e sinal): decisions (apostei / ignorei).
-- O app so le, grava e atualiza; nenhuma policy permite APAGAR dados.
-- ATENCAO: quem tiver a URL + chave consegue ler e escrever essas tabelas. Compartilhe a chave so entre voces.

create table if not exists public.alerts (
  key text primary key,
  created_at text not null,
  found_by text,
  fixture_id text, start_time text, fixture text,
  market_id text, market text, outcome_id text, outcome text,
  pin_odds text,
  fair_prob double precision, betano_odd double precision, ev double precision, kelly_pct double precision,
  venue text, ask double precision, fee double precision, limit_usd double precision,
  closing_pin_odds text, clv double precision
);

create table if not exists public.scans (
  uid text primary key,
  user_name text,
  started_at text, finished_at text, trigger text, params text,
  listed integer, skipped integer, scanned integer, signals integer, blocked integer, queries integer,
  status text, error text
);

create table if not exists public.scan_games (
  scan_uid text not null,
  fixture_id text not null,
  user_name text,
  fixture text, league text, start_time text,
  compared integer, signals integer, best_ev double precision, note text,
  seen_at text,
  primary key (scan_uid, fixture_id)
);
create index if not exists scan_games_seen_at on public.scan_games (seen_at);

create table if not exists public.decisions (
  user_name text not null,
  alert_key text not null,
  status text,
  bet_odd double precision, bet_stake double precision,
  decided_at text,
  primary key (user_name, alert_key)
);

do $$
declare t text;
begin
  foreach t in array array['alerts', 'scans', 'scan_games', 'decisions'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists evcantos_select on public.%I', t);
    execute format('drop policy if exists evcantos_insert on public.%I', t);
    execute format('drop policy if exists evcantos_update on public.%I', t);
    execute format('create policy evcantos_select on public.%I for select to anon, authenticated using (true)', t);
    execute format('create policy evcantos_insert on public.%I for insert to anon, authenticated with check (true)', t);
    execute format('create policy evcantos_update on public.%I for update to anon, authenticated using (true) with check (true)', t);
    execute format('grant select, insert, update on public.%I to anon, authenticated', t);
  end loop;
end $$;
