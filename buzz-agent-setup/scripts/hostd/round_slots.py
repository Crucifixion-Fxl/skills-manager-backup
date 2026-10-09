"""Loop-owned round admission with configurable live-message reservation.

Priority changes scheduling only. All original binding locks, authorization,
rate limits and joined executor lifetime remain owned by the caller.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

class AdmissionWithdrawn(Exception):
    """A not-yet-admitted ticket was removed without cancelling its task."""

@dataclass(eq=False)
class Ticket:
    name: str
    future: asyncio.Future
    live: bool = False
    admitted: bool = False

class RoundSlots:
    def __init__(self, capacity=4, live_reserve=None):
        if type(capacity) is not int or not 1 <= capacity <= 16:
            raise ValueError('round workers must be an integer from 1 to 16')
        if live_reserve is None:
            live_reserve=min(1,capacity-1)
        if type(live_reserve) is not int or not 0 <= live_reserve < capacity:
            raise ValueError('round live reserve must be an integer from 0 to workers - 1')
        self.capacity=capacity;self.live_reserve=live_reserve
        self.background_limit=capacity-live_reserve
        self.waiting=[];self.active=[];self.live_names=set();self.live_streak=0

    def promote(self,name):
        self.live_names.add(name)
        for ticket in self.waiting:
            if ticket.name==name:ticket.live=True
        self._dispatch()

    def withdraw_waiting(self,name):
        """Loop-local: never touch admitted work or its caller-owned lock."""
        selected=[t for t in self.waiting if t.name==name and not t.admitted and not t.future.done()]
        for ticket in selected:
            self.waiting.remove(ticket)
            ticket.future.set_result(False)
        self._dispatch()
        return len(selected)

    def _dispatch(self):
        while len(self.active)<self.capacity:
            pending=[t for t in self.waiting if not t.future.cancelled()]
            live=[t for t in pending if t.live]
            background=[t for t in pending if not t.live]
            background_ok=sum(not t.live for t in self.active)<self.background_limit
            if background and background_ok and (not live or self.live_streak>=8):
                chosen=background[0];self.live_streak=0
            elif live:
                chosen=live[0];self.live_streak+=1
            else:break
            self.waiting.remove(chosen);self.active.append(chosen);chosen.admitted=True
            self.live_names.discard(chosen.name);chosen.future.set_result(True)

    @asynccontextmanager
    async def slot(self,name):
        ticket=Ticket(name,asyncio.get_running_loop().create_future(),name in self.live_names)
        self.waiting.append(ticket);self._dispatch()
        try:
            if not await ticket.future:
                raise AdmissionWithdrawn('round admission withdrawn')
            yield
        finally:
            if ticket in self.waiting:self.waiting.remove(ticket)
            if ticket in self.active:self.active.remove(ticket)
            self._dispatch()
