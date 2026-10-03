from pydantic import BaseModel


class ApiInfo(BaseModel):
    name: str
    status: str


class HealthStatus(BaseModel):
    status: str
