"""One-shot cleanup: delete every Lead that didn't come from a Monday board import.

CASCADE on ProjectLead → Lead and OutreachEvent → ProjectLead removes all the
per-project state automatically. OptOut / AuditLog / StudyAsset / Project /
PartnerProfile / AIUsageLog / ApolloCreditLog are preserved.

Run on Render:
    python manage.py cleanup_non_monday_leads --dry-run    # preview counts
    python manage.py cleanup_non_monday_leads              # actually delete
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from core.models import Lead


class Command(BaseCommand):
    help = 'Delete all Leads whose source is NOT monday_import (ProjectLead + OutreachEvent cascade).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting.',
        )

    def handle(self, *args, **options):
        target = Lead.objects.exclude(source=Lead.SOURCE_MONDAY)
        total_to_delete = target.count()
        total_kept = Lead.objects.filter(source=Lead.SOURCE_MONDAY).count()

        breakdown = list(
            target.values('source')
            .annotate(n=Count('id'))
            .order_by('-n')
        )

        self.stdout.write(self.style.NOTICE(
            f'Will delete {total_to_delete} leads. Keeping {total_kept} Monday-imported leads.'
        ))
        for row in breakdown:
            self.stdout.write(f'  - {row["source"]}: {row["n"]}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — nothing deleted.'))
            return

        if total_to_delete == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to delete.'))
            return

        with transaction.atomic():
            deleted_count, per_model = target.delete()

        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted_count} rows across tables:'))
        for model_label, n in per_model.items():
            self.stdout.write(f'  - {model_label}: {n}')
