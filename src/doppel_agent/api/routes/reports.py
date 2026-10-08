"""Explicit allowlist report reads; original owner only, no execution/paths."""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
router = APIRouter(prefix='/reports/runs', tags=['reports'])


async def _read(run_id: str, request: Request):
    if re.fullmatch(r'[0-9a-f]{32}', run_id) is None or request.scope.get('query_string', b''):
        raise HTTPException(422, 'invalid_report_request', headers=HEADERS)
    try:
        return await request.app.state.run_service.run_report(run_id)
    except KeyError:
        raise HTTPException(404, 'report_scope_not_found', headers=HEADERS) from None
    except Exception:
        # Never echo source/path/SQL/thread/worker/provider error strings.
        raise HTTPException(503, 'report_evidence_unavailable', headers=HEADERS) from None


@router.get('/{run_id}')
async def report(run_id: str, request: Request):
    return JSONResponse(await _read(run_id, request), headers=HEADERS)


@router.get('/{run_id}/download')
async def download(run_id: str, request: Request):
    value = await _read(run_id, request)
    return JSONResponse(value, headers={**HEADERS, 'Content-Disposition': f'attachment; filename="doppel-report-{run_id}.json"'})
