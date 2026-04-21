import core.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('study_code', models.CharField(help_text='IRB / sponsor study code', max_length=100, unique=True)),
                ('horizon_study_id', models.CharField(blank=True, help_text='External reference to Horizon', max_length=100)),
                ('monday_board_id', models.CharField(blank=True, help_text='Set after Monday board auto-creation', max_length=100)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('setup', 'Setup'), ('sourcing', 'Sourcing Leads'), ('active', 'Active'), ('paused', 'Paused'), ('completed', 'Completed')], default='draft', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='projects_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='Lead',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_name', models.CharField(blank=True, max_length=150)),
                ('last_name', models.CharField(blank=True, max_length=150)),
                ('email', models.EmailField(blank=True, db_index=True, max_length=255, null=True)),
                ('phone', models.CharField(blank=True, max_length=50)),
                ('npi', models.CharField(blank=True, db_index=True, max_length=20, null=True)),
                ('organization', models.CharField(blank=True, max_length=255)),
                ('role', models.CharField(blank=True, max_length=255)),
                ('specialty', models.CharField(blank=True, max_length=255)),
                ('geography', models.JSONField(blank=True, default=dict)),
                ('source', models.CharField(choices=[('npi', 'NPI Registry'), ('apollo', 'Apollo'), ('ai_suggested', 'AI Suggested (pending review)'), ('csv_import', 'CSV Import'), ('manual', 'Manual'), ('ctgov', 'ClinicalTrials.gov')], default='manual', max_length=30)),
                ('enrichment_status', models.CharField(choices=[('needed', 'Needs enrichment'), ('pending', 'Enrichment in progress'), ('complete', 'Enrichment complete'), ('failed', 'Enrichment failed')], default='complete', max_length=30)),
                ('global_opt_out', models.BooleanField(default=False)),
                ('do_not_contact_reason', models.CharField(blank=True, max_length=255)),
                ('quality_score', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='lead',
            constraint=models.UniqueConstraint(
                condition=models.Q(('email__isnull', False), models.Q(('email', '')), _negated=True),
                fields=('email',),
                name='lead_email_unique_when_present',
            ),
        ),
        migrations.AddConstraint(
            model_name='lead',
            constraint=models.UniqueConstraint(
                condition=models.Q(('npi__isnull', False), models.Q(('npi', '')), _negated=True),
                fields=('npi',),
                name='lead_npi_unique_when_present',
            ),
        ),
        migrations.CreateModel(
            name='StudyAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('email', 'Email Copy'), ('flyer', 'Flyer'), ('landing_page', 'Landing Page URL'), ('summary', 'Study Summary')], max_length=20)),
                ('subject', models.CharField(blank=True, help_text='Email subject line (email assets only)', max_length=255)),
                ('content_text', models.TextField(blank=True)),
                ('content_url', models.URLField(blank=True, max_length=1000)),
                ('content_file', models.FileField(blank=True, null=True, upload_to='assets/%Y/%m/')),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='assets', to='core.project')),
            ],
            options={'ordering': ['project', 'type', '-created_at']},
        ),
        migrations.CreateModel(
            name='PartnerProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('partner_type', models.CharField(choices=[('clinician', 'Clinician'), ('support_group', 'Support Group / Advocacy Org'), ('research_coordinator', 'Research Coordinator'), ('investigator', 'Investigator / PI')], default='clinician', max_length=30)),
                ('specialty_tags', models.JSONField(blank=True, default=list, help_text='List of therapeutic-area / specialty tags')),
                ('icd10_codes', models.JSONField(blank=True, default=list)),
                ('geography', models.JSONField(blank=True, default=dict, help_text="e.g. {'type':'national'} or {'type':'state','states':['NY']}")),
                ('target_size', models.PositiveIntegerField(default=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.OneToOneField(on_delete=models.deletion.CASCADE, related_name='partner_profile', to='core.project')),
            ],
        ),
        migrations.CreateModel(
            name='ProjectLead',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('campaign_status', models.CharField(choices=[('queued', 'Queued'), ('sent', 'Sent'), ('opened', 'Opened'), ('clicked', 'Clicked'), ('replied', 'Replied'), ('bounced', 'Bounced'), ('unsubscribed', 'Unsubscribed'), ('interested', 'Interested'), ('not_interested', 'Not Interested')], default='queued', max_length=30)),
                ('instantly_lead_id', models.CharField(blank=True, max_length=100)),
                ('monday_item_id', models.CharField(blank=True, max_length=100)),
                ('tracking_token', models.CharField(db_index=True, default=core.models.new_tracking_token, max_length=64, unique=True)),
                ('referred_count', models.PositiveIntegerField(default=0)),
                ('enrolled_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('lead', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='project_leads', to='core.lead')),
                ('project', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='project_leads', to='core.project')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='projectlead',
            constraint=models.UniqueConstraint(fields=('project', 'lead'), name='projectlead_project_lead_unique'),
        ),
        migrations.CreateModel(
            name='Campaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('instantly_campaign_id', models.CharField(blank=True, max_length=100)),
                ('name', models.CharField(max_length=255)),
                ('sequence_config', models.JSONField(blank=True, default=list, help_text='Ordered list of steps: [{step, subject, body, delay_days}]')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('awaiting_approval', 'Awaiting Human Approval'), ('active', 'Active'), ('paused', 'Paused'), ('completed', 'Completed')], default='draft', max_length=30)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='campaigns', to='core.project')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='OutreachEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('email_sent', 'Email Sent'), ('email_opened', 'Email Opened'), ('email_clicked', 'Email Clicked'), ('email_replied', 'Email Replied'), ('email_bounced', 'Email Bounced'), ('lead_unsubscribed', 'Lead Unsubscribed'), ('landing_page_view', 'Landing Page View'), ('screener_submitted', 'Screener Submitted')], max_length=30)),
                ('timestamp', models.DateTimeField()),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('synced_to_monday', models.BooleanField(default=False)),
                ('synced_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('project_lead', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='events', to='core.projectlead')),
            ],
            options={
                'ordering': ['-timestamp'],
                'indexes': [
                    models.Index(fields=['project_lead', 'event_type'], name='core_outrea_project_idx'),
                    models.Index(fields=['timestamp'], name='core_outrea_timestm_idx'),
                    models.Index(fields=['synced_to_monday'], name='core_outrea_synced_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='OptOut',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(db_index=True, max_length=255, unique=True)),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('source', models.CharField(choices=[('instantly_webhook', 'Instantly webhook'), ('manual', 'Manual'), ('reply_classified', 'Reply classified as opt-out')], default='manual', max_length=30)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(help_text="e.g. 'project.create', 'campaign.launch'", max_length=100)),
                ('entity_type', models.CharField(max_length=100)),
                ('entity_id', models.CharField(max_length=100)),
                ('before_state', models.JSONField(blank=True, null=True)),
                ('after_state', models.JSONField(blank=True, null=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-timestamp'],
                'indexes': [
                    models.Index(fields=['entity_type', 'entity_id'], name='core_auditl_entity_idx'),
                    models.Index(fields=['timestamp'], name='core_auditl_timestm_idx'),
                    models.Index(fields=['user', 'timestamp'], name='core_auditl_user_ts_idx'),
                ],
            },
        ),
    ]
