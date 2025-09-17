# forms.py
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django import forms

User = get_user_model()

class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True, label="Email")

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("Пользователь с таким e-mail уже зарегистрирован.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"].lower()
        user.is_active = False
        if commit:
            user.save()
        return user
    
from django.contrib.auth import authenticate, get_user_model

class UsernameChangeForm(forms.Form):
    username = forms.CharField(max_length=150, label="Yangi username")
    password = forms.CharField(widget=forms.PasswordInput, label="Joriy parol")

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_username(self):
        new = self.cleaned_data["username"].strip()
        if User.objects.exclude(pk=self.user.pk).filter(username__iexact=new).exists():
            raise forms.ValidationError("Bu username band.")
        return new

    def clean(self):
        cleaned = super().clean()
        pwd = cleaned.get("password")
        if pwd and not authenticate(username=self.user.username, password=pwd):
            # Agar email bilan login bo‘lsangiz, authenticate uchun USERNAME_FIELD mos bo‘lsin.
            raise forms.ValidationError("Parol noto‘g‘ri.")
        return cleaned

    def save(self):
        self.user.username = self.cleaned_data["username"]
        self.user.save(update_fields=["username"])
        return self.user
