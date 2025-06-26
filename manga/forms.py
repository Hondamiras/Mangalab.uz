# forms.py
from django import forms

class MultiPageUploadForm(forms.Form):
    images = forms.FileField(
        label="Rasmlar",
        required=False,
        widget=forms.ClearableFileInput()
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['images'].widget.attrs.update({'multiple': True})

    def clean_images(self):
        files = self.files.getlist('images')
        if not files:
            raise forms.ValidationError("Kamida bitta rasm tanlang.")
        return files
