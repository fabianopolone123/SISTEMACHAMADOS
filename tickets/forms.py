from django import forms
from django.contrib.auth import get_user_model

from .models import Ticket, TicketMessage, UserProfile, TicketType, WhatsAppRecipient
from .utils import normalize_phone_number

User = get_user_model()


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiFileField(forms.FileField):
    widget = MultiFileInput

    def clean(self, data, initial=None):
        files = data or []
        if not isinstance(files, (list, tuple)):
            files = [files]
        cleaned_files = []
        for file_obj in files:
            if file_obj:
                cleaned_files.append(super().clean(file_obj, initial))
        return cleaned_files


class RegisterForm(forms.Form):
    username = forms.CharField(label='Nome de usuário', max_length=150)
    email = forms.EmailField(label='E-mail corporativo')
    full_name = forms.CharField(label='Nome completo', max_length=150)
    setor = forms.CharField(label='Setor', max_length=100)
    password1 = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput,
    )
    password2 = forms.CharField(
        label='Repita a senha',
        widget=forms.PasswordInput,
    )

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Esse usuário já existe.')
        return username

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('As senhas não conferem.')

    def save(self):
        cleaned = self.cleaned_data
        user = User.objects.create_user(
            username=cleaned['username'],
            password=cleaned['password1'],
            email=cleaned['email'],
        )
        user.first_name = cleaned['full_name']
        user.save()
        UserProfile.objects.create(user=user, setor=cleaned['setor'])
        return user


class ProfileUpdateForm(forms.Form):
    full_name = forms.CharField(label='Nome completo', max_length=150)
    email = forms.EmailField(label='E-mail corporativo')
    setor = forms.CharField(label='Setor', max_length=100)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            self.user = user
            self.fields['full_name'].initial = user.first_name
            self.fields['email'].initial = user.email
            perfil = getattr(user, 'perfil', None)
            if perfil:
                self.fields['setor'].initial = perfil.setor

    def save(self):
        perfil = getattr(self.user, 'perfil', None)
        self.user.first_name = self.cleaned_data['full_name']
        self.user.email = self.cleaned_data['email']
        if perfil:
            perfil.setor = self.cleaned_data['setor']
            perfil.save()
        else:
            UserProfile.objects.create(user=self.user, setor=self.cleaned_data['setor'])
        self.user.save()


class PasswordUpdateForm(forms.Form):
    new_password = forms.CharField(
        label='Nova senha',
        widget=forms.PasswordInput,
    )
    confirm_password = forms.CharField(
        label='Repita a nova senha',
        widget=forms.PasswordInput,
    )

    def clean(self):
        cleaned = super().clean()
        low = cleaned.get('new_password')
        confirm = cleaned.get('confirm_password')
        if low and confirm and low != confirm:
            raise forms.ValidationError('As senhas digitadas não conferem.')

    def save(self, user):
        user.set_password(self.cleaned_data['new_password'])
        user.save()

class WhatsAppRecipientForm(forms.ModelForm):
    class Meta:
        model = WhatsAppRecipient
        fields = ['phone_number']
        help_texts = {
            'phone_number': 'Informe o número internacional (reseta automaticamente para o formato 551499...)',
        }

    def clean_phone_number(self):
        value = self.cleaned_data.get('phone_number')
        try:
            return normalize_phone_number(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc))


class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['title', 'description', 'urgency', 'ticket_type']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Resumo do chamado', 'class': 'form-control'}),
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Descreva o problema com o máximo de detalhes possível', 'class': 'form-control'}),
            'urgency': forms.Select(attrs={'class': 'form-control'}),
            'ticket_type': forms.Select(attrs={'class': 'form-control'}),
        }

    attachments = MultiFileField(
        label='Anexos',
        required=False,
        help_text='Envie prints, vídeos, PDFs ou logs (máx. 10MB cada).'
    )


class TicketMessageForm(forms.ModelForm):
    class Meta:
        model = TicketMessage
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Adicione um comentário para o chamado', 'class': 'form-control'}),
        }

    attachments = MultiFileField(
        label='Anexos do comentário',
        required=False,
        help_text='Envie prints, vídeos, PDFs ou logs relacionados a esta mensagem.'
    )


class ResolutionForm(forms.Form):
    resolution = forms.CharField(
        label='Descrição da resolução',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Descreva o que foi feito para resolver o chamado',
            'class': 'form-control',
        }),
        max_length=1000,
    )
