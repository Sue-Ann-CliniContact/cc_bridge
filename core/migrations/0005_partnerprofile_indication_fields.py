from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_lead_contact_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='partnerprofile',
            name='study_indication',
            field=models.CharField(
                blank=True,
                max_length=500,
                help_text='Specific clinical condition the study targets (e.g. "metastatic triple-negative breast cancer")',
            ),
        ),
        migrations.AddField(
            model_name='partnerprofile',
            name='patient_population_description',
            field=models.TextField(
                blank=True,
                help_text='2–4 sentences describing the target patients (disease stage, age, key inclusion, demographics)',
            ),
        ),
        migrations.AddField(
            model_name='partnerprofile',
            name='target_org_types',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Categories of orgs to target (e.g. ["indication-specific patient advocacy", "NCI-designated cancer centers"])',
            ),
        ),
        migrations.AddField(
            model_name='partnerprofile',
            name='target_contact_roles',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Specific titles we want to reach (e.g. ["Principal Investigator", "Clinical Research Coordinator"])',
            ),
        ),
        migrations.AlterField(
            model_name='partnerprofile',
            name='specialty_tags',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='CMS NPI taxonomy names (e.g. "Medical Oncology")',
            ),
        ),
    ]
