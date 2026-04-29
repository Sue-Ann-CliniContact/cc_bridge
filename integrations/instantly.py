"""Instantly.ai API client — Python port of the original instantlyApi.js + route logic.

Endpoint paths, payload shapes, and behavior intentionally match the JS reference at
_reference/InstantlyLeadsICP-.../server/lib/instantlyApi.js so we can diff against
the known-working original.
"""
from __future__ import annotations

import re
import logging
import time
from html import escape
from typing import Iterable

import requests
from django.conf import settings

log = logging.getLogger(__name__)

INSTANTLY_V1 = 'https://api.instantly.ai/api/v1'
INSTANTLY_V2 = 'https://api.instantly.ai/api/v2'
BATCH_SIZE = 50
DEFAULT_INSTANTLY_TIMEZONE = 'America/Detroit'
INSTANTLY_ALLOWED_TIMEZONES = {
    'Etc/GMT+12', 'Etc/GMT+11', 'Etc/GMT+10', 'America/Anchorage', 'America/Dawson',
    'America/Creston', 'America/Chihuahua', 'America/Boise', 'America/Belize', 'America/Chicago',
    'America/Bahia_Banderas', 'America/Regina', 'America/Bogota', 'America/Detroit',
    'America/Indiana/Marengo', 'America/Caracas', 'America/Asuncion', 'America/Glace_Bay',
    'America/Campo_Grande', 'America/Anguilla', 'America/Santiago', 'America/St_Johns',
    'America/Sao_Paulo', 'America/Argentina/La_Rioja', 'America/Araguaina', 'America/Godthab',
    'America/Montevideo', 'America/Bahia', 'America/Noronha', 'America/Scoresbysund',
    'Atlantic/Cape_Verde', 'Africa/Casablanca', 'America/Danmarkshavn', 'Europe/Isle_of_Man',
    'Atlantic/Canary', 'Africa/Abidjan', 'Arctic/Longyearbyen', 'Europe/Belgrade', 'Africa/Ceuta',
    'Europe/Sarajevo', 'Africa/Algiers', 'Africa/Windhoek', 'Asia/Nicosia', 'Asia/Beirut',
    'Africa/Cairo', 'Asia/Damascus', 'Europe/Bucharest', 'Africa/Blantyre', 'Europe/Helsinki',
    'Europe/Istanbul', 'Asia/Jerusalem', 'Africa/Tripoli', 'Asia/Amman', 'Asia/Baghdad',
    'Europe/Kaliningrad', 'Asia/Aden', 'Africa/Addis_Ababa', 'Europe/Kirov', 'Europe/Astrakhan',
    'Asia/Tehran', 'Asia/Dubai', 'Asia/Baku', 'Indian/Mahe', 'Asia/Tbilisi', 'Asia/Yerevan',
    'Asia/Kabul', 'Antarctica/Mawson', 'Asia/Yekaterinburg', 'Asia/Karachi', 'Asia/Kolkata',
    'Asia/Colombo', 'Asia/Kathmandu', 'Antarctica/Vostok', 'Asia/Dhaka', 'Asia/Rangoon',
    'Antarctica/Davis', 'Asia/Novokuznetsk', 'Asia/Hong_Kong', 'Asia/Krasnoyarsk', 'Asia/Brunei',
    'Australia/Perth', 'Asia/Taipei', 'Asia/Choibalsan', 'Asia/Irkutsk', 'Asia/Dili',
    'Asia/Pyongyang', 'Australia/Adelaide', 'Australia/Darwin', 'Australia/Brisbane',
    'Australia/Melbourne', 'Antarctica/DumontDUrville', 'Australia/Currie', 'Asia/Chita',
    'Antarctica/Macquarie', 'Asia/Sakhalin', 'Pacific/Auckland', 'Etc/GMT-12', 'Pacific/Fiji',
    'Asia/Anadyr', 'Asia/Kamchatka', 'Etc/GMT-13', 'Pacific/Apia',
}
INSTANTLY_TIMEZONE_ALIASES = {
    'America/New_York': 'America/Detroit',
    'US/Eastern': 'America/Detroit',
    'EST': 'America/Detroit',
    'America/Los_Angeles': 'America/Boise',
    'US/Pacific': 'America/Boise',
    'America/Denver': 'America/Boise',
    'US/Mountain': 'America/Boise',
    'America/Phoenix': 'America/Creston',
    'America/Mexico_City': 'America/Chicago',
    'US/Central': 'America/Chicago',
    'Africa/Johannesburg': 'Africa/Blantyre',
    'Europe/London': 'Europe/Isle_of_Man',
}
DEFAULT_OUTREACH_SIGNATURE = """Louis De Souza
Community Outreach Specialist
CliniContact
Accelerating research through adaptive participant recruitment.
P +1 (267) 295-4353
W clinicontact.com
Visit CliniContact: https://www.clinicontact.com"""


def _get_key(api_key: str | None = None) -> str:
    key = api_key or settings.INSTANTLY_API_KEY
    if not key:
        raise RuntimeError('INSTANTLY_API_KEY is not configured')
    return key


def _instantly_timezone() -> str:
    configured = (
        getattr(settings, 'INSTANTLY_TIMEZONE', '')
        or getattr(settings, 'TIME_ZONE', '')
        or DEFAULT_INSTANTLY_TIMEZONE
    )
    candidate = INSTANTLY_TIMEZONE_ALIASES.get(configured, configured)
    if candidate in INSTANTLY_ALLOWED_TIMEZONES:
        return candidate
    return DEFAULT_INSTANTLY_TIMEZONE


def _outreach_signature() -> str:
    return (getattr(settings, 'OUTREACH_EMAIL_SIGNATURE', '') or DEFAULT_OUTREACH_SIGNATURE).strip()


def _strip_generic_signature(body: str) -> str:
    patterns = [
        r'\n\s*Best,?\s*\n\s*The CliniContact team\s*$',
        r'\n\s*Best regards,?\s*\n\s*The CliniContact team\s*$',
        r'\n\s*Sincerely,?\s*\n\s*The CliniContact team\s*$',
        r'\s+Best,?\s+The CliniContact team\s*$',
        r'\s+Best regards,?\s+The CliniContact team\s*$',
        r'\s+Sincerely,?\s+The CliniContact team\s*$',
    ]
    cleaned = body.strip()
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _linkify_escaped_line(line: str) -> str:
    return re.sub(
        r'(https?://[^\s<]+|www\.[^\s<]+|\bclinicontact\.com\b)',
        lambda match: f'<a href="{match.group(0) if match.group(0).startswith("http") else "https://" + match.group(0)}">{match.group(0)}</a>',
        line,
    )


def _format_email_body(body: str) -> str:
    cleaned = _strip_generic_signature(body or '')
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r'\n\s*\n', cleaned)
        if paragraph.strip()
    ]
    if len(paragraphs) == 1 and len(paragraphs[0]) > 650:
        paragraphs = _split_dense_paragraph(paragraphs[0])
    html_parts = []
    for paragraph in paragraphs:
        lines = [_linkify_escaped_line(escape(line.strip())) for line in paragraph.splitlines() if line.strip()]
        if lines:
            html_parts.append(f'<p>{"<br>".join(lines)}</p>')

    signature_lines = [_linkify_escaped_line(escape(line.strip())) for line in _outreach_signature().splitlines() if line.strip()]
    if signature_lines:
        html_parts.append(f'<p>{"<br>".join(signature_lines)}</p>')
    return '\n'.join(html_parts)


def _split_dense_paragraph(text: str) -> list[str]:
    """Repair older AI drafts that were stored as one long run-on paragraph."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    paragraphs: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and current_len + len(sentence) > 430:
            paragraphs.append(' '.join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence) + 1
    if current:
        paragraphs.append(' '.join(current))
    return paragraphs or [text.strip()]


def _sequence_payload_steps(sequence_steps: list[dict]) -> list[dict]:
    return [
        {
            'type': 'email',
            'delay': int(step.get('delay_days') or 0),
            'variants': [{
                'subject': step.get('subject') or '',
                'body': _format_email_body(step.get('body') or ''),
            }],
        }
        for step in sequence_steps
    ]


def ping(api_key: str | None = None) -> dict:
    """Lightweight connection check — fetches the first page of campaigns."""
    try:
        key = _get_key(api_key)
    except RuntimeError as exc:
        return {'ok': False, 'error': str(exc)}

    try:
        campaigns = get_campaigns(key)
        return {'ok': True, 'campaigns_count': len(campaigns), 'sample': campaigns[:3]}
    except Exception as exc:  # noqa: BLE001 — we want to surface any failure
        return {'ok': False, 'error': str(exc)}


def get_sending_accounts(api_key: str | None = None) -> list[dict]:
    """Fetch the workspace's configured sending accounts (Gmail/Outlook/SMTP)."""
    key = _get_key(api_key)
    try:
        r = requests.get(
            f'{INSTANTLY_V2}/accounts',
            params={'limit': 100},
            headers={'Authorization': f'Bearer {key}'},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else (data.get('items') or data.get('data') or [])
        return [
            {
                'email': a.get('email') or a.get('sending_account_email') or '',
                'status': a.get('status', ''),
                'daily_limit': a.get('daily_limit', 0),
            }
            for a in items
            if (a.get('email') or a.get('sending_account_email'))
        ]
    except requests.RequestException as exc:
        log.warning('Failed to fetch Instantly sending accounts: %s', exc)
        return []


def create_campaign(
    *,
    name: str,
    sequence_steps: list[dict],
    sending_account_emails: list[str],
    api_key: str | None = None,
    daily_max_leads: int = 30,
) -> dict:
    """Create an Instantly v2 campaign with a multi-step email sequence.

    sequence_steps: [{subject, body, delay_days}, ...] — same shape Bridge stores.
    sending_account_emails: ["user@example.com", ...] — pulled from get_sending_accounts.

    Returns {'id': campaign_id, 'raw': full_response} on success.
    """
    key = _get_key(api_key)
    if not sequence_steps:
        raise RuntimeError('Cannot create campaign with empty sequence')
    if not sending_account_emails:
        raise RuntimeError('At least one sending account email is required')

    steps = _sequence_payload_steps(sequence_steps)

    payload = {
        'name': name,
        'campaign_schedule': {
            'schedules': [
                {
                    'name': 'Business hours (M-F, 9-5 ET)',
                    'timing': {'from': '09:00', 'to': '17:00'},
                    'days': {'0': False, '1': True, '2': True, '3': True, '4': True, '5': True, '6': False},
                    'timezone': _instantly_timezone(),
                }
            ],
        },
        'sequences': [{'steps': steps}],
        'email_gap_min': 10,
        'email_gap_max': 15,
        'daily_max_leads': daily_max_leads,
        'link_tracking': True,
        'open_tracking': True,
        'stop_on_reply': True,
        'stop_on_auto_reply': True,
        'email_list': sending_account_emails,
    }

    r = requests.post(
        f'{INSTANTLY_V2}/campaigns',
        json=payload,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f'Instantly campaign create {r.status_code}: {r.text[:500]}')
    data = r.json() or {}
    campaign_id = data.get('id') or (data.get('data') or {}).get('id')
    if not campaign_id:
        raise RuntimeError(f'Instantly campaign create: no id in response {str(data)[:300]}')
    return {'id': campaign_id, 'raw': data}


def update_campaign_sequence(
    *,
    campaign_id: str,
    sequence_steps: list[dict],
    api_key: str | None = None,
) -> dict:
    """Patch an existing Instantly campaign sequence with formatted Bridge copy."""
    key = _get_key(api_key)
    if not campaign_id:
        raise RuntimeError('campaign_id is required')
    if not sequence_steps:
        raise RuntimeError('Cannot update campaign with empty sequence')

    payload = {'sequences': [{'steps': _sequence_payload_steps(sequence_steps)}]}
    r = requests.patch(
        f'{INSTANTLY_V2}/campaigns/{campaign_id}',
        json=payload,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f'Instantly campaign update {r.status_code}: {r.text[:500]}')
    return r.json() or {}


def get_campaigns(api_key: str | None = None) -> list[dict]:
    """Fetch campaigns. Tries v2 (Bearer auth), falls back to v1 (api_key query param)."""
    key = _get_key(api_key)

    try:
        r = requests.get(
            f'{INSTANTLY_V2}/campaigns',
            params={'limit': 100},
            headers={'Authorization': f'Bearer {key}'},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else (data.get('items') or data.get('data') or [])
        return [{'id': c.get('id'), 'name': c.get('name'), 'status': c.get('status')} for c in items]
    except requests.RequestException as v2_err:
        try:
            r = requests.get(
                f'{INSTANTLY_V1}/campaign/list',
                params={'api_key': key, 'limit': 100},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json() or {}
            items = data.get('data') or data or []
            return [{'id': c.get('id'), 'name': c.get('name'), 'status': c.get('status')} for c in items]
        except requests.RequestException as v1_err:
            raise RuntimeError(f'Failed to fetch campaigns: v2={v2_err}; v1={v1_err}') from v1_err


def push_leads(
    *,
    leads: Iterable[dict],
    campaign_id: str,
    api_key: str | None = None,
) -> dict:
    """Push leads to an Instantly campaign.

    Each lead dict must include at minimum `email`. Optional fields mirror the
    JS reference: first_name, last_name, organisation, plus anything the caller
    wants under `custom_variables`.
    """
    key = _get_key(api_key)

    leads_list = list(leads)
    valid = [l for l in leads_list if l.get('email') and '@' in l['email']]
    skipped = len(leads_list) - len(valid)
    if not valid:
        return {'pushed': 0, 'skipped': skipped, 'errors': []}

    results = {'pushed': 0, 'skipped': skipped, 'errors': []}
    for i in range(0, len(valid), BATCH_SIZE):
        batch = valid[i : i + BATCH_SIZE]
        payload = {
            'campaign_id': campaign_id,
            'leads': [
                {
                    'email': lead['email'],
                    'first_name': lead.get('first_name', ''),
                    'last_name': lead.get('last_name', ''),
                    'company_name': lead.get('organisation', '') or lead.get('organization', ''),
                    'custom_variables': lead.get('custom_variables', {}),
                }
                for lead in batch
            ],
        }
        try:
            r = requests.post(
                f'{INSTANTLY_V2}/leads/add',
                json=payload,
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                timeout=30,
            )
            if r.status_code >= 400:
                results['errors'].append(f'Batch {i // BATCH_SIZE + 1}: {r.status_code} {r.text[:500]}')
                continue
            data = r.json() or {}
            uploaded = data.get('leads_uploaded')
            results['pushed'] += int(uploaded) if uploaded is not None else len(batch)
            skipped_count = int(data.get('skipped_count') or 0)
            invalid_count = int(data.get('invalid_email_count') or 0)
            duplicate_count = int(data.get('duplicated_leads') or data.get('duplicate_email_count') or 0)
            incomplete_count = int(data.get('incomplete_count') or 0)
            results['skipped'] += skipped_count + invalid_count + duplicate_count + incomplete_count
        except requests.RequestException as exc:
            results['errors'].append(f'Batch {i // BATCH_SIZE + 1}: {exc}')

        if i + BATCH_SIZE < len(valid):
            time.sleep(0.5)

    return results


def get_campaign_analytics(
    api_key: str,
    campaign_ids: list[str],
    start_date: str,
    end_date: str,
) -> dict:
    """Fetch analytics aggregated across tracked campaign IDs for a date range."""
    r = requests.get(
        f'{INSTANTLY_V2}/campaigns/analytics',
        params={'start_date': start_date, 'end_date': end_date},
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json() or {}
    all_campaigns = data if isinstance(data, list) else (data.get('data') or data.get('campaigns') or [])

    campaigns = [c for c in all_campaigns if (c.get('campaign_id') or c.get('id')) in campaign_ids] if campaign_ids else all_campaigns
    log.info('Instantly analytics: %s total, %s tracked, %s→%s', len(all_campaigns), len(campaigns), start_date, end_date)

    totals = {'emails_sent': 0, 'opened': 0, 'replied': 0, 'bounced': 0}
    for c in campaigns:
        totals['emails_sent'] += int(c.get('emails_sent_count') or c.get('emails_sent') or 0)
        totals['opened'] += int(c.get('open_count') or c.get('opened') or 0)
        totals['replied'] += int(c.get('reply_count') or c.get('replied') or 0)
        totals['bounced'] += int(c.get('bounced_count') or c.get('bounced') or 0)

    sent = totals['emails_sent'] or 1
    return {
        **totals,
        'api_errors': [],
        'open_rate': totals['opened'] / sent,
        'reply_rate': totals['replied'] / sent,
        'bounce_rate': totals['bounced'] / sent,
    }
