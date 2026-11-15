"""Python audit-event observation for the collector's short-lived worker only.

Not an OS/network sandbox: native libraries can bypass Python audit events.
Only known repository scenarios are executed. Addresses/payloads are not logged.
"""

from contextlib import contextmanager
import sys

EVENTS = frozenset({"socket.connect", "socket.sendto", "socket.sendmsg",
                    "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
                    "socket.getnameinfo"})


class NetworkAttemptBlocked(RuntimeError):
    pass


class PythonNetworkObserver:
    def __init__(self):
        self.current = None
        self.rows = {}
        self.ready = False
        self.marker = object()

    def hook(self, event, args):
        if event == "claimci.observer_ready" and args == (self.marker,):
            self.ready = True
        if self.current is not None and event in EVENTS:
            row = self.rows[self.current]
            row["external_requests"] += 1
            row["events"][event] = row["events"].get(event, 0) + 1
            raise NetworkAttemptBlocked("Python network event blocked by the local collector")

    def install(self):
        sys.addaudithook(self.hook)
        sys.audit("claimci.observer_ready", self.marker)
        if not self.ready:
            raise RuntimeError("Observer registration could not be confirmed")

    @contextmanager
    def case(self, identifier):
        if not self.ready or self.current is not None or identifier in self.rows:
            raise RuntimeError("Observer is unavailable, nested or repeated")
        self.rows[identifier] = {"id":identifier, "scope":"python_socket_events",
                                "external_requests":0, "events":{}, "observation_complete":False}
        self.current = identifier
        try:
            yield self.rows[identifier]
            self.rows[identifier]["observation_complete"] = True
        finally:
            self.current = None
