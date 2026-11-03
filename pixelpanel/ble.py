"""Bluetooth LE connection to one panel (bleak), adapted from ha-pixelclock's device.py."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from bleak import BleakClient, BleakScanner

from .core import protocol as proto

log = logging.getLogger(__name__)

ACK_TIMEOUT = 8.0
COMMAND_ACK_TIMEOUT = 3.0
INFO_TIMEOUT = 5.0
FAST_SYNC_EVERY = 8  # every 8th chunk of a fast upload is an acknowledged write (back pressure)
FAST_ACK_TIMEOUT = 3.0


async def scan(timeout: float = 6.0) -> list[tuple[str, str, int]]:
    """Nearby panels as (address, name, rssi), strongest first."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    panels = [
        (dev.address, adv.local_name or dev.name or "", adv.rssi)
        for dev, adv in found.values()
        if (adv.local_name or dev.name or "").startswith(proto.NAME_PREFIX)
    ]
    return sorted(panels, key=lambda p: -p[2])


class PanelClient:
    """One connection; commands are serialised and wait for the panel's ACK."""

    def __init__(self, on_disconnect: Callable[[], None] | None = None) -> None:
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._ack: asyncio.Queue[int] = asyncio.Queue()
        self._info: asyncio.Future[bytes] | None = None
        self._on_disconnect_cb = on_disconnect
        self.address: str | None = None
        self.device_info: proto.DeviceInfo | None = None
        self.fast_writes = True

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def size(self) -> tuple[int, int]:
        return (self.device_info.width, self.device_info.height) if self.device_info else proto.DEFAULT_SIZE

    def _disconnected(self, _client: BleakClient) -> None:
        log.info("panel disconnected")
        self._client = None
        if self._on_disconnect_cb:
            self._on_disconnect_cb()

    def _notify(self, _sender: object, data: bytearray) -> None:
        data = bytes(data)
        status = proto.ack_status(data)
        if status is not None:
            self._ack.put_nowait(status)
        elif self._info is not None and not self._info.done():
            self._info.set_result(data)

    async def connect(self, address: str) -> None:
        async with self._lock:
            if self.connected and self.address == address:
                return
            await self._disconnect()
            client = BleakClient(address, timeout=20, disconnected_callback=self._disconnected)
            await client.connect()
            try:
                await client.start_notify(proto.NOTIFY_UUID, self._notify)
            except Exception:
                await client.disconnect()
                raise
            self._client = client
            self.address = address
            log.info("connected to %s, MTU %s", address, client.mtu_size)
            await self._sync_time()

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect()

    async def _disconnect(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001
                log.debug("disconnect: %s", err)

    def _require(self) -> BleakClient:
        if not self.connected:
            raise ConnectionError("Panel nicht verbunden")
        return self._client  # type: ignore[return-value]

    def _drain(self) -> None:
        while not self._ack.empty():
            self._ack.get_nowait()

    async def _wait_ack(self, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        want = proto.ACK_WINDOW + (proto.ACK_DONE,)
        while (left := end - loop.time()) > 0:
            try:
                if await asyncio.wait_for(self._ack.get(), left) in want:
                    return True
            except TimeoutError:
                return False
        return False

    async def _write(self, data: bytes, fast: bool = False) -> None:
        client = self._require()
        size = max(20, min(proto.MAX_CHUNK, client.mtu_size - 3))
        chunks = list(proto.chunks(data, size))
        for i, chunk in enumerate(chunks):
            sync = not fast or i % FAST_SYNC_EVERY == FAST_SYNC_EVERY - 1 or i == len(chunks) - 1
            await client.write_gatt_char(proto.WRITE_UUID, chunk, response=sync)

    async def _sync_time(self) -> None:
        self._info = asyncio.get_running_loop().create_future()
        try:
            self._drain()
            await self._write(proto.set_time(datetime.now()))
            self.device_info = proto.parse_device_info(await asyncio.wait_for(self._info, INFO_TIMEOUT))
            log.info("device info %s", self.device_info)
        except (TimeoutError, ValueError) as err:
            log.warning("no device info: %s", err)
        finally:
            self._info = None

    async def command(self, data: bytes) -> None:
        async with self._lock:
            self._drain()
            await self._write(data)
            await self._wait_ack(COMMAND_ACK_TIMEOUT)

    async def sync_time(self) -> None:
        async with self._lock:
            self._require()
            await self._sync_time()

    async def send_image(self, data: bytes, is_gif: bool) -> None:
        """Upload a PNG or GIF and show it (not stored on the panel)."""
        async with self._lock:
            windows = proto.image_windows(data, is_gif)
            if self.fast_writes:
                if await self._upload(windows, fast=True):
                    return
                log.info("fast upload not acknowledged, retrying with acknowledged writes")
                self.fast_writes = False
            if not await self._upload(windows, fast=False):
                raise TimeoutError("Panel hat das Bild nicht bestätigt")

    async def _upload(self, windows: list[bytes], fast: bool) -> bool:
        self._drain()
        for window in windows:
            await self._write(window, fast)
            if not await self._wait_ack(FAST_ACK_TIMEOUT if fast else ACK_TIMEOUT):
                return False
        return True
