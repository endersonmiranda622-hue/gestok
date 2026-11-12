import os, secrets
from pathlib import Path
from flask import Flask, request, jsonify

from .config import Config
from .db import connect, migrate, schema_version

def create_app():
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    
    secret = app.config.get("SECRET_KEY") or os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config["SECRET_KEY"] = secret
    
    base_dir = Path(app.root_path).parent
    instance_path = base_dir / "instance"
    
    if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
        import tempfile
        db_path = Path(tempfile.gettempdir()) / "gestok.db"
    else:
        instance_path.mkdir(parents=True, exist_ok=True)
        db_path = instance_path / "gestok.db"
    
    migrations_dir = Path(app.root_path) / "migrations"
    
    try:
        conn = connect(str(db_path))
        if migrations_dir.exists():
            migrate(conn, str(migrations_dir))
        conn.close()
    except Exception as e:
        app.logger.error(f"DB init failed: {e}")

    def safe_register(module_name, bp_name=None):
        try:
            mod = __import__(f"app.{module_name}", fromlist=[bp_name or "bp"])
            bp = getattr(mod, bp_name or "bp", None)
            if bp:
                app.register_blueprint(bp)
        except Exception as ex:
            app.logger.warning(f"Skip {module_name}: {ex}")

    safe_register("auth")
    safe_register("dashboard")
    safe_register("catalog")
    safe_register("stock")
    safe_register("finance")
    safe_register("ops")
    safe_register("security")
    safe_register("importer")
    safe_register("admin")

    @app.route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        if request.method == "HEAD":
            return "", 200
        try:
            conn = connect(str(db_path))
            v = schema_version(conn) if conn else "unknown"
            conn.close()
            return jsonify({"status": "ok", "version": str(v), "db": str(db_path)}), 200
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.route("/api/health", methods=["GET"])
    def api_health():
        return jsonify({"status": "ok"}), 200

    @app.route("/")
    def index():
        return jsonify({"app": "Gestok", "status": "running", "health": "/healthz"}), 200

    return app

app = create_app()