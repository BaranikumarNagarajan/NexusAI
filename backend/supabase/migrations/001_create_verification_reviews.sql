-- NexusAI: verification_reviews
-- One row per final A2A verification task produced by Agent B.
--
-- Apply manually: Supabase dashboard > SQL Editor > New query > paste this file > Run.
-- Safe to re-run: every statement is idempotent.
--
-- Access model: Row Level Security is ENABLED with NO policies, and table
-- privileges are revoked from the anon and authenticated roles. Only the
-- backend, using the secret key (service_role, which bypasses RLS), can read or
-- write. Never expose that key to a browser.

begin;

create table if not exists public.verification_reviews (
    id                  uuid primary key default gen_random_uuid(),

    -- A2A identifiers. a2a_task_id is the idempotency key: one final review per task.
    a2a_task_id         text        not null,
    a2a_context_id      text,

    -- Final A2A task state and the verifier's overall result.
    task_state          text        not null,
    verification_status text        not null,

    -- Invoice metadata (null when absent from the request or when the request was invalid).
    invoice_number      text,
    po_reference        text,       -- invoice.po_reference as submitted
    po_number           text,       -- purchase_order.po_number as submitted
    supplier_name       text,
    currency            text,
    invoice_total       numeric(26, 6),

    -- Structured result, matching Agent B's verification_result artifact.
    summary             text        not null,
    findings            jsonb       not null default '[]'::jsonb,
    validation_errors   jsonb       not null default '[]'::jsonb,
    assumptions         jsonb       not null default '[]'::jsonb,

    created_at          timestamptz not null default now(),

    constraint verification_reviews_a2a_task_id_key unique (a2a_task_id),
    constraint verification_reviews_task_id_not_blank check (length(btrim(a2a_task_id)) > 0),
    constraint verification_reviews_task_state_check
        check (task_state in ('COMPLETED', 'REJECTED')),
    constraint verification_reviews_status_check
        check (verification_status in ('MATCH', 'DISCREPANCY_FOUND', 'NEEDS_REVIEW', 'INVALID_REQUEST')),
    -- Malformed requests are REJECTED tasks; every other result is a COMPLETED task.
    constraint verification_reviews_state_matches_status
        check ((task_state = 'REJECTED') = (verification_status = 'INVALID_REQUEST')),
    constraint verification_reviews_currency_format
        check (currency is null or currency ~ '^[A-Z]{3}$'),
    constraint verification_reviews_invoice_total_non_negative
        check (invoice_total is null or invoice_total >= 0),
    constraint verification_reviews_findings_is_array
        check (jsonb_typeof(findings) = 'array'),
    constraint verification_reviews_validation_errors_is_array
        check (jsonb_typeof(validation_errors) = 'array'),
    constraint verification_reviews_assumptions_is_array
        check (jsonb_typeof(assumptions) = 'array')
);

comment on table public.verification_reviews is
    'Final invoice-verification reviews from NexusAI Agent B, one per A2A task. Not a payment approval.';
comment on column public.verification_reviews.a2a_task_id is
    'A2A task ID returned to the client; unique idempotency key.';
comment on column public.verification_reviews.verification_status is
    'MATCH | DISCREPANCY_FOUND | NEEDS_REVIEW | INVALID_REQUEST';

create index if not exists verification_reviews_created_at_idx
    on public.verification_reviews (created_at desc);
create index if not exists verification_reviews_status_created_at_idx
    on public.verification_reviews (verification_status, created_at desc);
create index if not exists verification_reviews_invoice_number_idx
    on public.verification_reviews (invoice_number)
    where invoice_number is not null;

alter table public.verification_reviews enable row level security;
revoke all on table public.verification_reviews from anon, authenticated;

commit;
