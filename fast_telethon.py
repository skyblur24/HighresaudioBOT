# Subida paralela para Telethon (basado en el proyecto comunitario "FastTelethon",
# derivado de mautrix-telegram, MIT license). Abre varias conexiones a la vez para
# subir distintas partes del archivo en simultáneo, en vez de una sola conexión secuencial.

import asyncio
import hashlib
import inspect
import logging
import math
import os
from typing import BinaryIO, List, Optional, Union

from telethon import helpers, utils, TelegramClient
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest
from telethon.tl.types import InputFile, InputFileBig

log = logging.getLogger("fast_telethon")


class UploadSender:
    def __init__(self, client, sender, file_id, part_count, big, index, stride, loop):
        self.client = client
        self.sender = sender
        self.part_count = part_count
        self.stride = stride
        self.previous = None
        self.loop = loop
        if big:
            self.request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self.request = SaveFilePartRequest(file_id, index, b"")

    async def next(self, data: bytes):
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data))

    async def _next(self, data: bytes):
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride

    async def disconnect(self):
        if self.previous:
            await self.previous
        return await self.sender.disconnect()


class ParallelTransferrer:
    def __init__(self, client: TelegramClient, dc_id: Optional[int] = None):
        self.client = client
        self.loop = client.loop
        self.dc_id = dc_id or client.session.dc_id
        self.auth_key = (
            client.session.auth_key
            if not dc_id or client.session.dc_id == dc_id
            else None
        )
        self.senders: Optional[List[UploadSender]] = None
        self.upload_ticker = 0

    async def _cleanup(self):
        if self.senders:
            await asyncio.gather(*[s.disconnect() for s in self.senders])
        self.senders = None

    @staticmethod
    def _get_connection_count(file_size, max_count=1, full_size=1 * 1 * 1):
        if file_size > full_size:
            return max_count
        return max(1, math.ceil((file_size / full_size) * max_count))

    async def _create_sender(self):
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(
            self.client._connection(
                dc.ip_address, dc.port, dc.id,
                loggers=self.client._log, proxy=self.client._proxy,
            )
        )
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            req = InvokeWithLayerRequest(LAYER, self.client._init_request)
            await sender.send(req)
            self.auth_key = sender.auth_key
        return sender

    async def _create_upload_sender(self, file_id, part_count, big, index, stride):
        return UploadSender(
            self.client, await self._create_sender(), file_id, part_count, big,
            index, stride, loop=self.loop,
        )

    async def init_upload(self, file_id, file_size, part_size_kb=None, connection_count=None):
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = int((part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024)
        part_count = math.ceil(file_size / part_size)
        is_large = file_size > 10 * 1024 * 1024

        self.senders = [
            await self._create_upload_sender(file_id, part_count, is_large, 0, connection_count),
            *await asyncio.gather(*[
                self._create_upload_sender(file_id, part_count, is_large, i, connection_count)
                for i in range(1, connection_count)
            ]),
        ]
        return part_size, part_count, is_large

    async def upload(self, part: bytes):
        await self.senders[self.upload_ticker].next(part)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)

    async def finish_upload(self):
        await self._cleanup()


async def upload_file(
    client: TelegramClient,
    file: BinaryIO,
    progress_callback=None,
    part_size_kb: Optional[float] = None,
    file_size: Optional[int] = None,
    filename: Optional[str] = None,
) -> Union[InputFile, InputFileBig]:
    if not file_size:
        file_size = os.path.getsize(file.name)

    file_id = helpers.generate_random_long()
    hash_md5 = hashlib.md5()

    transferrer = ParallelTransferrer(client)
    part_size, part_count, is_large = await transferrer.init_upload(file_id, file_size, part_size_kb)

    uploaded = 0
    for _ in range(part_count):
        data = file.read(part_size)
        if not is_large:
            hash_md5.update(data)
        await transferrer.upload(data)
        uploaded += len(data)
        if progress_callback:
            r = progress_callback(uploaded, file_size)
            if inspect.isawaitable(r):
                await r

    await transferrer.finish_upload()

    if is_large:
        return InputFileBig(file_id, part_count, filename or "unnamed")
    return InputFile(file_id, part_count, filename or "unnamed", hash_md5.hexdigest())
