# Ref: I2C-bus specification and user manual Rev 7.0
# Document Number: UM10204
# Accession: G00101

import contextlib
import logging
import struct

from amaranth import *
from amaranth.lib import enum, wiring, stream
from amaranth.lib.wiring import In, Out

from glasgow.support.logging import dump_hex
from glasgow.abstract import AbstractAssembly, GlasgowPin, PullState, ClockDivisor
from glasgow.applet.interface.bus1wire_controller._1wire import Bus1WireController
from glasgow.applet import GlasgowAppletError, GlasgowAppletV2

__all__ = ["Bus1WireControllerInterface", "PullState","I2CNotAcknowledged", "Bus1WireControllerComponent"]


class I2CNotAcknowledged(GlasgowAppletError):
    pass

class _Command(enum.Enum, shape=8):
    Sync = 0x00
    Write = 0x02
    Read  = 0x03
    Reset = 0x04


class Bus1WireControllerComponent(wiring.Component):
    i_stream: In(stream.Signature(8))
    o_stream: Out(stream.Signature(8))

    divisor: In(16)
    pulsetimer_value: In(16, init = 10)

    def __init__(self, ports):
        self._ports = ports
        self.ctrl = Bus1WireController(self._ports, 0, 2)
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.ctrl = self.ctrl
        ctrl = self.ctrl
        m.d.comb += ctrl.divisor.eq(self.divisor)
        # m.d.comb += ctrl.controller_timer_reset.eq(self.pulsetimer_value)

        cmd   = Signal(_Command)
        count = Signal(16)

        with m.FSM():
            with m.State("IDLE"):
                m.d.sync += cmd.eq(self.i_stream.payload)
                with m.If(self.i_stream.valid & ~ctrl.busy):
                    m.d.comb += self.i_stream.ready.eq(1)
                    m.next = "COMMAND"

            with m.State("COMMAND"):
                with m.Switch(cmd):
                    with m.Case(_Command.Sync):
                        # m.d.comb += ctrl.start.eq(1)
                        m.next = "SYNC"
                    with m.Case(_Command.Write, _Command.Read):
                        m.next = "COUNT"
                    with m.Case(_Command.Reset):
                        m.next = "RESET-FIRST"

            with m.State("SYNC"):
                with m.If(~ctrl.busy):
                    m.d.comb += self.o_stream.valid.eq(1)
                    with m.If(self.o_stream.ready):
                        m.next = "IDLE"

            with m.State("COUNT"):
                word = Signal(range(2))
                m.d.comb += self.i_stream.ready.eq(1)
                with m.If(self.i_stream.valid):
                    m.d.sync += count.word_select(word, 8).eq(self.i_stream.payload)
                    m.d.sync += word.eq(word + 1)
                    with m.If(word == 1):
                        with m.Switch(cmd):
                            with m.Case(_Command.Write):
                                m.next = "WRITE-FIRST"
                            with m.Case(_Command.Read):
                                m.next = "READ-FIRST"


            with m.State("WRITE-FIRST"):
                with m.If(self.i_stream.valid):
                    m.d.comb += self.i_stream.ready.eq(1)
                    m.d.comb += ctrl.data_o.eq(self.i_stream.payload[0])
                    m.d.comb += ctrl.write.eq(1)
                    m.next = "WRITE-ACK"

            with m.State("WRITE-ACK"):
                with m.If(~ctrl.busy):
                    # with m.If(ctrl.ack_o):
                    m.d.sync += count.eq(count - 1)
                    m.next = "WRITE"

            with m.State("WRITE"):
                with m.If((count == 0)):
                    m.next = "REPORT"
                with m.Elif(self.i_stream.valid):
                    m.d.comb += self.i_stream.ready.eq(1)
                    m.d.comb += ctrl.data_o.eq(self.i_stream.payload[0])
                    m.d.comb += ctrl.write.eq(1)
                    m.next = "WRITE-ACK"

            with m.State("REPORT"):
                word = Signal(range(2))
                m.d.comb += self.o_stream.valid.eq(1)
                with m.If(self.o_stream.ready):
                    m.d.comb += self.o_stream.payload.eq(count.word_select(word, 8))
                    m.d.sync += word.eq(word + 1)
                    with m.If(word == 1):
                        m.d.sync += count.eq(0)
                        m.next = "IDLE"


            with m.State("RESET-FIRST"):
                m.d.comb += ctrl.reset.eq(1)
                m.d.sync += count.eq(0)
                m.next = "READ"

# this can prolly be reduced
            with m.State("READ-FIRST"):
                m.d.comb += ctrl.read.eq(1)
                m.d.sync += count.eq(count - 1)
                m.next = "READ"

            with m.State("READ"):
                with m.If(~ctrl.busy):
                    m.d.comb += self.o_stream.valid.eq(1)
                    m.d.comb += self.o_stream.payload[0].eq(ctrl.data_i)
                    with m.If(self.o_stream.ready):
                        with m.If(count == 0):
                            m.next = "IDLE"
                        with m.Else():
                            m.d.comb += ctrl.read.eq(1)
                            m.d.sync += count.eq(count - 1)

        return m


class Bus1WireControllerInterface:
    def __init__(self, logger: logging.Logger, assembly: AbstractAssembly, *,
                 data_pin: GlasgowPin):
        self._logger = logger
        self._level  = logging.DEBUG if self._logger.name == __name__ else logging.TRACE

        assembly.use_pulls({data_pin: "high"})
        ports = assembly.add_port_group(data_pin=data_pin)
        component = assembly.add_submodule(Bus1WireControllerComponent(ports))
        self._pipe = assembly.add_inout_pipe(component.o_stream, component.i_stream)
        self._clock = assembly.add_clock_divisor(component.divisor,
            ref_period=assembly.sys_clk_period, name="wierd_4_clock")
        self._pulsetimer_value = assembly.add_rw_register(component.pulsetimer_value)

        self._multi = False
        self._busy  = False

    def bytes_to_bits(self, data: bytes) -> bytes:
        return b"".join([b"\x01" if d&(1<<i) else b"\x00" for d in data for i in range(8)])

    def bits_to_bytes(self, bits: bytes) -> bytearray:
        res = bytearray()
        curval = 0
        for i, bit in enumerate(bits):
            curval |= bit << (i % 8)
            if (i % 8) == 7:
                res.append(curval)
                curval = 0
        if (len(bits)%8) != 0:
            res.append(curval)
        return bytes(res)

    @staticmethod
    def _chunked(items, *, count=0xffff):
        while items:
            yield items[:count]
            items = items[count:]

    def _log(self, message, *args):
        self._logger.log(self._level, "1-wire: " + message, *args)

    async def _command(self, cmd: _Command, *, send: bytes | bytearray, recv: int) -> memoryview:
        await self._pipe.send([cmd.value])
        await self._pipe.send(send)
        await self._pipe.flush()
        return await self._pipe.recv(recv)

    async def _do_sync(self):
        if not self._busy:
            self._log("start")
        else:
            self._log("rep-start")
        await self._command(_Command.Sync, send=b"", recv=1)
        self._busy = True
    
    async def _do_reset(self):
        if not self._busy:
            self._log("reset")
        else:
            self._log("busy-reset")
        presence = struct.unpack("<B",
                await self._command(_Command.Reset, send=b"", recv=1))
        self._log("presence=<%x>", presence[0])
        return presence
    

    async def _do_addr(self, address: int|None) -> bool:
        if not address:
            report = await self._do_write(self.bytes_to_bits(b"\xcc"))
        else:
            data = b"\x55"+self.bytes_to_bits(address.to_bytes('little'))
            self._log(f"matching rom={address:#064b}")
            await self._do_write(data)


    async def _do_write(self, data: bytes | bytearray | memoryview) -> int:
        self._log("write data=<%s>", dump_hex(data))
        acked = 0
        for chunk in self._chunked(data):
            chunk_unacked, = struct.unpack("<H",
                await self._command(_Command.Write,
                    send=struct.pack("<H", len(chunk)) + bytes(chunk),
                    recv=2))
            acked += len(chunk) - chunk_unacked
            if chunk_unacked > 0:
                raise I2CNotAcknowledged(
                    f"data not acknowledged ({acked}/{len(data)} written)")

    async def _do_read(self, count: int) -> bytes:
        data_chunks = []
        for chunk in self._chunked(range(count)):
            chunk_data = await self._command(_Command.Read,
                send=struct.pack("<H", len(chunk)),
                recv=len(chunk))
            data_chunks.append(chunk_data)

        data = b"".join(data_chunks)
        self._log("read data=<%s>", dump_hex(data))
        return data

    @contextlib.asynccontextmanager
    async def _do_operation(self):
        await self._do_sync()
        try:
            yield
        finally:
            if not self._multi:
                await self._do_sync()

    @property
    def clock(self) -> ClockDivisor:
        """SCL clock divisor."""
        return self._clock

    @contextlib.asynccontextmanager
    async def select_rom(self, addr):
        await self._do_reset()
        await self._do_addr(addr)
        try:
            yield
        finally:
            await  self._do_sync()


    @contextlib.asynccontextmanager
    async def transaction(self):
        """Perform a transaction.

        While a transaction is active, calls to :meth:`write` and :meth:`read` do not generate
        a STOP condition; only one STOP condition is generated once the transaction ends. This also
        means that each call to :meth:`write` or :meth:`read` after the first such call in
        a transaction will generate a repeated START condition.

        For example, to perform ``S 0x50 nW 0x01 A Sr 0x50 R 0x?? nA 0x?? P`` (read of two bytes
        from a 24-series single address byte EEPROM, starting at address 0x01), use the following
        code:

        .. code:: python

            async with iface.transaction():
                await iface.write(0x50, [0x01])
                data = await iface.read(0x50, 2)

        An empty transaction (where the body does not call :meth:`write` or :meth:`read`) is
        allowed and produces no bus activity. (A START condition followed by a STOP condition is
        prohibited by the I²C specification.)
        """
        assert not self._multi, "transaction already active"

        self._multi = True
        try:
            yield
        finally:
            if self._busy:
                await self._do_sync()
            self._multi = False

    async def write(self, address: int, data: bytes | bytearray | memoryview):
        """Write bits.

        Generates a START condition followed by a WRITE target address (:py:`(address << 1) | 0`),
        writes data, then generates a STOP condition (unless used within a transaction).

        Raises
        ------
        I2CNotAcknowledged
            If either the target address or the written data receives a not-acknowledgement.
        """
        async with self._do_operation():
            await self._do_write(data)

    async def reset(self):
        """Reset the 1-wire Bus.
        
        Generates Reset pulse and returns a presence response
        """
        async with self._do_operation():
            await self._do_reset()

    async def read(self, count: int) -> bytes:
        """Read bits.

        Generates a START condition followed by a READ target address (:py:`(address << 1) | 1`),
        reads data, then generates a STOP condition (unless used within a transaction).

        The I²C bus design requires :py:`count` to be 1 or more.

        Raises
        ------
        I2CNotAcknowledged
            If the target address receives a not-acknowledgement.
        """
        # assert address in range(0, 128) and count >= 1

        async with self._do_operation():
            return await self._do_read(count)


    async def scan(self, addresses: range = range(0b0001_000, 0b1111_000)) -> set[int]:
        """Scan address range for presence.

        Calls :meth:`ping` for each of :py:`addresses`. The default address range includes every
        non-reserved I²C address.

        Returns the set of addresses receiving an acknowledgement.
        """
        acked = set()
        for address in addresses:
            if await self.ping(address):
                acked.add(address)
        return acked

    async def device_id(self, address: int) -> tuple[int, int, int]:
        """Retrieve Device ID.

        The standard I²C Device ID command (which uses the reserved address :py:`0b1111_100`) must
        not be confused with various vendor-specific device identifiers (which use a vendor-specific
        mechanism). This command is optional and rarely implemented.

        Returns a 3-tuple :py:`(manufacturer, part_ident, revision)`.

        Raises
        ------
        I2CNotAcknowledged
            If the command is not implemented.
        """
        async with self.transaction():
            await self.write(0b1111_100, [address])
            device_id = await self.read(0b1111_100, 3)

        manufacturer = (device_id[0] << 4) | (device_id[1] >> 4)
        part_ident   = ((device_id[1] & 0xf) << 5) | (device_id[2] >> 3)
        revision     = device_id[2] & 0x7
        return (manufacturer, part_ident, revision)

import time
class Bus1WireControllerApplet(GlasgowAppletV2):
    logger = logging.getLogger(__name__)
    help = "initiate 1-wire transactions"
    description = """
    Initiate transactions on the 1-wire bus.

    The following optional bus features are supported:

    * Clock stretching
    * Device ID
    """
    required_revision = "C0"

    @classmethod
    def add_build_arguments(cls, parser, access):
        access.add_voltage_argument(parser)
        access.add_pins_argument(parser, "data_pin", default=True, required=True)

    def build(self, args):
        with self.assembly.add_applet(self):
            self.assembly.use_voltage(args.voltage)
            self._1wire_iface = Bus1WireControllerInterface(self.logger, self.assembly,
                data_pin=args.data_pin)

    @classmethod
    def add_setup_arguments(cls, parser):
        parser.add_argument(
            "-f", "--frequency", metavar="FREQ", type=int, default=100,
            help="set frequency to FREQ kHz (default: %(default)s, range: 100...4000)")

    async def setup(self, args):
        await self._1wire_iface.clock.set_frequency(args.frequency * 1000)

    @classmethod
    def add_run_arguments(cls, parser):
        p_operation = parser.add_subparsers(dest="operation", metavar="OPERATION", required=True)

        writecommand1 = p_operation.add_parser(
            "write1", help="write a 1 bit"
        )
        writecommand0 = p_operation.add_parser(
            "write0", help="write a 0 bit"
        )

        reset = p_operation.add_parser(
            "reset", help="send a reset to the bus"
        )

    async def run(self, args):
        # if args.operation == "scan":
        #     for addr in await self._1wire_iface.scan():
        #         self.logger.info(f"scan found address {addr:#09b}/{addr:#04x}")
        #         if args.device_id:
        #             try:
        #                 manufacturer, part_ident, revision = await self.i2c_iface.device_id(addr)
        #                 self.logger.info("device %s ID: manufacturer %s, part %s, revision %s",
        #                     bin(addr), bin(manufacturer), bin(part_ident), bin(revision))
        #             except I2CNotAcknowledged:
        #                 self.logger.warning("device %s did not acknowledge Device ID", bin(addr))
        if "write" in args.operation:
            while(1):
                if "1" in args.operation:
                    data = b"\x33"
                    bits = []
                    for k in data:
                        for i in range(8):
                            bits.append( (k>>i)&1)
                    
                    msg = [b"\x01" if bit == 1 else b"\x00" for bit in bits ]
                    await self._1wire_iface.reset()
                    # await self._1wire_iface.write(1,b"\x01\x01\x00\x01")
                    await self._1wire_iface.write(1,b"".join(msg))
                    self.logger.info("sent 1")

                    out = await self._1wire_iface.read(64)
                    print(out)

                else:
                    await self._1wire_iface.write(1,b"\x00")
                    self.logger.info("sent 0")
                time.sleep(1)
        elif "reset" in args.operation:
            print("resetting")
            await self._1wire_iface.reset()
    # @classmethod
    # def tests(cls):
    #     from . import test
    #     return test.Bus1WireMasterAppletTestCase
