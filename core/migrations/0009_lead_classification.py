from django.db import migrations, models


CLASS_UNCLASSIFIED = 'unclassified'
CLASS_METABOLIC_CLINIC = 'metabolic_clinic'
CLASS_GENETIC_COUNSELOR = 'genetic_counselor'
CLASS_ADVOCACY_ORG = 'advocacy_org'
CLASS_COMMUNITY_PROVIDER = 'community_provider'


def _text_chunks(*values):
    chunks = []
    for value in values:
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip().lower())
        elif isinstance(value, dict):
            chunks.append(_text_chunks(*value.values()))
        elif isinstance(value, (list, tuple, set)):
            chunks.append(_text_chunks(*list(value)))
    return ' '.join(chunk for chunk in chunks if chunk)


def _infer_classification(lead):
    text = _text_chunks(
        getattr(lead, 'organization', ''),
        getattr(lead, 'role', ''),
        getattr(lead, 'specialty', ''),
        getattr(lead, 'contact_url', ''),
        getattr(lead, 'geography', {}) or {},
    )
    advocacy_terms = (
        'advocacy', 'foundation', 'society', 'association', 'alliance', 'network',
        'support group', 'support organization', 'nonprofit', 'non-profit',
        'rare disease org', 'patient org', 'faod', 'fatty acid oxidation',
    )
    counselor_terms = (
        'genetic counselor', 'genetic counselling', 'genetic counseling',
        'licensed genetic counselor', 'cgc',
    )
    metabolic_terms = (
        'metabolic clinic', 'metabolism', 'genetics clinic', 'genetic clinic',
        'biochemical genetics', 'clinical biochemical genetics',
        'inherited metabolic', 'metabolic genetics', 'metabolic specialist',
        'rare disease clinic', 'division of genetics', 'division of metabolism',
        'genetics and metabolism',
    )
    provider_terms = (
        'md', 'do', 'physician', 'provider', 'pediatrician', 'pediatrics', 'neurology',
        'clinical genetics', 'hospital', 'medical center', 'clinic', 'health system',
        'nurse practitioner', 'pa-c', 'community provider',
    )

    if any(term in text for term in advocacy_terms):
        return CLASS_ADVOCACY_ORG
    if any(term in text for term in counselor_terms):
        return CLASS_GENETIC_COUNSELOR
    if any(term in text for term in metabolic_terms):
        return CLASS_METABOLIC_CLINIC
    if any(term in text for term in provider_terms):
        return CLASS_COMMUNITY_PROVIDER
    return CLASS_UNCLASSIFIED


def backfill_lead_classification(apps, schema_editor):
    Lead = apps.get_model('core', 'Lead')
    for lead in Lead.objects.all().iterator():
        lead.classification = _infer_classification(lead)
        lead.save(update_fields=['classification'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_lead_apollo_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='classification',
            field=models.CharField(
                choices=[
                    (CLASS_UNCLASSIFIED, 'Unclassified'),
                    (CLASS_METABOLIC_CLINIC, 'Metabolic Clinic'),
                    (CLASS_GENETIC_COUNSELOR, 'Genetic Counselor'),
                    (CLASS_ADVOCACY_ORG, 'Advocacy Organization'),
                    (CLASS_COMMUNITY_PROVIDER, 'Community Provider'),
                ],
                db_index=True,
                default=CLASS_UNCLASSIFIED,
                max_length=40,
            ),
        ),
        migrations.RunPython(backfill_lead_classification, migrations.RunPython.noop),
    ]
