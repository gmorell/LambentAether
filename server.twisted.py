# !/usr/bin/env python
import cgi
import json
import serial
from twisted.application import internet
from twisted.application import service

from twisted.internet import defer
from twisted.internet import protocol
from twisted.internet import reactor
from twisted.internet import task
from twisted.internet import utils

from twisted.protocols import basic

from twisted.web import client
from twisted.web import resource
from twisted.web import server
from twisted.web import static

from simpleprogs import WaitingCounter
from helpers import DummySerialDevice

LED_COUNT = 240
LED_PORT = "/dev/ttyACM0"
LED_DURATION = 600
LED_FADE_TIME = 0.05
LED_FADE_STEPS = 30

GLOBAL_KWARGS = {
    "led_count": LED_COUNT,
    "run_duration": LED_DURATION,
    "fade_time": LED_FADE_TIME,
    "fade_steps": LED_FADE_STEPS,
}
# ## TODO,
# try this out with the serial debug device
# add the various lighting programs and presets to the array


from config import avail_progs


class TelnetLightProtocol(basic.LineReceiver):
    avail_progs = avail_progs
    current_value = "default"

    def lineReceived(self, line):
        countah = self.factory.counter
        if line in ["reset", "r", "r:"]:
            countah.reset()
            self.transport.write("Reset currently running counter. \r\n")
            return

        if line == "kill":
            self.transport.loseConnection()
        if "c:" in line:
            self.transport.write("Currently running: %s\r\n" % self.current_value)
        elif "p:" in line:
            val = line.rsplit('p: ')[1]
            prog = self.avail_progs.get(val, None)
            if prog:
                self.factory.change_program(prog, val)
                self.transport.write("Changed to %s.\r\n" % val)
            else:
                self.transport.write("No Such Prog")

        elif "d:" in line:
            val = line.rsplit('d: ')[1]
            # todo
            # self.device = somewayofgettingthedevice
            # self.change_program(self.current_prog, self.current_val)


        else:
            self.transport.write(str(countah.proto_value()) + "\r\n")
            # return usr.counter
            # def onError(err):
            # return "error"
            #
            # usr.addErrback(onError)
            #
            # def writeAResp(msg):
            # self.transport.write(msg + "\r\n")
            # self.transport.loseConnection()
            #
            # usr.addCallback(writeAResp)


class LightFactory(protocol.ServerFactory):
    protocol = TelnetLightProtocol

    def __init__(self, counter, loop, device, **kwargs):
        self.counter = counter
        self.loop = loop
        self.device = device

    def getCntr(self):
        return self.counter
        # return defer.succeed(self.users.get(user, None))
        # return utils.getProcessOutput("finger", [user])
        # return client.getPage("http://gmp.io")

    def setCntr(self, cntr):
        self.counter = cntr

    def setLoop(self, loop):
        self.loop = loop


class LightHTMLTree(resource.Resource):
    def __init__(self, service):
        resource.Resource.__init__(self)
        self.service = service

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json; charset=utf-8")
        r_dict = {
            "status": "/status",
            "programs": "/progs",
            "set": "/set",
        }
        retval = json.dumps(r_dict)
        return retval


class LightStatus(resource.Resource):
    def __init__(self, service):
        resource.Resource.__init__(self)
        self.service = service

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json; charset=utf-8")
        status = self.service.current_value
        retval = json.dumps({"running": status})
        return retval


class LightProgramSetter(resource.Resource):
    def __init__(self, service):
        resource.Resource.__init__(self)
        self.service = service

    def handle_get_post(self, prog):
        if prog:
            val = prog[0]
            if val not in self.service.available_progs.keys():
                return json.dumps(
                    {
                        "status": "ERROR_NON_EXISTENT_PROGRAM_PARAMETER",
                        "value": val
                    }
                )
            else:
                prog = self.service.available_progs.get(val, None)
                self.service.change_program(prog, val)
                return json.dumps(
                    {
                        "status": "SUCCESS_CHANGED_PROGRAM",
                        "value": val
                    }
                )
        return json.dumps(
            {
                "status": "ERROR_MISSING_PROGRAM_PARAMETER",
                "value": "prog"
            }
        )

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json; charset=utf-8")
        prog = request.args.get('prog', None)
        return self.handle_get_post(prog)

    def render_POST(self, request):
        request.setHeader("Content-Type", "application/json; charset=utf-8")
        prog = request.args.get('prog', None)
        return self.handle_get_post(prog)


class LightProgramList(resource.Resource):
    def __init__(self, service):
        resource.Resource.__init__(self)
        self.service = service

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json; charset=utf-8")
        status = sorted(self.service.available_progs.keys())
        retval = json.dumps({"available_progs": status})
        return retval


class LightService(service.Service):
    def __init__(self, counter=None, loop=None, device=DummySerialDevice(), step_time=0.1, current_value="default",
                 avail_progs=None, **kwargs):
        self.current_value = current_value
        self.step_time = step_time
        self.available_progs = avail_progs

        if not counter:
            self.counter = WaitingCounter(5)
        else:
            self.counter = counter

        if not loop:
            self.loop = task.LoopingCall(self.counter.step)
            self.loop.start(self.step_time)
        else:
            self.loop = loop

        if not device:
            self.device = device
        else:
            self.device = device

    def getCntr(self):
        return self.counter

    def setCntr(self, cntr):
        self.counter = cntr

    def setLoop(self, loop):
        self.loop = loop

    def setLoopInterval(self, value):
        self.step_time = value
        self.loop.stop()
        self.loop.start(self.step_time)

    def change_program(self, prog, val):
        self.current_value = val

        # # stop the existing one
        loop_old = self.loop
        loop_old.stop()

        # # setup
        self.program_class = prog['class']
        self.program_args = prog['kwargs']

        self.program_args['device'] = self.device
        self.program_args.update(GLOBAL_KWARGS)
        initiated_prog = self.program_class(**self.program_args)

        loop_new = task.LoopingCall(initiated_prog.step)
        loop_new.start(self.step_time)
        self.setLoop(loop_new)
        self.setCntr(initiated_prog)

    def getLightFactory(self):
        f = protocol.ServerFactory()
        f.protocol = TelnetLightProtocol
        f.counter = self.counter
        f.loop = self.loop
        f.device = self.device

        f.setLoop = self.setLoop
        f.setCntr = self.setCntr
        f.change_program = self.change_program
        return f

    def getLightResource(self):
        r = LightHTMLTree(self)
        r.putChild("", r)

        st = LightStatus(self)
        r.putChild("status", st)

        pl = LightProgramList(self)
        r.putChild("progs", pl)

        se = LightProgramSetter(self)
        r.putChild("set", se)

        return r


if __name__ == "__main__":
    device = DummySerialDevice()
    # device = serial.Serial(LED_PORT, 115200)

    # ctr = WaitingCounter(5)
    # l = task.LoopingCall(ctr.step)
    # l.start(0.1)
    # reactor.listenTCP(1079, LightFactory(counter=ctr, loop=l, device=device))
    # reactor.run()

application = service.Application('lambent_aether')  # , uid=1, gid=1)
s = LightService(avail_progs=avail_progs)
serviceCollection = service.IServiceCollection(application)
s.setServiceParent(serviceCollection)
internet.TCPServer(8660, s.getLightFactory()).setServiceParent(serviceCollection)
internet.TCPServer(8680, server.Site(s.getLightResource())).setServiceParent(serviceCollection)
