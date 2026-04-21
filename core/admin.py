from django.contrib import admin

from .models import (
    ApolloCreditLog,
    AuditLog,
    Campaign,
    Lead,
    OptOut,
    OutreachEvent,
    PartnerProfile,
    Project,
    ProjectLead,
    StudyAsset,
)


@admin.register(ApolloCreditLog)
class ApolloCreditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'endpoint', 'credits', 'user', 'notes')
    list_filter = ('endpoint',)
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('study_code', 'name', 'status', 'monday_board_id', 'created_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('study_code', 'name', 'horizon_study_id', 'monday_board_id')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(StudyAsset)
class StudyAssetAdmin(admin.ModelAdmin):
    list_display = ('project', 'type', 'subject', 'approved_at')
    list_filter = ('type',)
    search_fields = ('project__study_code', 'subject')


@admin.register(PartnerProfile)
class PartnerProfileAdmin(admin.ModelAdmin):
    list_display = ('project', 'partner_type', 'target_size')
    list_filter = ('partner_type',)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'email', 'organization', 'specialty', 'source', 'global_opt_out')
    list_filter = ('source', 'enrichment_status', 'global_opt_out')
    search_fields = ('first_name', 'last_name', 'email', 'npi', 'organization', 'specialty')


@admin.register(ProjectLead)
class ProjectLeadAdmin(admin.ModelAdmin):
    list_display = ('project', 'lead', 'campaign_status', 'monday_item_id', 'referred_count', 'enrolled_count')
    list_filter = ('campaign_status',)
    search_fields = ('project__study_code', 'lead__email', 'tracking_token')


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ('project', 'name', 'status', 'instantly_campaign_id', 'started_at')
    list_filter = ('status',)


@admin.register(OutreachEvent)
class OutreachEventAdmin(admin.ModelAdmin):
    list_display = ('project_lead', 'event_type', 'timestamp', 'synced_to_monday')
    list_filter = ('event_type', 'synced_to_monday')
    date_hierarchy = 'timestamp'


@admin.register(OptOut)
class OptOutAdmin(admin.ModelAdmin):
    list_display = ('email', 'source', 'reason', 'created_at')
    list_filter = ('source',)
    search_fields = ('email',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action', 'entity_type', 'entity_id', 'user', 'timestamp')
    list_filter = ('action', 'entity_type')
    search_fields = ('entity_id', 'action')
    date_hierarchy = 'timestamp'
    readonly_fields = ('before_state', 'after_state', 'timestamp')
