import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'gestok-secret-key-2024-super-seguro')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:////tmp/gestok.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

# Mantém sua função original pra compatibilidade
def load_config():
    return {
        "SECRET_KEY": Config.SECRET_KEY,
        "DATABASE": "/tmp/gestok.db",
        "BASE_URL": "https://gestok-final.vercel.app",
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "MAX_CONTENT_LENGTH": 2 * 1024 * 1024,
    }