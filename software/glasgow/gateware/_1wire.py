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
        self.data_o = Signal(name="bus_data_o", init = 1)

        self.reset_detect  = Signal(name="bus_reset_detect")
        self.data_pin_o = Signal()
        self.timer = Signal.like(self.divisor, init=0)

    def elaborate(self, platform):
        m = Module()

        m.submodules.io_data = data_io_buffer = io.Buffer("io", self.pads.data_pin)

        
        reset_timer = Signal.like(self.reset_timeout, init = -1)
        data_pin_i = Signal()

        falling_edge_strobe = Signal()
        delayed_data_pin_i = Signal()

        m.d.sync += [
            delayed_data_pin_i.eq(data_pin_i)
        ]

        m.d.comb += [
            data_io_buffer.o.eq(0),
            data_io_buffer.oe.eq(~self.data_pin_o),

            falling_edge_strobe.eq(delayed_data_pin_i & ~data_pin_i),
        ]


        with m.If((self.timer == 0) & falling_edge_strobe):
            m.d.sync += self.timer.eq(self.divisor)
        with m.Elif(self.timer != 0):
            m.d.sync += self.timer.eq(self.timer - 1)

        # Sampling should happen one quarter into the shortest window-size
        with m.If(self.timer == (self.divisor - self.divisor // 4)):
            m.d.sync += self.data_i.eq(data_pin_i)
            m.d.sync += self.data_ready.eq(1)
        with m.Else():
            m.d.sync += self.data_ready.eq(0)



        with m.If(data_pin_i == 1):
            m.d.sync += reset_timer.eq(self.reset_timeout)
        with m.Elif(reset_timer != 0):
            m.d.sync += reset_timer.eq(reset_timer - 1)

        m.d.comb += [self.reset_detect.eq(reset_timer == 0),]



        m.submodules += [
            FFSynchronizer(data_io_buffer.i, data_pin_i, init=1),
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
    """

    def __init__(self, pads, period_cyc, pulsetimer):
        assert (period_cyc // 4) < (1 << 16)

        self.period_cyc = int(period_cyc)
        self.pulsetimer = int(pulsetimer)

        self.busy   = Signal(init=0)
        self.read   = Signal()
        self.data_i = Signal()
        self.write  = Signal()
        self.data_o = Signal()

        self.requested_type = Signal()

        self.strobe = Signal()

        self.bus = Bus1Wire(pads, period_cyc)

        self.pulsetimer = Signal(16, init = 0)
        self.pulsetimer_value = Signal.like(self.pulsetimer, init = pulsetimer)

    def elaborate(self, platform):
        m = Module()

        m.submodules.bus = self.bus


        # timer = Signal.like(self.divisor)
        with m.FSM(init="IDLE") as fsm:
            self._fsm = fsm

            with m.State("IDLE"):
                with m.If(self.read):
                    m.d.sync += self.busy.eq(1)
                    m.d.sync += self.requested_type.eq(0)
                    m.next = "PULSE"
                with m.Elif(self.write):
                    m.d.sync += self.busy.eq(1)
                    m.d.sync += self.bus.data_o.eq(self.data_o)
                    m.d.sync += self.requested_type.eq(1)
                    m.next = "PULSE"
                with m.Else():
                    m.d.sync += self.busy.eq(0)
                

            with m.State("PULSE"):
                m.d.comb += self.bus.data_pin_o.eq(0)
                m.d.sync += self.busy.eq(1)

                with m.If(self.pulsetimer == 0):
                    m.d.sync += self.pulsetimer.eq(self.pulsetimer_value)

                with m.Elif(self.pulsetimer == 1):
                    m.d.sync += self.pulsetimer.eq(0)
                    with m.If(self.requested_type):
                        m.next = "WRITING"
                    with m.Else():
                        m.next = "READING"

                with m.Else():
                    m.d.sync += self.pulsetimer.eq(self.pulsetimer - 1)
            with m.State("READING"):
                m.d.sync += self.busy.eq(1)
                with m.If(self.bus.data_ready):
                    m.d.sync += self.data_i.eq(self.bus.data_i)
                    m.next = "WAITING"



            with m.State("WRITING"):
                m.d.sync += self.busy.eq(1)
                m.d.comb += self.bus.data_pin_o.eq(self.bus.data_o)
                with m.If(self.bus.timer == 42):
                    m.next = "WAITING"

            with m.State("WAITING"):
                m.d.sync += self.busy.eq(1)
                m.d.comb += self.bus.data_pin_o.eq(1)
                with m.If(self.bus.timer == 0):
                    m.next = "IDLE"
        return m

