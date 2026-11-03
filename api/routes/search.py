"""Authenticated, write-only web search configuration."""
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, SecretStr
from api.schemas import ApiResponse
from core.search_connections import SearchConnections
from core.web_search_provider import get_web_search_provider_status

class PrivateConfigRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Default validation errors echo the request's raw credential input.
                raise HTTPException(422, 'Invalid search configuration') from None
        return safe_handler

router=APIRouter(prefix='/search',tags=['search'],route_class=PrivateConfigRoute)

class SearchConfig(BaseModel):
    api_key: SecretStr=Field(default_factory=lambda:SecretStr(''))

def public_status():
    status=get_web_search_provider_status()
    try:
        configured = SearchConnections().configured()
        configuration_error = False
    except ValueError:
        configured = False
        configuration_error = True
    return {'provider':'tavily','active_provider':status.active_provider,
            'configured':configured, 'configuration_error':configuration_error,
            'search_available':status.search_available and status.active_provider=='tavily',
            'credential_verified':False}

@router.get('',response_model=ApiResponse)
def search_status():
    try:return ApiResponse(success=True,data=public_status())
    except Exception:raise HTTPException(400,'无法读取搜索配置') from None

@router.put('',response_model=ApiResponse)
def configure_search(config:SearchConfig):
    try:
        SearchConnections().save(config.api_key.get_secret_value())
        return ApiResponse(success=True,data=public_status())
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    except Exception:raise HTTPException(400,'保存搜索配置失败，请检查本地配置目录权限') from None
