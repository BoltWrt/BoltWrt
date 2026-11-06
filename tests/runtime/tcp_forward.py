#!/usr/bin/env python3
"""Container TCP relay to loopback QEMU forwards; publish Docker ports on 127.0.0.1.

Targets are always 127.0.0.1. No payload, credentials or connection arguments
are logged. This relay changes neither guest routes nor host configuration.
"""
import argparse
import asyncio
import os
import signal


def mapping(value):
    try:
        listen, target = (int(part) for part in value.split(":"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected LISTEN_PORT:LOOPBACK_TARGET_PORT") from error
    if not (1024 <= listen <= 65535 and 1024 <= target <= 65535):
        raise argparse.ArgumentTypeError("ports must be between 1024 and 65535")
    return listen, target


class Relay:
    def __init__(self, limit=32, connect_timeout=10.0, idle_timeout=120.0):
        self.limit = limit
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.tasks = set()

    def accept(self, reader, writer, target):
        # Admission is synchronous: excess clients cannot queue unbounded tasks.
        if len(self.tasks) >= self.limit:
            writer.close()
            return
        task = asyncio.create_task(self.forward(reader, writer, target))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def forward(self, reader, writer, target):
        upstream = None
        workers = []
        try:
            incoming, upstream = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", target), self.connect_timeout)
            loop = asyncio.get_running_loop()
            activity = [loop.time()]

            async def pump(source, destination):
                while True:
                    data = await source.read(65536)
                    if not data:
                        # Keep the other direction alive after an orderly FIN.
                        if destination.can_write_eof():
                            destination.write_eof()
                            await destination.drain()
                        return
                    activity[0] = loop.time()
                    destination.write(data)
                    await destination.drain()
                    activity[0] = loop.time()

            async def watchdog():
                while True:
                    remaining = self.idle_timeout - (loop.time() - activity[0])
                    if remaining <= 0:
                        raise TimeoutError("relay idle timeout")
                    await asyncio.sleep(remaining)

            workers = [asyncio.create_task(pump(reader, upstream)),
                       asyncio.create_task(pump(incoming, writer))]
            timer = asyncio.create_task(watchdog())
            workers.append(timer)
            # A single orderly EOF is not completion; await both TCP directions.
            copying = asyncio.gather(*workers[:2])
            try:
                done, _ = await asyncio.wait((copying, timer), return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    await finished
            finally:
                copying.cancel()
                await asyncio.gather(copying, return_exceptions=True)
        except (OSError, TimeoutError):
            # Expected peer disconnects/timeouts contain no useful public data.
            pass
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            streams = [stream for stream in (writer, upstream) if stream is not None]
            for stream in streams:
                stream.close()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(stream.wait_closed() for stream in streams),
                                   return_exceptions=True), 5)
            except TimeoutError:
                for stream in streams:
                    stream.transport.abort()

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve(args):
    if os.geteuid() == 0:
        raise ValueError("run the relay as an unprivileged user")
    relay = Relay(args.max_connections, args.connect_timeout, args.idle_timeout)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    servers = []
    try:
        for listen, target in args.forward:
            servers.append(await asyncio.start_server(
                lambda reader, writer, port=target: relay.accept(reader, writer, port),
                "0.0.0.0", listen, limit=65536, backlog=args.max_connections))
        print("TCP relay ready; Docker host publications must use 127.0.0.1", flush=True)
        await stop.wait()
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))
        await relay.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forward", action="append", type=mapping,
                        help="LISTEN_PORT:LOOPBACK_TARGET_PORT; repeat for each mapping")
    parser.add_argument("--max-connections", type=int, default=32)
    parser.add_argument("--connect-timeout", type=float, default=10)
    parser.add_argument("--idle-timeout", type=float, default=120)
    args = parser.parse_args()
    args.forward = args.forward or [(2222, 2223), (8080, 8081), (8443, 8444)]
    listens = {listen for listen, _ in args.forward}
    if len(listens) != len(args.forward) or listens & {target for _, target in args.forward}:
        parser.error("listen ports must be unique and must not overlap target ports")
    if args.max_connections < 1 or not all(0 < timeout < float("inf")
                                         for timeout in (args.connect_timeout, args.idle_timeout)):
        parser.error("connection limit and finite timeouts must be positive")
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
