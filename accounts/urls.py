from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

app_name = "accounts"

urlpatterns = [
    # auth
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # profile
    path("profile/", views.profile_view, name="my_profile"),
    path("verify-code/", views.verify_code_view, name="verify_code"),
    # 1) Форма запроса сброса пароля
    path(
      "password-reset/",
      auth_views.PasswordResetView.as_view(
        template_name="accounts/password_reset_form.html",
        email_template_name="accounts/password_reset_email.html",
        subject_template_name="accounts/password_reset_subject.txt",
        success_url="/accounts/password-reset/done/"
      ),
      name="password_reset"
    ),
    # 2) Страница «письмо выслано»
    path(
      "password-reset/done/",
      auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"
      ),
      name="password_reset_done"
    ),
    # 3) Ссылка из письма — ввод нового пароля
    path(
      "reset/<uidb64>/<token>/",
      auth_views.PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html",
        success_url="/accounts/reset/done/"
      ),
      name="password_reset_confirm"
    ),
    # 4) Успешно сменили пароль
    path(
      "reset/done/",
      auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html"
      ),
      name="password_reset_complete"
    ),
]
