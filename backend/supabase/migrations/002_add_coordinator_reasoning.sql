-- NexusAI: store Agent A's outcome next to Agent B's verification result.
-- Apply manually: Supabase dashboard > SQL Editor > New query > paste this file > Run.
-- Safe to re-run. Requires 001_create_verification_reviews.sql.
--
-- Agent B saves the verified result (one row per A2A task, as before). The NexusAI API then
-- adds Agent A's recommended action and reasoning to that same row, so the dashboard can show
-- the full outcome. Rows created before this migration (or via the CLI) simply have nulls here.

begin;

alter table public.verification_reviews
    add column if not exists coordinator_task_id text,
    add column if not exists recommended_action  text,
    add column if not exists risk_level          text,
    add column if not exists reasoning_source    text,
    add column if not exists reasoning           jsonb;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'verification_reviews_action_check') then
        alter table public.verification_reviews add constraint verification_reviews_action_check
            check (recommended_action is null or recommended_action in
                   ('ROUTE_TO_APPROVER', 'HOLD_AND_QUERY_SUPPLIER', 'REQUEST_MISSING_INFORMATION', 'CORRECT_AND_RESUBMIT'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'verification_reviews_risk_check') then
        alter table public.verification_reviews add constraint verification_reviews_risk_check
            check (risk_level is null or risk_level in ('low', 'medium', 'high'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'verification_reviews_reasoning_source_check') then
        alter table public.verification_reviews add constraint verification_reviews_reasoning_source_check
            check (reasoning_source is null or reasoning_source in ('llm', 'built_in'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'verification_reviews_reasoning_is_object') then
        alter table public.verification_reviews add constraint verification_reviews_reasoning_is_object
            check (reasoning is null or jsonb_typeof(reasoning) = 'object');
    end if;
end $$;

create index if not exists verification_reviews_risk_idx
    on public.verification_reviews (risk_level) where risk_level is not null;

comment on column public.verification_reviews.reasoning is
    'Agent A explanation: {explanation, risk_points, supplier_email, model, fallback_reason}. Not a payment approval.';

commit;

-- Make the Supabase API (PostgREST) see the new columns immediately.
notify pgrst, 'reload schema';
