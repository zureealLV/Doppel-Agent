"""Typed application dependency accessors."""

from __future__ import annotations

from fastapi import Request

from ..runtime.service import RunService


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service
