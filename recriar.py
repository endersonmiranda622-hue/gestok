from flask import Flask
from .config import load_config
from .db import init_db, close_db

def create_app():
    app = Flask(__name__)
    cfg = load_config()
    app.config.update(cfg)
    app.secret_key = cfg["SECRET_KEY"]
    init_db(app)
    for name in ["auth","accounts","dashboard","catalog","stock","movements","numbers","exports","admin","users","analytics","ops"]:
        try:
            mod = __import__(f"app.{name}", fromlist=["bp","blueprint"])
            bp = getattr(mod, "bp", None) or getattr(mod, "blueprint", None)
            if bp:
                app.register_blueprint(bp)
        except Exception as e:
            print(f"warn {name}: {e}")
    @app.teardown_appcontext
    def teardown_db(exc):
        close_db()
    @app.get("/health")
    def health():
        return {"status":"ok"}, 200
    return app