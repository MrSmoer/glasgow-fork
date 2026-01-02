# Ref: I2C-bus specification and user manual Rev 7.0
# Document Number: UM10204
# Accession: G00101

from amaranth import *
from amaranth.lib import io
from amaranth.lib.cdc import FFSynchronizer


__all__ = ["Bus1WireController"]


class Bus1Wire(Elaboratable):
    """I2C bus.

    Decodes bus conditions (start, stop, sample and setup) and provides synchronization.
    """

    def __init__(self, pads, divider):
        self.period_cyc = int(divider)
        self.pads = pads
        self.divisor = Signal(16, init=self.period_cyc // 4)
        self.reset_timeout = Signal()

        self.data_i = Signal(name="bus_data_i")
        self.data_ready = Signal(name="bus_data_ready")
        self.data_pin_i = Signal()
        self.data_o = Signal(name="bus_data_o", init = 1)

        self.reset_detect  = Signal(name="bus_reset_detect")
        self.data_pin_o = Signal()
        self.timer = Signal.like(self.divisor, init=0)
        self.timersource = Signal(init = 0)
        self.divisor2 = Signal.like(self.divisor, init = 0)
        self.falling_edge_strobe = Signal()

    def elaborate(self, platform):
        m = Module()

        m.submodules.io_data = data_io_buffer = io.Buffer("io", self.pads.data_pin)

        
        reset_timer = Signal.like(self.reset_timeout, init = -1)

        delayed_data_pin_i = Signal()

        m.d.sync += [
            delayed_data_pin_i.eq(self.data_pin_i)
        ]

        m.d.comb += [
            data_io_buffer.o.eq(0),
            data_io_buffer.oe.eq(~self.data_pin_o),

            self.falling_edge_strobe.eq(delayed_data_pin_i & ~self.data_pin_i),
        ]


        with m.If((self.timer == 0) & self.falling_edge_strobe):
            m.d.sync += self.timer.eq(self.divisor)
        with m.Elif(self.timer != 0):
            m.d.sync += self.timer.eq(self.timer - 1)

        # Sampling should happen one quarter into the shortest window-size
        with m.If(self.timer == (self.divisor - (self.divisor >> 3))):
            m.d.sync += self.data_i.eq(self.data_pin_i)
            m.d.sync += self.data_ready.eq(1)
        with m.Else():
            m.d.sync += self.data_ready.eq(0)


        with m.If(self.data_pin_i == 1):
            m.d.sync += reset_timer.eq(self.reset_timeout)
        with m.Elif(reset_timer != 0):
            m.d.sync += reset_timer.eq(reset_timer - 1)

        m.d.comb += [self.reset_detect.eq(reset_timer == 0),]

        m.submodules += [
            FFSynchronizer(data_io_buffer.i, self.data_pin_i, init=1),
        ]

        return m




class Bus1WireController(Elaboratable):

    """Simple I2C transaction initiator.

    Generates start and stop conditions, and transmits and receives octets.
    Clock stretching is supported.

    :param period_cyc:
        Bus clock period, as a multiple of system clock period.
    :type period_cyc: int

    :attr busy:
        Busy flag. Low if the state machine is idle, high otherwise.
    :attr write:
        Write strobe. When ``busy`` is low, asserting ``write`` for one cycle receives
        a bit on the bus and latches it to ``data_o``. Ignored when ``busy`` is high.
    :attr data_i:
        Data bit to be transmitted. Latched immediately after ``write`` is asserted.
    :attr read:
        Read strobe. When ``busy`` is low, asserting ``read`` for one cycle latches
        ``data_i`` and transmits it on the bus. Ignored when ``busy`` is high.
    :attr data_o:
        Received data octet.
    :attr reset:
        Assert when ``busy`` is low, asserting ``reset`` for one cycle generates
        a reset on the bus. Ignored when ``busy`` is high.
        Whether a device generated a presence pulse can be read from data_i busy is down again
    """

    def __init__(self, pads, period_cyc, pulsetimer):
        assert (period_cyc // 4) < (1 << 16)

        self.period_cyc = int(period_cyc)
        self.controller_timer = int(pulsetimer)

        self.busy   = Signal(init=0)
        self.read   = Signal(init=0)
        self.data_i = Signal()
        self.write  = Signal(init=0)
        self.data_o = Signal()
        self.reset  = Signal()
        self.divisor = Signal(16, init=self.period_cyc)

        self.requested_type = Signal()

        self.bus = Bus1Wire(pads, period_cyc)

        self.controller_timer = Signal(24, init = 0)
        self.controller_timer_reset = Signal.like(self.controller_timer)


    def elaborate(self, platform):
        m = Module()

        m.submodules.bus = self.bus

        m.d.comb += self.bus.divisor.eq(self.divisor)

        with m.If(self.controller_timer_reset):
            m.d.sync += self.controller_timer.eq(self.controller_timer_reset)
            m.d.sync += self.controller_timer_reset.eq(0)
        with m.Elif(self.controller_timer != 0):
            m.d.sync += self.controller_timer.eq(self.controller_timer - 1)
            

        timdown = Signal(init=0)
        # timer = Signal.like(self.divisor)
        with m.FSM(init="IDLE") as fsm:
            self._fsm = fsm
            m.d.comb += self.bus.data_pin_o.eq(1)
            with m.State("IDLE"):
                with m.If(self.reset | self.read | self.write):
                    m.d.sync += self.busy.eq(1)
                    with m.If(self.reset):
                        m.next = "RESET"

                    with m.Elif(self.read):
                        m.d.sync += self.requested_type.eq(0)
                        m.next = "PULSE"

                    with m.Elif(self.write):
                        m.d.sync += self.bus.data_o.eq(self.data_o)
                        m.d.sync += self.requested_type.eq(1)
                        m.next = "PULSE"

                with m.Else():
                    m.d.sync += self.busy.eq(0)


            with m.State("PULSE"):
                m.d.comb += self.bus.data_pin_o.eq(0)
                m.d.sync += self.controller_timer_reset.eq(self.divisor>>5)
                m.next = "PULSE_WAIT"

            with m.State("PULSE_WAIT"):
                m.d.comb += self.bus.data_pin_o.eq(0)
                with m.If((self.controller_timer == 0) & (self.controller_timer_reset == 0)):
                    with m.If(self.requested_type):
                        m.next = "WRITING"
                    with m.Else():
                        m.d.comb += self.bus.data_pin_o.eq(1)
                        m.next = "READING"

            with m.State("READING"):
                with m.If(self.bus.data_ready):
                    m.d.sync += self.data_i.eq(self.bus.data_i)
                    m.next = "WAITING"

            with m.State("WRITING"):
                m.d.comb += self.bus.data_pin_o.eq(self.bus.data_o)
                with m.If(self.bus.timer == ((self.divisor>>1))):
                    m.next = "WAITING"

            with m.State("WAITING"):
                m.d.comb += self.bus.data_pin_o.eq(1)
                with m.If(self.bus.timer == 0):
                    m.next = "IDLE"

            with m.State("RESET"):
                m.d.comb += self.bus.data_pin_o.eq(0)
                m.d.sync += self.controller_timer_reset.eq(self.divisor*8)
                m.d.sync += timdown.eq(0)
                m.next = "RESETTING"

            with m.State("RESETTING"):
                m.d.comb += self.bus.data_pin_o.eq(0)

                with m.If(((self.controller_timer == 0) & (self.controller_timer_reset == 0)) | timdown):
                    m.d.sync += timdown.eq(1)
                    m.d.comb += self.bus.data_pin_o.eq(1)
                    # wait for risetime until the bus is actually high again. This assumes a sane bus
                    with m.If(self.bus.data_pin_i):
                        m.d.sync += self.controller_timer_reset.eq(self.divisor*8)
                        m.next = "WAIT_PRESENCE"

            with m.State("WAIT_PRESENCE"):
                with m.If(~self.bus.data_pin_i):
                    m.d.sync += self.data_i.eq(1)
                with m.If((self.controller_timer == 0) & (self.controller_timer_reset==0)):
                    m.next = "IDLE"

        return m

