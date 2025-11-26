# manga/forms.py
from django import forms
from .models import Chapter


# ===== Multiple fayl input =====
class MultipleFileInput(forms.ClearableFileInput):
    """
    ClearableFileInput ning ko‘p-faylli varianti.
    Django rasmiy docs: allow_multiple_selected = True.
    """
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """
    Bir field orqali bir nechta fayl qabul qilish uchun maxsus FileField.
    cleaned_data["images"] natijasi:
      - bir nechta fayl bo‘lsa -> [UploadedFile, ...] (list)
      - bitta fayl bo‘lsa -> UploadedFile (oddiy obyekt)
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        # FileInput value_from_datadict data ni list/tuple ko‘rinishida yuboradi
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return single_clean(data, initial)


# ===== Sahifalarni bulk yuklash formasi =====
class MultiPageUploadForm(forms.Form):
    images = MultipleFileField(
        label="Rasmlar tanlang",
        required=True,
        error_messages={
            "required": "Kamida bitta rasm tanlang.",
        },
        help_text="Bir vaqtning o'zida bir nechta rasm tanlashingiz mumkin.",
    )


# ===== Bob yaratish uchun admin forma (bulk) =====
class ChapterAdminForm(forms.ModelForm):
    """
    Admin’da bob qo‘shishda bitta qo‘shimcha maydon:
    bulk_total – nechta bob ketma-ket yaratiladi.
    """
    bulk_total = forms.IntegerField(
        label="Nechta bob yaratilsin?",
        min_value=1,
        max_value=200,
        required=False,
        initial=1,
        help_text=(
            "1 bo'lsa – oddiy rejim (faqat bitta bob). "
            "2 yoki ko'proq bo'lsa – tanlangan Manga va Jild uchun "
            "ketma-ket shuncha bob yaratiladi."
        ),
    )

    class Meta:
        model = Chapter
        # M2M `thanks` ni formadan chiqarib tashlaymiz —
        # yangi (id yo‘q) object bilan ManyToMany muammosini oldini oladi.
        exclude = ("thanks",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tahrirlash rejimida bulk_total keraksiz — yashirib qo'yamiz
        if self.instance and self.instance.pk:
            self.fields["bulk_total"].widget = forms.HiddenInput()
