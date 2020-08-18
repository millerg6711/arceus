import asyncio
import ssl
from tcp_latency import measure_latency
import statistics
import traceback
import pause
import time
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from typing import Union
from abc import ABC, abstractmethod

from .account import Account
from .logger import log


class SocketManager:
    def __init__(
        self,
        host: str,
        port: int,
        payload: bytes,
        ssl: Union[bool, ssl.SSLContext] = True,
        attempts: int = 50,
    ):
        self.host = host
        self.port = port
        self.ssl = ssl
        self.payload = payload
        self.attempts = attempts
        self.conns = []

    async def create_conn(self):
        conn = await asyncio.open_connection(self.host, self.port, ssl=self.ssl)
        self.conns.append(conn)

    async def connect(self):
        await asyncio.gather(
            *(self.create_conn() for _ in range(self.attempts)), return_exceptions=True
        )

    async def spam(self):
        for _reader, writer in self.conns:
            writer.write(self.payload)
        await asyncio.gather(
            *(writer.drain() for _reader, writer in self.conns), return_exceptions=True
        )


def datetime_from_utc_to_local(utc_datetime):
    now_timestamp = time.time()
    offset = datetime.fromtimestamp(now_timestamp) - datetime.utcfromtimestamp(
        now_timestamp
    )
    return utc_datetime + offset


class Sniper(ABC):
    def __init__(self, target: str, account: Account):
        self.target = target
        self.account = account

    @property
    @abstractmethod
    def payload(self):
        pass

    def get_drop(self):
        page = requests.get(f"https://namemc.com/search?q={self.target}")
        soup = BeautifulSoup(page.content, "html.parser")
        countdown = soup.find(id="availability-time").attrs["datetime"]
        self.drop_time = datetime_from_utc_to_local(
            datetime.strptime(countdown, "%Y-%m-%dT%H:%M:%S.000Z")
        )

    def get_rtt(self, samples: int = 5):
        latency = measure_latency(host="api.mojang.com", port=443, runs=samples)
        self.rtt = timedelta(milliseconds=statistics.mean(latency))

    def block(
        self,
        attempts: int = 100,
        keepalive: timedelta = timedelta(seconds=1),
        verbose: bool = False,
    ):
        self.get_rtt()
        self.get_drop()
        log("Waiting for name drop...", "yellow")

        pause.until(self.drop_time - timedelta(seconds=10))
        if verbose:
            log("Authenticating...", "yellow")
        self.account.authenticate()
        self.account.get_challenges()  # Necessary to facilitate auth ¯\_(ツ)_/¯
        sockets = SocketManager("api.mojang.com", 443, self.payload, attempts=attempts,)

        pause.until(self.drop_time - keepalive)
        if verbose:
            log(f"Connecting...", "yellow")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(sockets.connect())

        pause.until(self.drop_time - (self.rtt / 2))
        if verbose:
            log(f"Spamming...", "yellow")
        #loop.run_until_complete(sockets.spam())


class Blocker(Sniper):
    @property
    def payload(self):
        return (
            f"PUT /user/profile/agent/minecraft/name/{self.target} HTTP/1.1\r\n"
            f"Host: api.mojang.com\r\n"
            f"Connection: keep-alive\r\n"
            f"Content-Length: 0\r\n"
            f"Accept: */*\r\n"
            f"Authorization: Bearer {self.account.token}\r\n"
            f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36\r\n\r\n"
        ).encode()