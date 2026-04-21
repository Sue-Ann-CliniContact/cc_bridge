from django import forms

from .models import Project, StudyAsset


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
