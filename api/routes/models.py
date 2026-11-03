"""Authenticated model catalog and write-only connection credentials."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr

from api.schemas import ApiResponse
from core.model_connections import ModelConnections
from core.model_history import saved_models
from core.model_default import GlobalModelSelection
from core.database import get_database_service
from config.settings import settings

router = APIRouter(prefix='/models', tags=['models'])


class ConnectionConfig(BaseModel):
    name: str = Field(default='', max_length=80)
    base_url: str = Field(max_length=2000)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    model: str = Field(default='', max_length=300)
    context_tokens: int | None = None


class DefaultModelConfig(BaseModel):
    connection_id: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=300)


@router.put('/default', response_model=ApiResponse)
def set_default_model(config: DefaultModelConfig):
    try:
        selected = GlobalModelSelection(ModelConnections()).select(config.connection_id, config.model)
        return ApiResponse(success=True, data=selected)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(400, '保存默认模型失败，请检查本地配置目录权限') from None


@router.get('', response_model=ApiResponse)
def list_models(user_id: str = settings.default_user_id, project_id: str = settings.default_project_id):
    connections = ModelConnections()
    catalog = connections.catalog()
    return ApiResponse(success=True, data={
        'models': catalog,
        'recent': saved_models(get_database_service(), connections, catalog, user_id=user_id, project_id=project_id),
        'current': GlobalModelSelection(connections).get()})


@router.get('/connections/{connection_id}', response_model=ApiResponse)
def connection_status(connection_id: str):
    try:
        return ApiResponse(success=True, data=ModelConnections().public(connection_id))
    except ValueError:
        raise HTTPException(404, '模型服务不存在') from None


@router.put('/connections/{connection_id}', response_model=ApiResponse)
def configure_connection(connection_id: str, config: ConnectionConfig):
    try:
        result = ModelConnections().save(connection_id, name=config.name, base_url=config.base_url,
                                         api_key=config.api_key.get_secret_value(), model=config.model,
                                         context_tokens=config.context_tokens)
        return ApiResponse(success=True, data=result)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(400, '保存模型配置失败，请检查本地配置目录权限') from None
