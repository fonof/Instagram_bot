from aiogram import Router

from bot.handlers.accounts import router as accounts_router
from bot.handlers.posting import router as posting_router
from bot.handlers.start import router as start_router
from bot.handlers.upload import router as upload_router


def setup_routers() -> Router:
    root = Router()
    root.include_router(start_router)
    root.include_router(accounts_router)
    root.include_router(upload_router)
    root.include_router(posting_router)
    return root
