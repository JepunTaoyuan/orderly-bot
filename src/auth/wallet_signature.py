from shared.wallet_verifier import WalletSignatureVerifier as _BaseVerifier
from src.utils.logging_config import get_logger

logger = get_logger("wallet_verifier")


class WalletSignatureVerifier(_BaseVerifier):
    def __init__(self):
        super().__init__(logger=logger)
