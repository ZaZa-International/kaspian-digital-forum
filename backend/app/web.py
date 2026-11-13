"""Browser pages with a shared shell and one source for the dispatcher markup."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse


def _source(directory: Path, filename: str) -> str:
    try:
        return (directory / filename).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(503, "Интерфейс временно недоступен") from exc


def _fragment(source: str, name: str) -> tuple[str, str]:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    if source.count(start) != 1 or source.count(end) != 1:
        raise HTTPException(503, "Не удалось загрузить интерфейс")
    before, remainder = source.split(start, 1)
    if end not in remainder:
        raise HTTPException(503, "Не удалось загрузить интерфейс")
    fragment, after = remainder.split(end, 1)
    return fragment, before + after


def home(directory: Path) -> HTMLResponse:
    source = _source(directory, "index.html")
    _, source = _fragment(source, "dispatcher")
    return HTMLResponse(source, headers={"Cache-Control": "no-cache"})


def _page(directory: Path, detail: bool = False) -> HTMLResponse:
    shell = _source(directory, "workspace.html")
    index = _source(directory, "index.html")
    dialogs, _ = _fragment(index, "dialogs")
    dispatcher = _fragment(index, "dispatcher")[0] if detail else ""
    for marker, content in {
        "<!-- DISPATCHER -->": dispatcher,
        "<!-- DIALOGS -->": dialogs,
        "<!-- DETAIL_SCRIPT -->": '<link rel="stylesheet" href="/static/passport.css" /><script type="module" src="/static/command.js"></script>' if detail else "",
    }.items():
        if shell.count(marker) != 1:
            raise HTTPException(503, "Не удалось загрузить рабочее пространство")
        shell = shell.replace(marker, content)
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


def create_router(directory: Path) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get("/app", response_class=HTMLResponse)
    @router.get("/app/shipments", response_class=HTMLResponse)
    @router.get("/app/map", response_class=HTMLResponse)
    @router.get("/app/risks", response_class=HTMLResponse)
    @router.get("/app/documents", response_class=HTMLResponse)
    @router.get("/app/analytics", response_class=HTMLResponse)
    def workspace_page():
        return _page(directory)

    @router.get("/app/shipments/{shipment_id}", response_class=HTMLResponse)
    def shipment_page(shipment_id: str):
        # The id is resolved by the existing API, never interpolated into HTML.
        return _page(directory, detail=True)

    return router
