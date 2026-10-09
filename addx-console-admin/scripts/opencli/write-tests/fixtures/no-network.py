"""Run one standalone Python test with transport disabled."""
import runpy, socket, sys

def deny(*args, **kwargs):
    raise RuntimeError("OFFLINE_TEST_NETWORK_FORBIDDEN")

socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
