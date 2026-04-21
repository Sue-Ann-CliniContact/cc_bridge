# CliniContact Bridge — Build Scope

**Product name:** CliniContact Bridge
**Owner:** Sue-Ann, CliniContact
**Position in product suite:** Horizon → Launch → **Bridge** → Smart Screener → Vision
**Purpose of this doc:** A buildable scope and architecture for Bridge — the partner outreach and community sourcing layer that sits between Launch (support/billing) and Smart Screener (patient screening) in the CliniContact study lifecycle.

---

## 1. What We're Building

**CliniContact Bridge** is a **project-based partner outreach platform** that:

1. Lets the CliniContact team set up a new "outreach project" per study.
2. Accepts approved study assets (email copy, flyer, landing page URL) as inputs.
3. Sources leads from **directories of medical professionals and patient/support organizations** (NOT ClinicalTrials.gov — we are the sponsor reaching out, not scraping trials).
4. Enriches leads with emails (Apollo API fallback when direct sources don't include contact info).
5. Builds an Instantly campaign using the approved assets and pushes leads into it. Claude drafts the actual email subject/body per sequence step from the approved source material; humans approve drafts before launch (IRB compliance).
6. Tracks every outreach event (sent, opened, replied, clicked, bounced, opted out) against a **project-specific Monday.com board**.
7. Maintains a **persistent, global leads database** across projects — so Bridge builds institutional memory of good partners, opt-outs, and previous outreach history.
8. Hands interested partners off to **Smart Screener** via the existing unique-link mechanism, where patient-level data flows onward to Vision.

Bridge ends at the Smart Screener handoff — no PHI lives in Bridge.

---

## 2. Core User Flow

```
1. User creates a new Project (tied to a specific study).
      │
      ▼
2. User uploads approved assets:
      - Email copy (subject + body, possibly a 2-3 step sequence)
      - Flyer (PDF/image)
      - Landing page URL
      - Study summary / target partner persona
      │
      ▼
3. User defines target partner profile:
      - Specialty / therapeutic area (ICD-10, MeSH, or free-text tags)
      - Geography (national, state, radius around sites)
      - Partner type (clinician, support group, advocacy org, research coordinator)
      │
      ▼
4. System sources leads from configured directories:
      - NPI Registry (primary, free)
      - Curated support-group/advocacy-org database (internal)
      - Apollo (enrichment + discovery fallback)
      │
      ▼
5. System cross-checks against global leads DB:
      - Dedupes against existing contacts
      - Filters out opt-outs and do-not-contact flags
      - Flags partners with prior positive engagement
      │
      ▼
6. User reviews lead list, approves/trims.
      │
      ▼
7. Claude drafts email subject + body per sequence step from the
   approved source material; human reviewer approves before
   campaign launches.
      │
      ▼
8. System creates Instantly campaign, pushes leads, starts sequence.
      │
      ▼
9. Instantly webhooks stream events back to our system.
      │
      ▼
10. Events sync to the project's Monday.com board AND are written
    to our global leads DB.
      │
      ▼
11. User sees live dashboard: send rate, open rate, reply rate,
    interested partners, opt-outs — all per project.
```

---

## 3. Tech Stack (Locked)

| Layer | Choice | Notes |
|---|---|---|
| Language / framework | **Django 5.2 (Python)** | Matches Vision for product-suite consistency |
| Database | **Postgres** via `dj_database_url` | Same pattern as Vision |
| Templates / UI | **Django templates + Tailwind CDN** | CC color palette shared with Vision |
| Auth | **Monday.com OAuth** for both internal and external users | Ported from Vision; Monday permissions define what a user sees |
| AI | **Anthropic Claude** via official SDK | Swappable through `AIProvider` model |
| Email sending | **Instantly.ai API** | Endpoints and payload shapes ported from the lead-scorer tool |
| Deploy | **Render** (Python runtime) | `build.sh` + `render.yaml`, WhiteNoise for static |
| Jobs | **Django management commands + Render cron** | Add Celery/Redis later if needed |

---

## 4. Integrations

### 4.1 Instantly API
Endpoints and payload shapes ported verbatim from `_reference/InstantlyLeadsICP-.../server/routes/instantly.js`:
- **Campaign creation** — create a campaign with name, schedule, sending account.
- **Lead push** — add leads with custom variables (first_name, last_name, organisation, plus `project_id`, `lead_id`, `tracking_token`).
- **Webhooks** — receive `email_sent`, `email_opened`, `email_clicked`, `email_replied`, `email_bounced`, `lead_unsubscribed`.
- **Analytics sync** — weekly pull of per-campaign metrics.

Campaign naming convention: `{ProjectCode}_{StudyAcronym}_{Date}` so events can be traced back.
Custom variables must include `project_id` and a Bridge-generated `tracking_token` so webhook events map back to the right ProjectLead.

### 4.2 Monday.com
**Auth:** Monday OAuth (`accounts.monday_login` / `accounts.monday_callback`) — identical flow to Vision. Users log in with their Monday account; the stored `access_token` on `ClientProfile` drives board-level permissions.

**Board provisioning:** Hybrid — Bridge has a dedicated workspace (set via `MONDAY_WORKSPACE_ID`). On project creation, Claude proposes study-specific columns on top of a baseline template; a human approves; the Monday client auto-creates the project's board.

**Required column schema (baseline):**
| Column | Type | Source |
|---|---|---|
| Contact Name | Text | Lead source |
| Organization | Text | Lead source |
| Role / Specialty | Text | Lead source |
| Email | Email | Lead source / Apollo |
| Source Directory | Status | NPI / Apollo / Manual / etc. |
| Campaign Status | Status | Instantly event |
| Last Event | Date | Instantly webhook |
| Interest Level | Status | Manual / AI-classified from reply |
| Referral Link | Text | `bridge.yoursite/r/{tracking_token}` |
| Referred Count | Numbers | Incremented on `landing_page_view` + `screener_submitted` |
| Notes | Long Text | Manual |

### 4.3 Apollo
- **Enrichment** via `people/match` (name + org → verified email).
- **Discovery** via `people/search` (specialty + geography filters).
- Feature-flagged by `APOLLO_API_KEY`; budget capped by `APOLLO_MONTHLY_BUDGET_CREDITS`.

### 4.4 Lead Source Directories
| Source | Access | Cost |
|---|---|---|
| NPI Registry (CMS) | Free public API | Free |
| Apollo | Paid API | Per credit |
| Internal curated DB | Manual + AI-assisted (Claude proposes → human reviews) | Labor |
| ClinicalTrials.gov (investigator lookup) | Free API | Free |

**Not using:** Doximity (no public API), LinkedIn (TOS prohibits scraping). Use Apollo's LinkedIn-sourced data where needed.

### 4.5 UTM / Click-through Tracking
Each `ProjectLead` gets a `tracking_token`. Email templates link to `{APP_BASE_URL}/r/{tracking_token}`; Bridge logs a `landing_page_view` OutreachEvent and redirects to the real landing page with UTM params (`utm_source=bridge&utm_campaign={study_code}&utm_content={tracking_token}`). Smart Screener reads `utm_content` on form submission and passes it to Monday for attribution.

---

## 5. Data Model

See `core/models.py`. Summary:

- **Project** — one per study (name, study_code, horizon_study_id, monday_board_id, status, created_by).
- **StudyAsset** — approved email copy, flyer, landing page URL, study summary (type, content_text/url/file, subject, approved_by, approved_at).
- **PartnerProfile** — targeting config per project (partner_type, specialty_tags, icd10_codes, geography, target_size).
- **Lead** — **global across projects** (first/last name, email, phone, npi, organization, role, specialty, geography, source, enrichment_status, global_opt_out, do_not_contact_reason, quality_score). Email and NPI are partial-unique.
- **ProjectLead** — per-project join (project, lead, campaign_status, instantly_lead_id, monday_item_id, tracking_token, referred_count, enrolled_count). Unique on (project, lead).
- **Campaign** — instantly_campaign_id, name, sequence_config (JSON — subject/body/delay per step), status, started_at.
- **OutreachEvent** — project_lead, event_type, timestamp, raw_payload, synced_to_monday. Indexed on (project_lead, event_type), timestamp, synced_to_monday.
- **OptOut** — **absolute** per-email exclusion (email unique, reason, source). Filters all future sourcing.
- **AuditLog** — user, action, entity_type, entity_id, before_state, after_state. Required for clinical-trial compliance.

---

## 6. Phased Build Plan

### Phase 1 — Foundation (Weeks 1-2) [THIS BUILD]
- Django project + Postgres + Render deploy
- Monday OAuth login flow (ported from Vision)
- Project CRUD + asset upload
- DB schema + admin interface
- Port Instantly HTTP client to Python
- "Test Instantly connection" sanity check on the project detail page
- ai_manager with Anthropic Claude (text-only, used by email drafting in Phase 3)
- CC color palette + base template matching Vision

**Deliverable:** A deploy-able app where an authenticated user creates a project, uploads assets, and verifies the Instantly API key is working.

### Phase 2 — Lead Sourcing (Weeks 3-4)
- NPI Registry integration with specialty/geography filters
- Apollo integration (enrichment + discovery), budget-capped
- Lead dedupe + global opt-out filtering
- Manual CSV import
- AI-assisted support-group discovery: Claude proposes national advocacy orgs for the partner profile → CSV review → import
- Partner profile UI

### Phase 3 — Campaign Push to Instantly (Weeks 5-6)
- Campaign builder UI
- **Claude drafts email subject/body per sequence step** from approved source material
- Human approval workflow (each draft must be approved before send; logged in AuditLog)
- Instantly campaign creation via Python client; lead push with `tracking_token` in custom variables
- Campaign status dashboard

### Phase 4 — Monday.com Sync via MCP (Weeks 7-8)
- Instantly webhook receiver
- Monday service wrapper (GraphQL today; MCP-ready interface)
- Event → Monday mapping logic (`create_item` on first send, `update_item` on every event)
- Retry queue for failed Monday syncs
- Reconciliation job (Monday = downstream view; DB is source of truth)
- Two-way sync: "Not Interested" on Monday pauses the Instantly sequence

### Phase 5 — Dashboard, Opt-Outs, Quality Scoring (Weeks 9-10)
- Project-level analytics
- Global leads DB view — search, filter by engagement history
- Opt-out management (one-click global opt-out)
- Quality score: `(enrolled / referred) × engagement_frequency`
- CAN-SPAM compliance check

### Phase 6 — Optional / Later
- Lini chat for Bridge dashboard (port Vision's tool-use pattern, Claude-backed)
- AI reply classification ("interested" / "not interested" / "OOO")
- Partner portal (partners log in to see their referrals' status)
- Referral milestone triggers

---

## 7. Compliance

- **HIPAA:** No PHI in Bridge. Partner data is not PHI. Never pull patient data back from Smart Screener/Vision.
- **CAN-SPAM:** Unsubscribe link on every email (Instantly handles). Opt-outs honored within 10 business days — `OptOut` table is the enforcement mechanism.
- **GDPR:** Default US-only for Phase 1. EU contact requires documented legitimate interest.
- **IRB:** Only IRB-approved source material. Claude-drafted emails must be approved by a human before send (`StudyAsset.approved_by` + `AuditLog`).
- **Audit log:** Every email sent, every status change, every Monday write — logged.

---

## 8. Ecosystem Position

```
Horizon          →  Study scoped, priced, materials created
    │
    ▼
Launch           →  Study kicked off, billing & access provisioned
    │
    ▼
BRIDGE           →  Partner outreach: source, contact, track (this product)
    │
    ▼
Smart Screener   →  Partner-referred patients screened via unique link
    │
    ▼
Vision           →  Enrolled participants managed in Monday.com
```

---

## 9. Environment

See `.env.example`. Required for local dev:
- `DJANGO_SECRET_KEY`, `DEBUG`, `DB_INTERNAL`
- `MONDAY_CLIENT_ID`, `MONDAY_CLIENT_SECRET`, `MONDAY_REDIRECT_URI`, `MONDAY_WORKSPACE_ID`
- `INSTANTLY_API_KEY`
- `ANTHROPIC_API_KEY` (optional for Phase 1; required for Phase 3)
- `APOLLO_API_KEY` (Phase 2)
