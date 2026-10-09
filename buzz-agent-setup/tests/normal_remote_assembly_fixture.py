"""Finite native transport/environment additions; original fixture is unchanged.

No Hostd import, authority response, constructor or factory replacement. Parent
transport isolation is ordinary-user Python socket containment, not a netns.
The native signed-reader child retains its canonical minimal env and private CA.
"""
from contextlib import contextmanager, ExitStack
import ipaddress
import os
import socket
import sys
from unittest.mock import patch


@contextmanager
def parent_transport(environment, port):
    """Permit private TLS loopback and Unix lifecycle, before Hostd imports."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo
    sendto = socket.socket.sendto

    def admit(sock, address):
        if sock.family == socket.AF_UNIX:
            return
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            raise OSError('test transport denied')
        try:
            host, requested_port = address[:2]
            if str(ipaddress.ip_address(host)) != '127.0.0.1' or requested_port != port:
                raise ValueError()
        except (ValueError, TypeError):
            raise OSError('test transport denied') from None

    def guarded_connect(sock, address):
        admit(sock, address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        admit(sock, address)
        return connect_ex(sock, address)

    def guarded_addrinfo(host, requested_port, *args, **kwargs):
        if host != '127.0.0.1' or int(requested_port) != port:
            raise OSError('test DNS denied')
        return getaddrinfo(host, requested_port, *args, **kwargs)

    def guarded_sendto(sock, *args):
        admit(sock, args[-1])
        return sendto(sock, *args)

    previous_umask = os.umask(0o077)
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, dict(environment), clear=True))
            stack.enter_context(patch.object(socket.socket, 'connect', guarded_connect))
            stack.enter_context(patch.object(socket.socket, 'connect_ex', guarded_connect_ex))
            stack.enter_context(patch.object(socket.socket, 'sendto', guarded_sendto))
            stack.enter_context(patch.object(socket, 'getaddrinfo', guarded_addrinfo))
            yield
    finally:
        sys.dont_write_bytecode = previous_bytecode
        os.umask(previous_umask)


# Appended to that child's generated/pinned startupwire BEFORE admission only.
# AF_UNIX server bind is the native console I/O failure: normal Hostd main/finally
# executes, with original argv and actual Popen. No production module is patched.
EARLY_CONSOLE_BIND_FAILURE = '''
_zero_original_bind = socket.socket.bind
def _zero_fail_console_bind(sock, address):
    if sock.family == socket.AF_UNIX:
        raise OSError('test Unix console transport unavailable')
    return _zero_original_bind(sock, address)
socket.socket.bind = _zero_fail_console_bind
'''
