from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import socket
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import ConsoleMessage, Page, Request, expect
import uvicorn

from clearagent.chat.app import create_chat_app
from clearagent.create import create_agent
from clearagent.providers.base import FakeProvider, ProviderResponse
from clearagent.storage.sqlite import SQLiteTraceStore


@dataclass(frozen=True)
class ChatServer:
    url: str
    trace_db_path: Path


class ReadyServer(uvicorn.Server):
    """Expose an exact readiness signal after Uvicorn starts accepting connections."""

    def __init__(self, config: uvicorn.Config, ready: threading.Event) -> None:
        super().__init__(config)
        self._ready = ready

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        self._ready.set()


@pytest.fixture
def chat_server(tmp_path: Path) -> Iterator[ChatServer]:
    trace_db_path = tmp_path / "traces.sqlite"
    agent = create_agent(
        name="browser_agent",
        model="local:fake-model",
        system_prompt="Answer browser checks deterministically.",
        provider=FakeProvider(
            [
                ProviderResponse.fake_text("Hello **from ClearAgent**."),
                RuntimeError("controlled browser provider failure"),
            ]
        ),
        trace_db_path=trace_db_path,
    )
    app = create_chat_app(agent, chat_db_path=tmp_path / "chat.sqlite")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", 0))
    port = server_socket.getsockname()[1]
    ready = threading.Event()
    thread_errors: list[BaseException] = []
    server = ReadyServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            lifespan="off",
            log_level="error",
            timeout_graceful_shutdown=5,
            ws="none",
        ),
        ready,
    )

    def run_server() -> None:
        try:
            server.run(sockets=[server_socket])
        except BaseException as exc:
            thread_errors.append(exc)
        finally:
            ready.set()

    thread = threading.Thread(target=run_server, name="clearagent-browser-test", daemon=True)
    thread.start()
    if not ready.wait(timeout=10):
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("Timed out waiting for the local ClearAgent chat server to start.")
    if thread_errors:
        raise RuntimeError("The local ClearAgent chat server failed to start.") from thread_errors[0]
    if not server.started:
        pytest.fail("The local ClearAgent chat server stopped before becoming ready.")

    try:
        yield ChatServer(url=f"http://127.0.0.1:{port}", trace_db_path=trace_db_path)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        if thread.is_alive():
            pytest.fail("The local ClearAgent chat server did not shut down.")
        if thread_errors:
            raise RuntimeError("The local ClearAgent chat server failed.") from thread_errors[0]


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_local_chat_browser_streams_traces_and_surfaces_provider_errors(
    page: Page,
    chat_server: ChatServer,
) -> None:
    page_errors: list[str] = []
    console_errors: list[str] = []
    failed_requests: list[str] = []
    request_urls: list[str] = []

    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def record_console_error(message: ConsoleMessage) -> None:
        if message.type == "error":
            console_errors.append(message.text)

    def record_failed_request(request: Request) -> None:
        failed_requests.append(f"{request.method} {request.url}: {request.failure}")

    page.on("console", record_console_error)
    page.on("requestfailed", record_failed_request)
    page.on("request", lambda request: request_urls.append(request.url))

    page.goto(chat_server.url, wait_until="domcontentloaded")

    status = page.locator("#status")
    prompt = page.locator("#prompt")
    send = page.locator("#send")
    expect(status).to_have_text("Ready")
    expect(page.get_by_role("heading", name="Start a conversation")).to_be_visible()

    prompt.fill("Show the browser trace.")
    send.click()

    assistant = page.locator("article.message.assistant").last
    expect(assistant).to_contain_text("Hello from ClearAgent.")
    expect(assistant.locator("strong")).to_have_text("from ClearAgent")
    trace_link = assistant.get_by_role("button", name="Open trace")
    expect(trace_link).to_be_visible()
    expect(status).to_have_text("Ready")

    trace_link.click()

    trace_shell = page.locator("#trace-shell")
    trace_detail = page.locator("#trace-detail")
    expect(trace_shell).to_be_visible()
    expect(trace_detail.locator(".trace-detail-header h2")).to_have_text("browser_agent")
    expect(trace_detail.locator(".trace-io")).to_contain_text("Show the browser trace.")
    expect(trace_detail.locator(".trace-io")).to_contain_text("Hello **from ClearAgent**.")
    expect(trace_detail.locator(".model-call")).to_contain_text("fake / fake-model")
    run_id = trace_detail.locator(".trace-detail-header p").text_content()
    assert run_id
    assert SQLiteTraceStore(chat_server.trace_db_path).get_run(run_id)["status"] == "ok"

    page.locator("#traces-toggle").click()
    expect(prompt).to_be_visible()
    prompt.fill("Trigger the controlled failure.")
    send.click()

    expect(status).to_have_text("Error")
    expect(page.locator("article.message.assistant").last).to_contain_text(
        "controlled browser provider failure"
    )
    expect(send).to_be_enabled()

    external_requests = [
        url
        for url in request_urls
        if urlsplit(url).scheme in {"http", "https"}
        and urlsplit(url).hostname not in {"127.0.0.1", "localhost"}
    ]
    assert external_requests == []
    assert failed_requests == []
    assert page_errors == []
    assert console_errors == []
