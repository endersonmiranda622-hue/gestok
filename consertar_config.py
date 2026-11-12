import pathlib
code = '''
import os, secrets

class ConfigError(Exception):
    pass

def load_config():
    mode = os.getenv("GESTOK_ENV", "development")
    secret = os.getenv("GESTOK_SECRET_KEY") or os.getenv("SECRET_KEY") or secrets.token_hex(32)
    
    def _int(name, default, lo, hi):
        try:
            v = int(os.getenv(name, default))
        except:
            v = default
        return v
    
    env = os.environ
    return {
        "TRUSTED_PROXIES": _int("GESTOK_TRUSTED_PROXIES", 0, 0, 5),
        "FORCE_HTTPS": env.get("GESTOK_FORCE_HTTPS", "0") == "1",
        "SESSION_IDLE_MINUTES": _int("GESTOK_SESSION_IDLE_MINUTES", 120, 5, 1440),
        "SESSION_ABS_HOURS": _int("GESTOK_SESSION_ABS_HOURS", 8, 1, 72),
        "SESSION_COOKIE_NAME": "__Host-gestok" if mode == "production" else "session",
        "ENV_MODE": mode,
        "SECRET_KEY": secret,
        "DATABASE": env.get("GESTOK_DATABASE_PATH", "/tmp/gestok.db"),
        "BASE_URL": env.get("GESTOK_BASE_URL", "http://localhost:5000"),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": mode == "production",
        "MAX_CONTENT_LENGTH": 2 * 1024 * 1024,
    }
'''
pathlib.Path("app/config.py").write_text(code, encoding="utf-8")
print("app/config.py CONSERTADO - não exige mais SECRET_KEY!")