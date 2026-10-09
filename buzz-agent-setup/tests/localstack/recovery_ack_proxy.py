"""REC-006 fault boundary: a transparent local proxy in front of the isolated Relay.

Every client (native WebSocket, Buzz CLI, controller, runner) uses the proxy
origin, which is also the Relay's configured RELAY_URL, so NIP-42/NIP-98
bindings are unchanged. For selected accepted publications the proxy forwards
the request, reads the Relay's full answer, and then closes the client socket
without relaying it: the event is published but the publisher never sees the
ACK. Nothing is forged, re-signed or edited. Test-only; binds 127.0.0.1.
"""
import json
import select
import socket
import threading

HEAD_LIMIT = 64 * 1024
BODY_LIMIT = 2 * 1024 * 1024


def _read_head(sock, buffer=b""):
    while b"\r\n\r\n" not in buffer:
        if len(buffer) > HEAD_LIMIT:
            raise ValueError("oversized HTTP head")
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("peer closed before HTTP head")
        buffer += chunk
    head, rest = buffer.split(b"\r\n\r\n", 1)
    return head + b"\r\n\r\n", rest


def _content_length(head):
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
            if not 0 <= length <= BODY_LIMIT:
                raise ValueError("unsupported HTTP body")
            return length
    return 0


def _read_body(sock, rest, length):
    while len(rest) < length:
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("peer closed inside HTTP body")
        rest += chunk
    return rest[:length], rest[length:]


class AckDropProxy:
    def __init__(self, should_drop):
        self.should_drop = should_drop  # called once per POST /events with the signed event, under the lock
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.upstream = None
        self.lock = threading.Lock()
        self.dropped = []
        self.publications = []
        self.errors = []
        self.closed = threading.Event()

    def start(self, upstream_port):
        self.upstream = ("127.0.0.1", int(upstream_port))
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.closed.set()
        self.listener.close()

    def _accept(self):
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client):
        upstream = None
        try:
            head, rest = _read_head(client)
            method, path = head.split(b" ", 2)[:2]
            upstream = socket.create_connection(self.upstream, timeout=30)
            if method == b"POST" and path.split(b"?", 1)[0] == b"/events":
                body, rest = _read_body(client, rest, _content_length(head))
                event = json.loads(body)
                with self.lock:
                    drop = bool(self.should_drop(event))
                    self.publications.append(dict(event_id=event.get("id"), dropped=drop))
                upstream.sendall(head + body)
                if drop:
                    answer_head, answer_rest = _read_head(upstream)
                    answer, _ = _read_body(upstream, answer_rest, _content_length(answer_head))
                    status = int(answer_head.split(b" ", 2)[1])
                    try:
                        doc = json.loads(answer)
                    except ValueError:
                        doc = {}
                    with self.lock:
                        self.dropped.append(dict(event_id=event.get("id"), upstream_status=status,
                                                 accepted=isinstance(doc, dict) and doc.get("accepted") is True
                                                 and doc.get("event_id") == event.get("id")))
                    return  # the publisher never receives the Relay's ACK
                upstream.sendall(rest)
            else:
                upstream.sendall(head + rest)
            self._pipe(client, upstream)
        except (OSError, ValueError) as error:
            with self.lock:
                self.errors.append(type(error).__name__)
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def _pipe(self, client, upstream):
        """Bidirectional byte pipe (HTTP answers, WebSocket frames) until either side closes."""
        client.settimeout(None)
        upstream.settimeout(None)
        peers = {client: upstream, upstream: client}
        while not self.closed.is_set():
            readable, _, _ = select.select(list(peers), [], [], 1.0)
            for sock in readable:
                data = sock.recv(65536)
                if not data:
                    return
                peers[sock].sendall(data)
