from .push import bp as push_bp
from .agent_api import bp as agent_api_bp
from .pi_agent import bp as pi_agent_bp
from .main import bp as main_bp
from .moments import bp as moments_bp
from .auth import bp as auth_bp
from .app_api import bp as app_bp
from .api_auth import bp as api_auth_bp
from .ai_api import bp as ai_api_bp
from .ai_sessions import bp as ai_sessions_bp


def register_blueprints(app):
    app.register_blueprint(push_bp, url_prefix='/api/push')
    app.register_blueprint(agent_api_bp, url_prefix='/api/agent')
    app.register_blueprint(pi_agent_bp)
    app.register_blueprint(pi_agent_bp, name="coding_agent", url_prefix="/coding")
    app.register_blueprint(main_bp)
    app.register_blueprint(moments_bp, url_prefix='/moments')
    app.register_blueprint(auth_bp)
    app.register_blueprint(app_bp, url_prefix='/api')
    app.register_blueprint(api_auth_bp, url_prefix='/api')
    app.register_blueprint(ai_api_bp, url_prefix='/api/ai')
    app.register_blueprint(ai_sessions_bp)
