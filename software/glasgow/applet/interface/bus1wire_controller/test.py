from amaranth import *
from amaranth.lib import wiring, io
from amaranth.sim import Simulator

from glasgow.gateware.ports import PortGroup
from glasgow.gateware.jtag import tap as jtag_tap
from glasgow.applet import GlasgowAppletV2TestCase, synthesis_test
from . import I2CNotAcknowledged, Bus1WireControllerApplet, Bus1WireControllerComponent


def bits(*args):
    return sum(bit * (1 << place) for place, bit in enumerate(args))


async def stream_get(ctx, stream):
    ctx.set(stream.ready, 1)
    payload, = await ctx.tick().sample(stream.payload).until(stream.valid)
    ctx.set(stream.ready, 0)
    return payload


async def stream_put(ctx, stream, payload):
    ctx.set(stream.payload, payload)
    ctx.set(stream.valid, 1)
    await ctx.tick().until(stream.ready)
    ctx.set(stream.valid, 0)


class Bus1WireControllerAppletTestCase(GlasgowAppletV2TestCase, applet=Bus1WireControllerApplet):
    def testonewire(self):

        pin_ports = PortGroup()
        pin_ports.data_pin = io.SimulationPort("io", 1, name="data_pin")

        m = Module()
        testsig = Signal()
        m.submodules.probe = controller = Bus1WireControllerComponent(pin_ports)
        m.d.comb += [
            pin_ports.data_pin.i.eq(~(pin_ports.data_pin.oe&(~pin_ports.data_pin.o))),
            controller.divisor.eq(100),
            controller.pulsetimer_value.eq(10)
            
        ]

        async def i_testbench(ctx):
            # await stream_put(ctx, controller.i_stream,
            #     )
            # await stream_put(ctx, controller.i_stream, 0x00)
            datee = [0,1,1,0,1,1,0]
            bytestream = bytearray(b"")
            for val in datee:
                thround=bytearray(b"\x00\x02\x01\x00")
                thround.append(val)
                thround.append(0x01)
                bytestream.extend(thround)
            # raise RuntimeError(f"bytestream {bytestream}")
            for b in bytestream:
                await stream_put(ctx, controller.i_stream, b)
            await stream_put(ctx, controller.i_stream, 0x02)
            # await stream_put(ctx, controller.i_stream,
            #     {"len": 7, "tms": bits(0,1,1,0,0,0,1),   "tdi": bits(0,0,0,0,0,0,1)})
            # await stream_put(ctx, controller.i_stream,
            #     {"len": 5, "tms": bits(1,0,1,0,0),       "tdi": bits(0,0,0,0,0)})
            # await stream_put(ctx, controller.i_stream,
            #     {"len": 8, "tms": bits(0,0,0,0,0,0,0,0), "tdi": 0})
            # await stream_put(ctx, controller.i_stream,
            #     {"len": 8, "tms": bits(0,0,0,0,0,0,0,0), "tdi": 0})
            # await stream_put(ctx, controller.i_stream,
            #     {"len": 8, "tms": bits(0,0,0,0,0,0,0,0), "tdi": 0})
            # await stream_put(ctx, controller.i_stream,
            #     {"len": 8, "tms": bits(0,0,0,0,0,0,0,1), "tdi": 0})

        async def o_testbench(ctx):
            while(1):
                await stream_get(ctx, controller.o_stream)
            # await stream_get(ctx, controller.o_stream)
            # await stream_get(ctx, controller.o_stream)
            # assert (await stream_get(ctx, controller.o_stream)) == {"tdo": 0b10101001}
            # assert (await stream_get(ctx, controller.o_stream)) == {"tdo": 0b00000000}
            # assert (await stream_get(ctx, controller.o_stream)) == {"tdo": 0b00001111}
            # assert (await stream_get(ctx, controller.o_stream)) == {"tdo": 0b00111111}

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(i_testbench)
        sim.add_testbench(o_testbench)
        with sim.write_vcd("test_onewire.vcd"):
            sim.run()

    @synthesis_test
    def test_build(self):
        self.assertBuilds()
