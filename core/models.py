"""Core Bridge data model — scope §5.

Leads are global across projects (institutional memory). ProjectLead is the
per-project join row carrying campaign state. OptOut is absolute — one row there
excludes the email from every future project (CAN-SPAM enforcement).
"""
import secrets

from django.conf import settings
from django.db import models


def new_tracking_token():
    return secrets.token_urlsafe(16)


class Project(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_SETUP = 'setup'
    STATUS_SOURCING = 'sourcing'
    STATUS_ACTIVE = 'active'
    STATUS_PAUSED = 'paused'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SETUP, 'Setup'),
        (STATUS_SOURCING, 'Sourcing Leads'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    name = models.CharField(max_length=255)
    study_code = models.CharField(max_length=100, unique=True, help_text='IRB / sponsor study code')
    horizon_study_id = models.CharField(max_length=100, blank=True, help_text='External reference to Horizon')
    monday_board_id = models.CharField(max_length=100, blank=True, help_text='Set after Monday board auto-creation')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='projects_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.study_code} — {self.name}'


class StudyAsset(models.Model):
    TYPE_EMAIL = 'email'
    TYPE_FLYER = 'flyer'
    TYPE_LANDING_PAGE = 'landing_page'
    TYPE_SUMMARY = 'summary'
    TYPE_CHOICES = [
        (TYPE_EMAIL, 'Email Copy'),
        (TYPE_FLYER, 'Flyer'),
        (TYPE_LANDING_PAGE, 'Landing Page URL'),
        (TYPE_SUMMARY, 'Study Summary'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='assets')
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    subject = models.CharField(max_length=255, blank=True, help_text='Email subject line (email assets only)')
    content_text = models.TextField(blank=True)
    content_url = models.URLField(max_length=1000, blank=True)
    content_file = models.FileField(upload_to='assets/%Y/%m/', blank=True, null=True)

    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['project', 'type', '-created_at']

    def __str__(self):
        return f'{self.project.study_code} / {self.get_type_display()}'

    @property
    def is_approved(self):
        return self.approved_at is not None


class PartnerProfile(models.Model):
    PARTNER_CLINICIAN = 'clinician'
    PARTNER_SUPPORT_GROUP = 'support_group'
    PARTNER_COORDINATOR = 'research_coordinator'
    PARTNER_INVESTIGATOR = 'investigator'
    PARTNER_TYPE_CHOICES = [
        (PARTNER_CLINICIAN, 'Clinician'),
        (PARTNER_SUPPORT_GROUP, 'Support Group / Advocacy Org'),
        (PARTNER_COORDINATOR, 'Research Coordinator'),
        (PARTNER_INVESTIGATOR, 'Investigator / PI'),
    ]

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='partner_profile')
    partner_type = models.CharField(max_length=30, choices=PARTNER_TYPE_CHOICES, default=PARTNER_CLINICIAN)

    # Narrow, indication-level context — what the study is actually recruiting for.
    # These drive AI org suggestions so we stop getting generic "cancer orgs" when
    # the study is specific to, say, HER2+ metastatic breast cancer.
    study_indication = models.CharField(
        max_length=500, blank=True,
        help_text='Specific clinical condition the study targets (e.g. "metastatic triple-negative breast cancer")',
    )
    patient_population_description = models.TextField(
        blank=True,
        help_text='2–4 sentences describing the target patients (disease stage, age, key inclusion, demographics)',
    )
    target_org_types = models.JSONField(
        default=list, blank=True,
        help_text='Categories of orgs to target (e.g. ["indication-specific patient advocacy", "NCI-designated cancer centers"])',
    )
    target_contact_roles = models.JSONField(
        default=list, blank=True,
        help_text='Specific titles we want to reach (e.g. ["Principal Investigator", "Clinical Research Coordinator"])',
    )

    # Broader taxonomy used for NPI sourcing + filtering
    specialty_tags = models.JSONField(default=list, blank=True, help_text='CMS NPI taxonomy names (e.g. "Medical Oncology")')
    icd10_codes = models.JSONField(default=list, blank=True)
    geography = models.JSONField(default=dict, blank=True, help_text="e.g. {'type':'national'} or {'type':'state','states':['NY']}")
    target_size = models.PositiveIntegerField(default=100)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.project.study_code} profile'


class Lead(models.Model):
    SOURCE_NPI = 'npi'
    SOURCE_APOLLO = 'apollo'
    SOURCE_AI_SUGGESTED = 'ai_suggested'
    SOURCE_CSV = 'csv_import'
    SOURCE_MANUAL = 'manual'
    SOURCE_CTGOV = 'ctgov'
    SOURCE_MONDAY = 'monday_import'
    SOURCE_CHOICES = [
        (SOURCE_NPI, 'NPI Registry'),
        (SOURCE_APOLLO, 'Apollo'),
        (SOURCE_AI_SUGGESTED, 'AI Suggested (pending review)'),
        (SOURCE_CSV, 'CSV Import'),
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_CTGOV, 'ClinicalTrials.gov'),
        (SOURCE_MONDAY, 'Monday Board Import'),
    ]

    ENRICHMENT_NEEDED = 'needed'
    ENRICHMENT_PENDING = 'pending'
    ENRICHMENT_COMPLETE = 'complete'
    ENRICHMENT_FAILED = 'failed'
    ENRICHMENT_CHOICES = [
        (ENRICHMENT_NEEDED, 'Needs enrichment'),
        (ENRICHMENT_PENDING, 'Enrichment in progress'),
        (ENRICHMENT_COMPLETE, 'Enrichment complete'),
        (ENRICHMENT_FAILED, 'Enrichment failed'),
    ]

    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(max_length=255, blank=True, null=True, db_index=True)
    phone = models.CharField(max_length=50, blank=True)
    npi = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    organization = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=255, blank=True)
    specialty = models.CharField(max_length=255, blank=True)
    contact_url = models.URLField(max_length=500, blank=True, help_text='Org website or contact-page link (used for AI-suggested orgs where humans need to find the email manually)')
    geography = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    enrichment_status = models.CharField(max_length=30, choices=ENRICHMENT_CHOICES, default=ENRICHMENT_COMPLETE)
    global_opt_out = models.BooleanField(default=False)
    do_not_contact_reason = models.CharField(max_length=255, blank=True)
    quality_score = models.FloatField(null=True, blank=True)
    pending_conflict = models.JSONField(
        null=True,
        blank=True,
        help_text='Set when an import matched this lead by email but had different name/org. '
                  'Resolve by merging or dismissing in the lead review UI.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['email'],
                condition=models.Q(email__isnull=False) & ~models.Q(email=''),
                name='lead_email_unique_when_present',
            ),
            models.UniqueConstraint(
                fields=['npi'],
                condition=models.Q(npi__isnull=False) & ~models.Q(npi=''),
                name='lead_npi_unique_when_present',
            ),
        ]

    def __str__(self):
        name = f'{self.first_name} {self.last_name}'.strip() or self.email or f'Lead #{self.pk}'
        return name


class ProjectLead(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_SENT = 'sent'
    STATUS_OPENED = 'opened'
    STATUS_CLICKED = 'clicked'
    STATUS_REPLIED = 'replied'
    STATUS_BOUNCED = 'bounced'
    STATUS_UNSUBSCRIBED = 'unsubscribed'
    STATUS_INTERESTED = 'interested'
    STATUS_NOT_INTERESTED = 'not_interested'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_SENT, 'Sent'),
        (STATUS_OPENED, 'Opened'),
        (STATUS_CLICKED, 'Clicked'),
        (STATUS_REPLIED, 'Replied'),
        (STATUS_BOUNCED, 'Bounced'),
        (STATUS_UNSUBSCRIBED, 'Unsubscribed'),
        (STATUS_INTERESTED, 'Interested'),
        (STATUS_NOT_INTERESTED, 'Not Interested'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='project_leads')
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='project_leads')
    campaign_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    instantly_lead_id = models.CharField(max_length=100, blank=True)
    monday_item_id = models.CharField(max_length=100, blank=True)
    tracking_token = models.CharField(max_length=64, unique=True, db_index=True, default=new_tracking_token)
    referred_count = models.PositiveIntegerField(default=0)
    enrolled_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['project', 'lead'], name='projectlead_project_lead_unique'),
        ]

    def __str__(self):
        return f'{self.project.study_code} / {self.lead}'


class Campaign(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_AWAITING_APPROVAL = 'awaiting_approval'
    STATUS_ACTIVE = 'active'
    STATUS_PAUSED = 'paused'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_AWAITING_APPROVAL, 'Awaiting Human Approval'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PAUSED, 'Paused'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='campaigns')
    instantly_campaign_id = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=255)
    sequence_config = models.JSONField(default=list, blank=True, help_text='Ordered list of steps: [{step, subject, body, delay_days}]')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    started_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.project.study_code} — {self.name}'


class OutreachEvent(models.Model):
    EVENT_EMAIL_SENT = 'email_sent'
    EVENT_EMAIL_OPENED = 'email_opened'
    EVENT_EMAIL_CLICKED = 'email_clicked'
    EVENT_EMAIL_REPLIED = 'email_replied'
    EVENT_EMAIL_BOUNCED = 'email_bounced'
    EVENT_UNSUBSCRIBED = 'lead_unsubscribed'
    EVENT_LANDING_PAGE_VIEW = 'landing_page_view'
    EVENT_SCREENER_SUBMITTED = 'screener_submitted'
    EVENT_TYPE_CHOICES = [
        (EVENT_EMAIL_SENT, 'Email Sent'),
        (EVENT_EMAIL_OPENED, 'Email Opened'),
        (EVENT_EMAIL_CLICKED, 'Email Clicked'),
        (EVENT_EMAIL_REPLIED, 'Email Replied'),
        (EVENT_EMAIL_BOUNCED, 'Email Bounced'),
        (EVENT_UNSUBSCRIBED, 'Lead Unsubscribed'),
        (EVENT_LANDING_PAGE_VIEW, 'Landing Page View'),
        (EVENT_SCREENER_SUBMITTED, 'Screener Submitted'),
    ]

    project_lead = models.ForeignKey(ProjectLead, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES)
    timestamp = models.DateTimeField()
    raw_payload = models.JSONField(default=dict, blank=True)
    synced_to_monday = models.BooleanField(default=False)
    synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['project_lead', 'event_type']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['synced_to_monday']),
        ]

    def __str__(self):
        return f'{self.event_type} @ {self.timestamp:%Y-%m-%d %H:%M}'


class OptOut(models.Model):
    SOURCE_INSTANTLY_WEBHOOK = 'instantly_webhook'
    SOURCE_MANUAL = 'manual'
    SOURCE_REPLY_CLASSIFIED = 'reply_classified'
    SOURCE_CHOICES = [
        (SOURCE_INSTANTLY_WEBHOOK, 'Instantly webhook'),
        (SOURCE_MANUAL, 'Manual'),
        (SOURCE_REPLY_CLASSIFIED, 'Reply classified as opt-out'),
    ]

    email = models.EmailField(max_length=255, unique=True, db_index=True)
    reason = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.email


class ApolloCreditLog(models.Model):
    """Audit trail of Apollo API calls for monthly-budget tracking."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    endpoint = models.CharField(max_length=100)
    credits = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['created_at'])]

    def __str__(self):
        return f'{self.endpoint} · {self.credits} cr · {self.created_at:%Y-%m-%d}'


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    action = models.CharField(max_length=100, help_text="e.g. 'project.create', 'campaign.launch'")
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=100)
    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['user', 'timestamp']),
        ]

    def __str__(self):
        return f'{self.action} {self.entity_type}#{self.entity_id}'
