from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_apollocreditlog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='lead',
            name='source',
            field=models.CharField(
                choices=[
                    ('npi', 'NPI Registry'),
                    ('apollo', 'Apollo'),
                    ('ai_suggested', 'AI Suggested (pending review)'),
                    ('csv_import', 'CSV Import'),
                    ('manual', 'Manual'),
                    ('ctgov', 'ClinicalTrials.gov'),
                    ('monday_import', 'Monday Board Import'),
                ],
                default='manual',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='pending_conflict',
            field=models.JSONField(
                blank=True,
                null=True,
                help_text='Set when an import matched this lead by email but had different name/org. '
                          'Resolve by merging or dismissing in the lead review UI.',
            ),
        ),
    ]
