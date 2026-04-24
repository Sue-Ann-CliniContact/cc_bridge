import json

from django import forms

from .models import Lead, PartnerProfile, Project, StudyAsset


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'study_code', 'horizon_study_id', 'monday_board_id']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'e.g. NMD-204 Site Outreach'}),
            'study_code': forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'e.g. NMD-204'}),
            'horizon_study_id': forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'Optional Horizon ID'}),
            'monday_board_id': forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'Fill after board auto-creation'}),
        }


class PartnerProfileForm(forms.ModelForm):
    """Targeting config per project. Uses comma-separated inputs for list fields
    and a simple 'mode + states' pair for geography, both mapped to/from JSON."""

    target_org_types_csv = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'cc-input',
            'placeholder': 'indication-specific patient advocacy, NCI-designated cancer centers, academic medical centers',
        }),
        help_text='Categories of orgs to target (comma-separated)',
    )
    target_contact_roles_csv = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'cc-input',
            'placeholder': 'Principal Investigator, Clinical Research Coordinator, Patient Navigator',
        }),
        help_text='Specific titles we want to reach (comma-separated)',
    )
    specialty_tags_csv = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'Clinical Genetics, Clinical Biochemical Genetics, Genetic Counselor, Pediatrics'}),
        help_text='CMS NPI taxonomy names, comma-separated — drives NPI provider sourcing. Use multiple genetics/metabolic tags for rare-disease projects.',
    )
    icd10_codes_csv = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'C34.9, C78.0'}),
        help_text='Comma-separated ICD-10 codes (optional)',
    )
    geography_mode = forms.ChoiceField(
        choices=[
            ('national', 'National (all US)'),
            ('state', 'Specific states'),
            ('zip_radius', 'Within radius of ZIP'),
        ],
        widget=forms.Select(attrs={'class': 'cc-input'}),
        initial='national',
    )
    geography_states_csv = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'e.g. NY, CA, TX'}),
        help_text='Two-letter state codes, comma-separated (for "Specific states" mode)',
    )
    geography_zip = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'e.g. 10001'}),
        help_text='ZIP code (for "Within radius" mode)',
    )
    geography_radius_miles = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'cc-input', 'placeholder': '50'}),
        help_text='Miles from ZIP (for "Within radius" mode)',
    )

    class Meta:
        model = PartnerProfile
        fields = ['partner_type', 'study_indication', 'patient_population_description', 'target_size']
        widgets = {
            'partner_type': forms.Select(attrs={'class': 'cc-input'}),
            'study_indication': forms.TextInput(attrs={
                'class': 'cc-input',
                'placeholder': 'e.g. metastatic triple-negative breast cancer',
            }),
            'patient_population_description': forms.Textarea(attrs={
                'class': 'cc-input',
                'rows': 3,
                'placeholder': 'Who exactly are we recruiting — disease stage, key inclusion criteria, age, demographics?',
            }),
            'target_size': forms.NumberInput(attrs={'class': 'cc-input', 'placeholder': '100'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        if instance:
            self.fields['specialty_tags_csv'].initial = ', '.join(instance.specialty_tags or [])
            self.fields['icd10_codes_csv'].initial = ', '.join(instance.icd10_codes or [])
            self.fields['target_org_types_csv'].initial = ', '.join(instance.target_org_types or [])
            self.fields['target_contact_roles_csv'].initial = ', '.join(instance.target_contact_roles or [])
            geo = instance.geography or {}
            self.fields['geography_mode'].initial = geo.get('type', 'national')
            self.fields['geography_states_csv'].initial = ', '.join(geo.get('states') or [])
            self.fields['geography_zip'].initial = geo.get('zip', '')
            self.fields['geography_radius_miles'].initial = geo.get('radius_miles')

    def _split_csv(self, value):
        return [v.strip() for v in (value or '').split(',') if v.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.specialty_tags = self._split_csv(self.cleaned_data.get('specialty_tags_csv'))
        instance.icd10_codes = self._split_csv(self.cleaned_data.get('icd10_codes_csv'))
        instance.target_org_types = self._split_csv(self.cleaned_data.get('target_org_types_csv'))
        instance.target_contact_roles = self._split_csv(self.cleaned_data.get('target_contact_roles_csv'))

        mode = self.cleaned_data.get('geography_mode') or 'national'
        geography = {'type': mode}
        if mode == 'state':
            geography['states'] = [s.upper() for s in self._split_csv(self.cleaned_data.get('geography_states_csv'))]
        elif mode == 'zip_radius':
            geography['zip'] = (self.cleaned_data.get('geography_zip') or '').strip()
            geography['radius_miles'] = self.cleaned_data.get('geography_radius_miles') or 50
        instance.geography = geography

        if commit:
            instance.save()
        return instance


class StudyAssetForm(forms.ModelForm):
    class Meta:
        model = StudyAsset
        fields = ['type', 'subject', 'content_text', 'content_url', 'content_file']
        widgets = {
            'type': forms.Select(attrs={'class': 'cc-input'}),
            'subject': forms.TextInput(attrs={'class': 'cc-input', 'placeholder': 'Email subject (email assets only)'}),
            'content_text': forms.Textarea(attrs={'class': 'cc-input', 'rows': 6, 'placeholder': 'Email copy, study summary, or text brief'}),
            'content_url': forms.URLInput(attrs={'class': 'cc-input', 'placeholder': 'https://... (landing-page URL)'}),
        }


class LeadEditForm(forms.ModelForm):
    """Manual enrichment / edit form for a Lead — used when Apollo can't find an email."""

    class Meta:
        model = Lead
        fields = [
            'first_name', 'last_name', 'email', 'organization_email', 'phone', 'npi',
            'organization', 'role', 'specialty', 'classification', 'contact_url', 'linkedin_url',
            'enrichment_status', 'global_opt_out', 'do_not_contact_reason',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'cc-input'}),
            'last_name': forms.TextInput(attrs={'class': 'cc-input'}),
            'email': forms.EmailInput(attrs={'class': 'cc-input'}),
            'organization_email': forms.EmailInput(attrs={'class': 'cc-input'}),
            'phone': forms.TextInput(attrs={'class': 'cc-input'}),
            'npi': forms.TextInput(attrs={'class': 'cc-input'}),
            'organization': forms.TextInput(attrs={'class': 'cc-input'}),
            'role': forms.TextInput(attrs={'class': 'cc-input'}),
            'specialty': forms.TextInput(attrs={'class': 'cc-input'}),
            'classification': forms.Select(attrs={'class': 'cc-input'}),
            'contact_url': forms.URLInput(attrs={'class': 'cc-input', 'placeholder': 'https://org.example.com/contact'}),
            'linkedin_url': forms.URLInput(attrs={'class': 'cc-input', 'placeholder': 'https://www.linkedin.com/in/...'}),
            'enrichment_status': forms.Select(attrs={'class': 'cc-input'}),
            'do_not_contact_reason': forms.TextInput(attrs={'class': 'cc-input'}),
        }
