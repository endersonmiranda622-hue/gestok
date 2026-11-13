from flask import Flask, redirect

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'gestok-super-secret-123'
        
    @app.route('/health')
    def health():
        return 'OK - gestok no ar'

    @app.route('/rotas')
    def rotas():
        return '<br>'.join([str(r) for r in app.url_map.iter_rules()])

    try:
        from .auth import bp as auth_bp
        app.register_blueprint(auth_bp)
        from .dashboard import bp as dashboard_bp
        app.register_blueprint(dashboard_bp)
        from .stock import bp as stock_bp
        app.register_blueprint(stock_bp)
        from .catalog import bp as catalog_bp
        app.register_blueprint(catalog_bp)
        from .movements import bp as movements_bp
        app.register_blueprint(movements_bp)
        from .accounts import bp as accounts_bp
        app.register_blueprint(accounts_bp)
        from .users import bp as users_bp
        app.register_blueprint(users_bp)
    except Exception as e:
        print(f"ERRO: {e}")
        import traceback
        traceback.print_exc()

    return app
