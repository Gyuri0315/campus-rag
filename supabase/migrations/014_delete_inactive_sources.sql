-- ── One-off hard delete of inactive (superseded) sources ─────────────────
-- Background (2026-09-30 measurement): every re-load kept the previous
-- version of a document as status='inactive' instead of deleting it
-- (load_to_supabase.deduplicate_sources). Those dead rows had grown to
-- rag 51% / rule 49% / pknu_notice 31% / pknu_student_life 76% of the chunk
-- tables. No query reads them (every RPC filters s.status = 'active') but the
-- lexical/vector RPCs still read past them, and the bigger tables mean more
-- disk reads -- the root of the 6-7.5s lexical calls that hit the 8s
-- statement timeout. deduplicate_sources now deletes instead, so this script
-- only has to clear the backlog once.
--
-- Chunks are removed by `on delete cascade` (chunks.source_id references
-- sources.id). Each table is its own transaction so a failure only rolls
-- back that table. Run the "before" counts first and compare with "after".
--
-- NOT included on purpose: REINDEX of the HNSW indexes. A rebuild on these
-- tables can exceed Supabase limits and fail silently (see HNSW pitfall
-- note); pgvector handles deleted tuples on its own and VACUUM cleans them.

-- ── Before (read-only) ────────────────────────────────────────────────────
-- select 'rag' as t, status, count(*) from public.rag_sources group by status
-- union all select 'rule', status, count(*) from public.rule_sources group by status
-- union all select 'pknu_notice', status, count(*) from public.pknu_notice_sources group by status
-- union all select 'pknu_student_life', status, count(*) from public.pknu_student_life_sources group by status;

begin;
set local statement_timeout = '15min';
delete from public.rag_sources where status = 'inactive';
commit;

begin;
set local statement_timeout = '15min';
delete from public.rule_sources where status = 'inactive';
commit;

begin;
set local statement_timeout = '15min';
delete from public.pknu_notice_sources where status = 'inactive';
commit;

begin;
set local statement_timeout = '15min';
delete from public.pknu_student_life_sources where status = 'inactive';
commit;

-- VACUUM cannot run inside a transaction block; run each separately.
vacuum (analyze) public.rag_chunks;
vacuum (analyze) public.rag_sources;
vacuum (analyze) public.rule_chunks;
vacuum (analyze) public.rule_sources;
vacuum (analyze) public.pknu_notice_chunks;
vacuum (analyze) public.pknu_notice_sources;
vacuum (analyze) public.pknu_student_life_chunks;
vacuum (analyze) public.pknu_student_life_sources;

-- ── After (read-only): expect no 'inactive' rows, 'active' counts unchanged ─
-- (same query as "Before")
