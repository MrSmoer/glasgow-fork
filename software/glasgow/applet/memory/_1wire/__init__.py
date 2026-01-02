# Ref: AT24C04C and AT24C08C I²C-Compatible, (2-wire) Serial EEPROM 4-Kbit (512 x 8),
#      8-Kbit (1024 x 8) DATASHEET
# Accession: G00104
#
# Ref: AT24C256C I²C-Compatible (2-Wire) Serial EEPROM 256-Kbit (32,768 x 8) DATASHEET
# Accession: G00105

from typing import Literal
import asyncio
import logging
import argparse

from glasgow.applet.interface.bus1wire_controller import I2CNotAcknowledged, Bus1WireControllerInterface
from glasgow.applet import GlasgowAppletError, GlasgowAppletV2


__all__ = ["_1_WireEepromInterface", "_1_WireEepromApplet"]


class _1_WireEepromInterface:
    def __init__(self, logger: logging.Logger, interface: Bus1WireControllerInterface, _1wire_address: int,
                  page_size: int):
        self._logger     = logger
        self._level      = logging.DEBUG if self._logger.name == __name__ else logging.TRACE

        self._1wire_iface  = interface
        self._1wire_addr   = _1wire_address
        self._page_size   = page_size

    def _log(self, message, *args):
        self._logger.log(self._level, "24x: " + message, *args)

    async def read(self, address: int, length: int) -> bytes:
        """Read :py:`length` bytes at :py:`address`.

        If :py:`address` is larger than the maximum representable given the address width (i.e.
        greater than 0x100 or 0x10000 for 1 and 2 address byte memories respectively), the "extra"
        address bits are carried over into the I²C address by addition. For example, calling
        :py:`read(0x10358, 0x100)` when configured with :py:`i2c_address=0x51, address_width=2`
        reads 0x100 bytes from memory address 0x358 at I²C device address 0x52.

        .. note::

            While this behavior is suitable for most memories, there may be cases where it is not
            appropriate; for example, if a memory wraps over at 0x100 byte or 0x10000 byte
            boundaries instead of returning data that would be read from the next I²C address over.

        Raises
        ------
        I2CNotAcknowledged
            If communication fails; if a previous write hasn't completed yet.
        """
        assert address >= 0 and length >= 0
        if length == 0:
            return b"" # can't do a 0-length I2C read

        self._log("read addr=%#06x len=%#06x", address, length)

        async with self._1wire_iface.select_rom(self._1wire_addr):
            ## Note that even if this is a 1-byte address EEPROM and we write 2 bytes here,
            ## we will not overwrite the contents, since the actual write is only initiated
            ## on stop, not repeated start condition.
            # write TA1 TA2
            await self._1wire_iface.write(0xeeaaa, self._1wire_iface.bytes_to_bits(b"\xf0"))
            await self._1wire_iface.write(0xeee, self._1wire_iface.bytes_to_bits(address.to_bytes(2,'little')))
            data = await self._1wire_iface.read(length)
            self._log("read data=<%s>", data.hex())
            self._log("read data=<%s>", self._1wire_iface.bits_to_bytes(data).hex())

        return data

    async def write(self, address: int, data: bytes | bytearray | memoryview):
        """Write :py:`data` bytes at :py:`address`.

        The :py:`data` is broken up into chunks such that each chunk is aligned to, and no larger
        than, :py:`page_size`. This algorithm assumes that partial page writes update only the part
        being written, which is true for virtually every 24-series memory.

        The note on address bit carry-over in :meth:`read` also applies here, with the caveat that
        reading is done in one long request, but writing is done page-wise, and the address is sent
        anew for each write.

        This method waits for the write to complete by polling the device until it responds to its
        address ("Acknowledge Polling").

        Raises
        ------
        I2CNotAcknowledged
            If communication fails; if a previous write hasn't completed yet.
        """
        while len(data) > 0:
            if address % self._page_size == 0:
                chunk_size = self._page_size
            else:
                chunk_size = self._page_size - address % self._page_size
            chunk, data = data[:chunk_size], data[chunk_size:]

            self._log("write i2c-addr=%#04x addr=%#06x data=<%s>", i2c_addr, address, chunk.hex())

            await self._1wire_iface.write(i2c_addr, addr_bytes + chunk)
            while not await self._1wire_iface.ping(i2c_addr):
                await asyncio.sleep(0.050) # 50 ms

            address += len(chunk)

    
    async def writeScratchpad(self, address: int, data: bytes | bytearray | memoryview):
        """Write :py:`data` bytes at :py:`address`.

        The :py:`data` is broken up into chunks such that each chunk is aligned to, and no larger
        than, :py:`page_size`. This algorithm assumes that partial page writes update only the part
        being written, which is true for virtually every 24-series memory.

        The note on address bit carry-over in :meth:`read` also applies here, with the caveat that
        reading is done in one long request, but writing is done page-wise, and the address is sent
        anew for each write.

        This method waits for the write to complete by polling the device until it responds to its
        address ("Acknowledge Polling").

        Raises
        ------
        I2CNotAcknowledged
            If communication fails; if a previous write hasn't completed yet.
        """
        while len(data) > 0:
            if address % self._page_size == 0:
                chunk_size = self._page_size
            else:
                chunk_size = self._page_size - address % self._page_size
            chunk, data = data[:chunk_size], data[chunk_size:]

            self._log("write i2c-addr= addr=%#06x data=<%s>", address, chunk.hex())


            async with self._1wire_iface.select_rom(self._1wire_addr):
                ## Note that even if this is a 1-byte address EEPROM and we write 2 bytes here,
                ## we will not overwrite the contents, since the actual write is only initiated
                ## on stop, not repeated start condition.
                # write TA1 TA2
                await self._1wire_iface.write(0xeeaaa, self._1wire_iface.bytes_to_bits(b"\x0f"))
                await self._1wire_iface.write(0xeee, self._1wire_iface.bytes_to_bits(address.to_bytes(2,'little')))
                data = await self._1wire_iface.write(0xee, self._1wire_iface.bytes_to_bits(b"\xde\xad\xbe\xef"))
                self._log("read data=<%s>", data.hex())

            return data


    async def readScratchpad(self, address: int, length: int) -> bytes:
        """Read :py:`length` bytes at :py:`address`.

        If :py:`address` is larger than the maximum representable given the address width (i.e.
        greater than 0x100 or 0x10000 for 1 and 2 address byte memories respectively), the "extra"
        address bits are carried over into the I²C address by addition. For example, calling
        :py:`read(0x10358, 0x100)` when configured with :py:`i2c_address=0x51, address_width=2`
        reads 0x100 bytes from memory address 0x358 at I²C device address 0x52.

        .. note::

            While this behavior is suitable for most memories, there may be cases where it is not
            appropriate; for example, if a memory wraps over at 0x100 byte or 0x10000 byte
            boundaries instead of returning data that would be read from the next I²C address over.

        Raises
        ------
        I2CNotAcknowledged
            If communication fails; if a previous write hasn't completed yet.
        """
        assert address >= 0 and length >= 0
        if length == 0:
            return b"" # can't do a 0-length I2C read

        self._log("read addr=%#06x len=%#06x", address, length)

        async with self._1wire_iface.select_rom(self._1wire_addr):
            ## Note that even if this is a 1-byte address EEPROM and we write 2 bytes here,
            ## we will not overwrite the contents, since the actual write is only initiated
            ## on stop, not repeated start condition.
            # write TA1 TA2
            await self._1wire_iface.write(0xeeaaa, self._1wire_iface.bytes_to_bits(b"\xaa"))
            # await self._1wire_iface.write(0xeee, self._1wire_iface.bytes_to_bits(address.to_bytes(2,'little')))
            data = await self._1wire_iface.read(length+3*8)

            self._log("read data=<%s>", data.hex())
            self._log("read bytes=<%s>", self._1wire_iface.bits_to_bytes(data).hex())


        return data
    
    async def copyScratchpad(self, auth: bytes | bytearray | memoryview) -> bytes:
        """Read :py:`length` bytes at :py:`address`.

        If :py:`address` is larger than the maximum representable given the address width (i.e.
        greater than 0x100 or 0x10000 for 1 and 2 address byte memories respectively), the "extra"
        address bits are carried over into the I²C address by addition. For example, calling
        :py:`read(0x10358, 0x100)` when configured with :py:`i2c_address=0x51, address_width=2`
        reads 0x100 bytes from memory address 0x358 at I²C device address 0x52.

        .. note::

            While this behavior is suitable for most memories, there may be cases where it is not
            appropriate; for example, if a memory wraps over at 0x100 byte or 0x10000 byte
            boundaries instead of returning data that would be read from the next I²C address over.

        Raises
        ------
        I2CNotAcknowledged
            If communication fails; if a previous write hasn't completed yet.
        """

        self._log("read addr=%#06x len", auth)

        async with self._1wire_iface.select_rom(self._1wire_addr):
            ## Note that even if this is a 1-byte address EEPROM and we write 2 bytes here,
            ## we will not overwrite the contents, since the actual write is only initiated
            ## on stop, not repeated start condition.
            # write TA1 TA2
            await self._1wire_iface.write(0xeeaaa, self._1wire_iface.bytes_to_bits(b"\x55"))
            await self._1wire_iface.write(0xeee, self._1wire_iface.bytes_to_bits(auth))
            
            await asyncio.sleep(0.005) # 50 ms
            while print(await self._1wire_iface.read(1)):
                await asyncio.sleep(0.050) # 50 ms

            self._log("cpiedscrpd")
            # self._log("read bytes=<%s>", self._1wire_iface.bits_to_bytes(data).hex())


        return auth


class _1_WireEepromApplet(GlasgowAppletV2):
    logger = logging.getLogger(__name__)
    help = "read and write 24-series I²C EEPROM memories"
    default_page_size = 8
    description = f"""
    Read and write memories compatible with 24-series EEPROM memory, such as Microchip 24C02C,
    Atmel 24C256, or hundreds of other memories that typically have "24X" (where X is some letter)
    in their part number.

    If the address of a read or write operation points past 255 (for one address byte memories) or
    65536 (for two address byte memories), the I²C address has the extra address bits added to it.
    For example, running ``glasgow memory-24x -A 0x51 -W 2 read 0x10358 0x100`` reads 0x100 bytes
    from memory address 0x358 at I²C device address 0x52.

    Page size
    ~~~~~~~~~

    The memory performs writes by first latching incoming data into a page buffer, and committing
    the page buffer after a stop condition. The internal address counter wraps around to the start
    of the page whenever the end of the page is reached. Using the correct page size is very
    important: while a smaller page size can always be used with a memory that has a larger actual
    page size, the inverse is not true. On the other hand, using the right page size significantly
    improves performance.

    The default page size in this applet is {default_page_size}, because no memories with page size
    less than {default_page_size} bytes have been observed so far. If the writes are too slow,
    look up the page size in the memory documentation. If the writes seem to be corrupted, use
    the ``--page-size 1`` option.

    The pinout of a typical 24-series IC is as follows (the A2:0 pins may be N/C in large devices):

    ::

          NC       @ * NC
          NC       * * NC
          DATA-PIN * * NC
          GND      * * NC
    """

    @classmethod
    def add_build_arguments(cls, parser, access):
        access.add_voltage_argument(parser)
        access.add_pins_argument(parser, "data_pin", default=True, required=True)

        def address(arg):
            return int(arg, 0)

        parser.add_argument(
            "-A", "--onewire-address", type=address, metavar="I2C-ADDR", default=None,
            help="Onewire address of the memory; 64-bit, tyically; "
                 "(default: SKIP_ROM)")
        parser.add_argument(
            "-P", "--page-size", type=int, metavar="PAGE-SIZE", default=cls.default_page_size,
            help="page size; writes will be split at addresses that are multiples of PAGE-SIZE")

    def build(self, args):
        with self.assembly.add_applet(self):
            self.assembly.use_voltage(args.voltage)
            self.onewire_iface = Bus1WireControllerInterface(self.logger, self.assembly,
                data_pin=args.data_pin)
            self.eeprom_iface = _1_WireEepromInterface(self.logger, self.onewire_iface,
                args.onewire_address, 1000)

    @classmethod
    def add_setup_arguments(cls, parser):
        parser.add_argument(
            "-f", "--frequency", metavar="FREQ", type=int, default=400,
            help="set SCL frequency to FREQ kHz (default: %(default)s, range: 100...4000)")

    async def setup(self, args):
        await self.onewire_iface.clock.set_frequency(args.frequency * 1000)

    @classmethod
    def add_run_arguments(cls, parser):
        def address(arg):
            return int(arg, 0)
        def length(arg):
            return int(arg, 0)
        def hex_bytes(arg):
            return bytes.fromhex(arg)

        p_operation = parser.add_subparsers(dest="operation", metavar="OPERATION", required=True)

        p_read = p_operation.add_parser(
            "read", help="read memory")
        p_read.add_argument(
            "address", metavar="ADDRESS", type=address,
            help="read memory starting at address ADDRESS, with wraparound")
        p_read.add_argument(
            "length", metavar="LENGTH", type=length,
            help="read LENGTH bytes from memory")
        p_read.add_argument(
            "-f", "--file", metavar="FILENAME", type=argparse.FileType("wb"),
            help="write memory contents to FILENAME")

        p_write = p_operation.add_parser(
            "write", help="write memory")
        p_write.add_argument(
            "address", metavar="ADDRESS", type=address,
            help="write memory starting at address ADDRESS")
        g_write_data = p_write.add_mutually_exclusive_group(required=True)
        g_write_data.add_argument(
            "-d", "--data", metavar="DATA", type=hex_bytes,
            help="write memory with DATA as hex bytes")
        g_write_data.add_argument(
            "-f", "--file", metavar="FILENAME", type=argparse.FileType("rb"),
            help="write memory with contents of FILENAME")

        p_verify = p_operation.add_parser(
            "verify", help="verify memory")
        p_verify.add_argument(
            "address", metavar="ADDRESS", type=address,
            help="verify memory starting at address ADDRESS")
        g_verify_data = p_verify.add_mutually_exclusive_group(required=True)
        g_verify_data.add_argument(
            "-d", "--data", metavar="DATA", type=hex_bytes,
            help="compare memory with DATA as hex bytes")
        g_verify_data.add_argument(
            "-f", "--file", metavar="FILENAME", type=argparse.FileType("rb"),
            help="compare memory with contents of FILENAME")

        p_readScratchpad = p_operation.add_parser(
            "readScratchpad",help="eee"
        )
        p_readScratchpad.add_argument(
            "-l", "--length", metavar="LENGTH", type=address,
            help="write memory with DATA as hex bytes")
        p_readScratchpad.add_argument(
            "address", metavar="ADDRESS", type=address,
            help="verify memory starting at address ADDRESS")
        p_readScratchpad.add_argument(
            "-f", "--file", metavar="FILENAME", type=argparse.FileType("rb"),
            help="compare memory with contents of FILENAME")


        p_writeScratchpad = p_operation.add_parser(
            "writeScratchpad",help="eee"
        )
        p_writeScratchpad.add_argument(
            "-d", "--data", metavar="DATA", type=hex_bytes,
            help="write memory with DATA as hex bytes")
        p_writeScratchpad.add_argument(
            "address", metavar="ADDRESS", type=address,
            help="verify memory starting at address ADDRESS")
        p_writeScratchpad.add_argument(
            "-f", "--file", metavar="FILENAME", type=argparse.FileType("rb"),
            help="compare memory with contents of FILENAME")

        p_copyScratchpad = p_operation.add_parser("copyScratchpad", help="aa")
        p_copyScratchpad.add_argument("-a", "--auth", metavar="AUTH", type=hex_bytes, help="gimmeauthbytes")



    async def run(self, args):
        if args.operation == "read":
            data = await self.eeprom_iface.read(args.address, args.length)
            if args.file:
                args.file.write(data)
            else:
                print(data.hex())

        if args.operation == "write":
            if args.data is not None:
                data = args.data
            if args.file is not None:
                data = args.file.read()
            await self.eeprom_iface.write(args.address, data)
        
        if args.operation == "writeScratchpad":
            data = args.data
            if args.file is not None:
                data = args.file.read()
            print(data.hex())
            await self.eeprom_iface.writeScratchpad(args.address, data)
            
        if args.operation == "readScratchpad":
            data = await self.eeprom_iface.readScratchpad(args.address, args.length)
            if args.file:
                args.file.write(data)
            else:
                print(data.hex())

        if args.operation == "copyScratchpad":
            await self.eeprom_iface.copyScratchpad(args.auth)
            

        if args.operation == "verify":
            if args.data is not None:
                gold_data = args.data
            if args.file is not None:
                gold_data = args.file.read()

            live_data = await self.eeprom_iface.read(args.address, len(gold_data))
            if live_data == gold_data:
                self.logger.info("verify PASS")
            else:
                for offset, (gold_byte, live_byte) in enumerate(zip(gold_data, live_data)):
                    if gold_byte != live_byte:
                        differs_at = args.address + offset
                        break
                self.logger.error("first differing byte at %#08x (expected %#04x, actual %#04x)",
                                  differs_at, gold_byte, live_byte)
                raise GlasgowAppletError("verify FAIL")

    @classmethod
    def tests(cls):
        from . import test
        return test.Memory24xAppletTestCase
