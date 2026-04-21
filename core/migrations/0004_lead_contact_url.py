from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_lead_monday_source_and_conflict'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='contact_url',
            field=models.URLField(
                blank=True,
                max_length=500,
                help_text='Org website or contact-page link (used for AI-suggested orgs where humans need to find the email manually)',
            ),
        ),
    ]
