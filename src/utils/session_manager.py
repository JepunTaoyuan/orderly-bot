import src.services.session_service as _svc
from src.services.session_service import SessionManager as _BaseSessionManager
from src.core.grid_bot import GridTradingBot
from src.services.database_service import MongoManager

class SessionManager(_BaseSessionManager):
    def __init__(self):
        _svc.MongoManager = MongoManager
        _svc.GridTradingBot = GridTradingBot
        super().__init__()

    def __str__(self):
        return f"SessionManager managing {len(self.sessions)} sessions: {', '.join(self.sessions.keys())}"
