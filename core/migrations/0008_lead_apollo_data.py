from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_lead_linkedin_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='apollo_data',
            field=models.JSONField(
                blank=True,
                null=True,
                help_text='Full Apollo people/match response: headline, seniority, employment_history, '
                          'organization details, social URLs, photo. Used for lead context in review/edit.',
            ),
        ),
    ]
