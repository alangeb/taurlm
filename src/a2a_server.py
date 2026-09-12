"""A2A server: Unix socket server for inter-agent communication."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

from a2a_transport import DEFAULT_POLL_INTERVAL, HEARTBEAT_INTERVAL, SOCKET_BUFFER
from agent_models import InputMessage

class A2AServer:
    """Unix socket server for inter-agent communication.

    Runs in a daemon thread; handles ``agent_card`` (sync), ``status`` (sync),
    and ``query`` (async) requests from other agents.
    """

    def __init__(self, agent, sock_path: str = None):
        """Initialize server for *agent*; defaults socket to ``/tmp/taua2a-{PID}.sock``."""
        self.agent = agent
        self.sock_path = sock_path or f"/tmp/taua2a-{os.getpid()}.sock"
        self.sock = None
        self.running = False
        self.thread = None
        self._ready = threading.Event()

        self._handler_threads: set[threading.Thread] = set()
    def start(self):
        """Start the server in a daemon thread; blocks until ready or 5s timeout."""
        self.running = True
        self._ready.clear()
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("A2A server failed to start within 5s")

    def stop(self):
        """Stop the server and clean up the socket file (idempotent)."""
        self.running = False

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

        if hasattr(self, "sock_path") and Path(self.sock_path).exists():
            try:
                Path(self.sock_path).unlink(missing_ok=True)
            except OSError:
                pass

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

        # H20: Join any active handler threads
        for t in list(self._handler_threads):
            t.join(timeout=5)
        self._handler_threads.clear()

    def _accept_loop(self):
        """Bind socket, listen, and spawn a daemon thread per connection."""
        try:
            Path(self.sock_path).unlink(missing_ok=True)
        except OSError:
            pass

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # P7-B5-03/B5-12: create the socket file with owner-only permissions.
        # umask is applied atomically at bind() time (chmod-after-bind would
        # leave a world-accessible window).
        old_umask = os.umask(0o077)
        try:
            self.sock.bind(self.sock_path)
        finally:
            os.umask(old_umask)
        self.sock.listen(5)
        self._ready.set()
        self.sock.settimeout(1.0)

        while self.running:
            try:
                client_sock, _ = self.sock.accept()
                if not self._peer_allowed(client_sock):
                    client_sock.close()
                    continue
                if len(self._handler_threads) >= 10:
                    client_sock.close()
                    continue
                t = threading.Thread(
                    target=self._track_handler, args=(client_sock,), daemon=True
                )
                self._handler_threads.add(t)
                t.start()
            except TimeoutError:
                continue
            except OSError:
                if self.running:
                    continue

    def _peer_allowed(self, client_sock: socket.socket) -> bool:
        """P7-B5-03: accept only same-uid peers via SO_PEERCRED."""
        try:
            if hasattr(socket, "SO_PEERCRED"):
                cred = client_sock.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("III")
                )
                _pid, uid, _gid = struct.unpack("III", cred)
                return uid == os.geteuid()
            # No SO_PEERCRED (non-Linux): rely on 0700 socket permissions.
            return True
        except OSError:
            return False

    def _track_handler(self, client_sock: socket.socket):
        """Wrapper that tracks handler thread lifecycle."""
        try:
            self._handle_client(client_sock)
        finally:
            self._handler_threads.discard(threading.current_thread())

    def _recv_request(self, client_sock: socket.socket) -> bytes:
        """Receive the full JSON request from a client socket.

        Uses incremental JSONDecoder.raw_decode() to handle stream
        protocol correctly (split messages, short reads).
        """
        buffer = b""
        decoder = json.JSONDecoder()
        while True:
            chunk = client_sock.recv(SOCKET_BUFFER)
            if not chunk:
                break
            buffer += chunk
            try:
                obj, _idx = decoder.raw_decode(buffer.decode("utf-8"))
                return json.dumps(obj).encode("utf-8")
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return buffer

    def _handle_client(self, client_sock: socket.socket):
        """Read a JSON request and dispatch to handler.

        Runs in a daemon thread. A2A threads must use socket.sendall(),
        never print(). Do NOT swap sys.stdout here (process-global,
        not thread-safe).
        """

        request_type = "query"  # Default for finally block
        # P7-B5-02: bound recv/send so an idle or stalled client cannot pin a
        # handler thread forever (10 pinned threads = accept-loop refuses).
        # socket.timeout subclasses OSError -> handled by the except below.
        try:
            client_sock.settimeout(30.0)
        except OSError:
            pass
        try:
            data = self._recv_request(client_sock)
            if not data:
                return

            request = json.loads(data.decode("utf-8"))
            request_type = request.get("type", "query")

            if request_type == "agent_card":
                self._send_agent_card(client_sock)
            elif request_type == "status":
                self._handle_status(client_sock)
            else:
                request_id = request.get("id", str(uuid.uuid4()))
                query_content = request.get("query", "")
                if not isinstance(query_content, str):
                    client_sock.sendall(json.dumps({"type": "error", "id": request_id, "message": "query must be a string"}).encode() + b"\n")
                    return
                self._handle_query(client_sock, request_id, query_content)
        except (OSError, RuntimeError, json.JSONDecodeError) as e:
            try:
                client_sock.sendall(
                    json.dumps({"type": "error", "message": str(e)}).encode() + b"\n"
                )
            except OSError:
                pass
        finally:
            client_sock.close()

    def _build_agent_card(self) -> dict:
        """Build the agent card dict.

        Includes the A2A extension-004 session metadata fields
        (``original_task``, ``start_time``, ``parent_pid``, ``llm_group``,
        ``max_context_tokens``, ``session_id``). All new fields are optional and
        use ``getattr`` defaults so minimal duck-typed agents (and old clients)
        keep working.
        """
        session = getattr(self.agent, "_session", None)
        return {
            "type": "agent_card",
            "name": self.agent.agent_name,
            "model": self.agent.model_name,
            "mode": "rlm",
            "working_dir": str(self.agent.original_cwd),
            "context_length": len(self.agent.context),
            "uptime": (
                int(time.time() - self.agent._start_time)  # pylint: disable=W0212
                if hasattr(self.agent, "_start_time")
                else 0
            ),
            "sock_path": self.sock_path,
            "file": os.path.basename(sys.argv[0]) if sys.argv else "tau.py",
            "original_task": getattr(self.agent, "original_task", None),
            "start_time": getattr(self.agent, "_start_time", None),
            "parent_pid": os.getppid(),
            "llm_group": getattr(self.agent, "current_group_name", None),
            "max_context_tokens": getattr(self.agent, "max_context_tokens", None),
            "session_id": getattr(session, "prefix", None),
            "turn_active": getattr(self.agent, "_turn_active", False),
        }

    def _send_agent_card(self, client_sock: socket.socket):
        """Send agent metadata as JSON to *client_sock*."""
        try:
            client_sock.sendall(json.dumps(self._build_agent_card()).encode() + b"\n")
        except OSError:
            pass

    def _handle_status(self, client_sock: socket.socket):
        """Handle a lightweight status request — return running/idle info.

        Returns a JSON object with ``status_response`` type containing
        ``pid``, ``turn_active``, ``context_length``, ``uptime``,
        ``nesting_count``, and ``last_audit_mtime``.
        """
        audit_file = getattr(self.agent, "audit_file", None)
        last_audit_mtime = None
        if audit_file:
            try:
                last_audit_mtime = os.path.getmtime(str(audit_file))
            except OSError:
                pass

        status = {
            "type": "status_response",
            "pid": os.getpid(),
            "turn_active": getattr(self.agent, "_turn_active", False),
            "context_length": len(self.agent.context),
            "uptime": (
                int(time.time() - self.agent._start_time)  # pylint: disable=W0212
                if hasattr(self.agent, "_start_time")
                else 0
            ),
            "nesting_count": getattr(self.agent, "nesting_count", 0),
            "last_audit_mtime": last_audit_mtime,
        }
        try:
            client_sock.sendall(json.dumps(status).encode() + b"\n")
        except OSError:
            pass

    def _poll_for_response(self, client_sock: socket.socket, request_id: str) -> bool:
        """Poll for the response to *request_id* and send it to *client_sock*.

        Sends periodic heartbeats so the client knows the server is still alive.
        Polls indefinitely until response is available or client disconnects.
        Also sends streaming chunks (tool_call, tool_result, assistant) as they arrive.

        Returns True if response was sent, False if client disconnected.
        """
        last_heartbeat = time.time()
        while True:
            if not self.running:  # H17: shutdown check
                return False
            try:
                # Atomically snapshot and clear pending chunks (thread-safe: pop + list()
                # avoids race with main thread appending to the list).
                chunks_snapshot = list(
                    self.agent._pending_a2a_chunks.pop(request_id, [])  # pylint: disable=W0212
                )
                if chunks_snapshot:
                    for chunk in chunks_snapshot:
                        envelope = {
                            "protocol_version": "1.0",
                            "type": "stream_chunk",
                            "id": request_id,
                            "chunk": chunk,
                        }
                        client_sock.sendall(json.dumps(envelope).encode() + b"\n")

                if (
                    hasattr(self.agent, "_pending_a2a_responses")
                    and request_id in self.agent._pending_a2a_responses
                ):  # pylint: disable=W0212
                    result = self.agent._pending_a2a_responses.pop(
                        request_id
                    )  # pylint: disable=W0212
                    client_sock.sendall(json.dumps(result).encode() + b"\n")
                    return True

                # Send heartbeat if enough time has passed
                if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                    heartbeat = {"type": "heartbeat", "id": request_id}
                    client_sock.sendall(json.dumps(heartbeat).encode() + b"\n")
                    last_heartbeat = time.time()
            except OSError:
                return False
            time.sleep(DEFAULT_POLL_INTERVAL)
        return False

    def _handle_query(
        self, client_sock: socket.socket, request_id: str, query_content: str
    ):
        """Send ack, queue the query, then poll for the response with heartbeats."""
        ack = {"type": "queued", "id": request_id}
        try:
            client_sock.sendall(json.dumps(ack).encode() + b"\n")
        except OSError:
            return

        message = InputMessage.from_a2a(query_content, request_id)
        if not self.running:  # M-A1
            try:
                client_sock.sendall(json.dumps({"type": "error", "message": "Server shutting down"}).encode())
            except OSError:
                pass
            return
        self.agent.input_queue.put(message)
        self._poll_for_response(client_sock, request_id)



