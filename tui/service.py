"""Portable owned-service entry point: bind in the child, then report readiness."""
import asyncio
import json
import os
import socket
import sys

READY_PREFIX = 'HORIZON_SERVICE_READY '


def main():
    # No inherited descriptor: works with both POSIX and Windows subprocesses.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        os.environ['API_HOST'] = '127.0.0.1'
        os.environ['API_PORT'] = str(port)
        import uvicorn

        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    print(READY_PREFIX + json.dumps({'port': port, 'pid': os.getpid()}), flush=True)

        config = uvicorn.Config('api.app:app', host='127.0.0.1', port=port,
                                log_level='warning', access_log=False, timeout_graceful_shutdown=2)
        asyncio.run(ReadyServer(config).serve(sockets=[listener]))


if __name__ == '__main__':
    main()
