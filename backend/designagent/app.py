"""The web API.

Chat is a POST that returns an SSE stream, carrying four kinds of frame:

  token    - assistant text as it is generated
  status   - a progress line from a node ("Folding 6 candidates…")
  task     - a task's lifecycle (submitted / log / finished / canceled)
  state    - the state keys the UI renders (artifacts, ensemble, lead, viz)
  done     - end of turn

POST rather than GET-with-EventSource because the prompt belongs in a body; the
frontend reads the stream with fetch + ReadableStream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .graph.state import new_state
from .runtime import Runtime, build_runtime

log = logging.getLogger(__name__)

# State keys worth pushing to the UI after a turn.
UI_STATE_KEYS = (
    "reference_design",
    "key_metric",
    "lead_design",
    "ensemble",
    "molecular_visualization",
    "artifacts",
    "design_summary",
    "worklist",
    "round",
    "intent",
    "status",
    "warnings",
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = "default"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    runtime = await build_runtime(get_settings())
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="Protein Design Agent", lifespan=lifespan)


def _runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(503, "runtime is not ready")
    return runtime


def _frame(kind: str, payload: dict | None = None) -> str:
    body = {"type": kind, **(payload or {})}
    return f"data: {json.dumps(body, default=str)}\n\n"


# --- chat -----------------------------------------------------------------


@app.post("/api/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    runtime = _runtime(request)
    session_id = body.session_id

    async def stream() -> AsyncIterator[str]:
        # Task events arrive from the manager on its own schedule, and graph
        # updates from astream; a queue merges both into one ordered stream.
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def sink(event: dict) -> None:
            await queue.put(_frame(event.pop("type", "task"), event))

        runtime.manager.subscribe(session_id, sink)

        async def run_graph() -> None:
            config = {
                "configurable": {"thread_id": session_id},
                "recursion_limit": 50,
            }
            payload = {
                **new_state(session_id),
                "messages": [{"role": "user", "content": body.message}],
            }
            try:
                async for mode, chunk in runtime.app.astream(
                    payload,
                    config=config,
                    stream_mode=["messages", "custom", "updates"],
                ):
                    if mode == "messages":
                        message, _meta = chunk
                        text = getattr(message, "content", "")
                        if isinstance(text, list):
                            text = "".join(
                                b.get("text", "")
                                for b in text
                                if isinstance(b, dict)
                            )
                        if text:
                            await queue.put(_frame("token", {"text": text}))
                    elif mode == "custom":
                        if isinstance(chunk, dict):
                            await queue.put(
                                _frame(chunk.pop("type", "status"), chunk)
                            )
                    elif mode == "updates":
                        for node, update in (chunk or {}).items():
                            if not isinstance(update, dict):
                                continue
                            slim = {
                                k: update[k] for k in UI_STATE_KEYS if k in update
                            }
                            if slim:
                                await queue.put(
                                    _frame("state", {"node": node, "state": slim})
                                )
                            # Without an LLM no tokens stream, so send the
                            # assistant turn explicitly.
                            for message in update.get("messages") or []:
                                content = _content_of(message)
                                if content:
                                    await queue.put(
                                        _frame("message", {"text": content})
                                    )
            except Exception as exc:
                log.exception("graph run failed")
                await queue.put(_frame("error", {"message": str(exc)}))
            finally:
                await queue.put(None)

        runner = asyncio.ensure_future(run_graph())
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                yield frame
            yield _frame("done")
        except asyncio.CancelledError:
            # The browser went away; stop the turn rather than leaking it.
            runner.cancel()
            raise
        finally:
            runtime.manager.unsubscribe(session_id, sink)
            if not runner.done():
                runner.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # let nginx pass chunks straight through
        },
    )


def _content_of(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    role = getattr(message, "type", None) or (
        message.get("role") if isinstance(message, dict) else None
    )
    if role in ("human", "user"):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return ""


# --- sessions and state ---------------------------------------------------


@app.get("/api/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> JSONResponse:
    """Current state for a session, so a reload restores the UI."""
    runtime = _runtime(request)
    config = {"configurable": {"thread_id": session_id}}
    try:
        snapshot = await runtime.app.aget_state(config)
    except Exception as exc:
        log.warning("state fetch failed: %s", exc)
        return JSONResponse({"session_id": session_id, "state": {}, "messages": []})

    values = snapshot.values if snapshot else {}
    return JSONResponse(
        {
            "session_id": session_id,
            "state": {k: values.get(k) for k in UI_STATE_KEYS if k in values},
            "messages": [
                {"role": _role_of(m), "content": _content_of(m) or _raw_content(m)}
                for m in (values.get("messages") or [])
            ],
            "tasks": runtime.manager.snapshot(session_id),
        }
    )


def _role_of(message: Any) -> str:
    role = getattr(message, "type", None) or (
        message.get("role") if isinstance(message, dict) else None
    )
    return "user" if role in ("human", "user") else "assistant"


def _raw_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content if isinstance(content, str) else ""


# --- artifacts ------------------------------------------------------------


@app.get("/api/artifacts/{artifact_id}")
async def get_artifact(request: Request, artifact_id: str) -> Response:
    runtime = _runtime(request)
    record = runtime.artifacts.get(artifact_id)
    if record is None:
        raise HTTPException(404, "no such artifact")
    data = runtime.artifacts.read_bytes(artifact_id)
    if data is None:
        raise HTTPException(410, "artifact file is missing")

    headers = {}
    if record["kind"] == "docx":
        filename = f"{record['title'].replace(' ', '_')}.docx"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=data, media_type=record["mime"], headers=headers)


@app.get("/api/artifacts")
async def list_artifacts(request: Request, session_id: str = "default") -> JSONResponse:
    runtime = _runtime(request)
    return JSONResponse({"artifacts": runtime.artifacts.list_for_session(session_id)})


# --- tasks ----------------------------------------------------------------


@app.get("/api/tasks")
async def list_tasks(request: Request, session_id: str | None = None) -> JSONResponse:
    runtime = _runtime(request)
    return JSONResponse({"tasks": runtime.manager.snapshot(session_id)})


@app.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: str) -> JSONResponse:
    runtime = _runtime(request)
    handle = runtime.manager.get(task_id)
    if handle is None:
        raise HTTPException(404, "no such task")
    return JSONResponse(handle.snapshot())


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str) -> JSONResponse:
    runtime = _runtime(request)
    handle = runtime.manager.get(task_id)
    if handle is None:
        raise HTTPException(404, "no such task")
    ok = await runtime.manager.cancel(task_id)
    if not ok:
        interface = runtime.manager.interfaces.get(handle.interface)
        reason = (
            "this interface cannot cancel a running task"
            if interface and not interface.capabilities.supports_cancel
            else "the task had already finished"
        )
        return JSONResponse({"canceled": False, "reason": reason}, status_code=409)
    return JSONResponse({"canceled": True})


# --- health ---------------------------------------------------------------


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    runtime = _runtime(request)
    return JSONResponse(
        {
            "ok": True,
            "llm": runtime.settings.llm_available,
            "model": runtime.settings.model if runtime.settings.llm_available else None,
            "hpc": runtime.manager.hpc_available,
            "flowgentic": runtime.integration is not None,
            "fold_backend": runtime.settings.fold_backend,
            "interfaces": {
                name: {
                    "cancel": iface.capabilities.supports_cancel,
                    "logs": iface.capabilities.supports_log_stream,
                    "push_events": iface.capabilities.supports_push_events,
                    "staging": iface.capabilities.supports_staging,
                }
                for name, iface in runtime.manager.interfaces.items()
            },
            "notes": runtime.notes,
        }
    )


@app.get("/api/catalog")
async def catalog(request: Request) -> JSONResponse:
    """What the agent can do, for the UI's help panel."""
    _runtime(request)
    from .tasks.registry import CATALOG

    return JSONResponse(
        {
            "tasks": [
                {
                    "name": t.name,
                    "interface": t.interface,
                    "description": t.description,
                    "produces": t.produces,
                }
                for t in CATALOG.values()
            ]
        }
    )
