from pydantic import BaseModel, Field

from app.schemas.types import Email


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(..., min_length=6)


class PhoneLoginRequest(BaseModel):
    """Вход пациента в личный кабинет по телефону и паролю.

    Пациент не вводит email (он может отсутствовать), а использует телефон,
    указанный при регистрации на ресепшене.
    """

    phone: str = Field(..., max_length=50)
    password: str = Field(..., min_length=6)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    must_change_password: bool = False


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class PatientAccessResponse(BaseModel):
    """Ответ на выдачу пациенту доступа к личному кабинету."""

    patient_id: int
    user_id: int
    login_phone: str
    temp_password_ttl_hours: int
    message: str = "Временный пароль отправлен пациенту по SMS"
