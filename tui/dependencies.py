"""Stdlib-only startup preflight; optional browser dependencies are separate."""
from importlib.util import find_spec

RUNTIME_MODULES = ('httpx','socksio','rich','textual','fastapi','uvicorn','openai','requests',
                   'tavily','dotenv','bs4','pydantic','pymysql','pypdf','openpyxl')


def missing_runtime_modules():
    return [name for name in RUNTIME_MODULES if find_spec(name) is None]
