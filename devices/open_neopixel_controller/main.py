#!/usr/bin/env micropython
"""
Neopixel Controller

MIT license
(C) Konstantin Belyalov 2017-2018
"""

import logging
import micropython
import machine
import network
import uasyncio as asyncio

import tinyweb
import tinydns
import tinymqtt

import platform.utils.captiveportal
from platform.btn.setup import SetupButton
from platform.led.neopixel import Neopixel
from platform.led.status import StatusLed
from platform.utils.wifi import WifiSetup
from platform.utils.config import SimpleConfig
from platform.sensor.ambient import AmbientAnalogSensor


neopixel_pin = const(4)
setup_btn_pin = const(5)
status_led_pin = const(10)

log = logging.Logger('main')


def parse_pixels(data):
    parsed = []
    for leds, hexcolor in data.items():
        # Convert hex color to int
        try:
            if hexcolor.startswith('#'):
                hexcolor = hexcolor[1:]
            bcolor = int(hexcolor, 16).to_bytes(4, 'big')
        except ValueError:
            raise ValueError('Invalid color format.')
        # All pixels
        if leds.lower() == 'all':
            parsed.append((None, bcolor))
            continue
        # Range, e.g. "1-10"
        if '-' in leds:
            arr = leds.split('-')
            if len(arr) > 2 or not arr[0].isdigit() or not arr[1].isdigit():
                raise ValueError('Invalid range format')
            r1 = int(arr[0])
            r2 = int(arr[1])
        elif leds.isdigit():
            r1 = int(leds)
            r2 = r1
        else:
            raise ValueError('Invalid color format')
        if r1 < 1:
            raise ValueError('Invalid range format')
        parsed.append((range(r1 - 1, r2), bcolor))
    return parsed


class NeopixelStrip(Neopixel):
    def __init__(self, config, mqtt, loop):
        super().__init__(machine.Pin(neopixel_pin), config, loop)
        self.mqtt = mqtt
        self.cfg.add_param('mqtt_topic_led_status', 'neopixel/led')
        self.cfg.add_param('mqtt_topic_led_control', 'neopixel/led/set')
        self.mqtt.subscribe(self.cfg.mqtt_topic_led_control, self.mqtt_control)

    def post(self, data):
        print(data)
        # In case of no input parameters - just use recent color map
        if not len(data):
            data = {'all': '#00ff00'}
        self.np.fade_in(parse_pixels(data), length=20, delay=20)
        return {'message': 'color changed'}

    def mqtt_control(self, data):
        print(data)


async def shutdown_wait():
    """Helper to make graceful app shutdown"""
    await asyncio.sleep_ms(100)


def main():
    # Some ports requires to allocate extra mem for exceptions
    if hasattr(micropython, 'alloc_emergency_exception_buf'):
        micropython.alloc_emergency_exception_buf(100)

    loop = asyncio.get_event_loop()
    logging.basicConfig(level=logging.DEBUG)

    # Base config
    config = SimpleConfig()
    config.add_param('configured', False)
    wsetup = WifiSetup(config)

    # MQTT
    mqtt = tinymqtt.MQTTClient('neopixelcontroller-{}'.format(
        platform.utils.mac_last_digits()), config=config)

    # DNS
    dns = tinydns.Server(ttl=10)

    # WebServer
    web = tinyweb.webserver()

    # Modules
    ledstrip = NeopixelStrip(config, mqtt, loop)
    ambi = AmbientAnalogSensor(config, mqtt, machine.ADC(0))
    setupbtn = SetupButton(config, machine.Pin(setup_btn_pin))
    status = StatusLed(config, machine.Pin(status_led_pin))

    # Web Rest API
    web.add_resource(config, '/config')
    web.add_resource(wsetup, '/wifi')
    web.add_resource(ledstrip, '/ledstrip')

    # Other web routes
    @web.route('/')
    async def index(req, resp):
        if config.configured:
            await resp.redirect('/dashboard')
        else:
            await resp.redirect('/setup')

    @web.route('/dashboard')
    async def page_dashboard(req, resp):
        await resp.send_file('dashboard_all.html.gz',
                             content_encoding='gzip',
                             content_type='text/html')

    @web.route('/setup')
    async def page_setup(req, resp):
        await resp.send_file('setup_all.html.gz',
                             content_encoding='gzip',
                             content_type='text/html')

    # Setup AP parameters
    ap_if = network.WLAN(network.AP_IF)
    essid = b'NeoPixelCtrl-%s' % platform.utils.mac_last_digits()
    ap_if.active(True)
    ap_if.config(essid=essid, authmode=network.AUTH_WPA_WPA2_PSK, password=b'neopixel')
    ap_if.ifconfig(('192.168.168.1', '255.255.255.0', '192.168.168.1', '192.168.168.1'))
    ap_if.active(False)
    # Captive portal
    platform.utils.captiveportal.enable(web, dns, '192.168.168.1')

    # Load configuration
    try:
        config.load()
    except Exception as e:
        log.warning('Config load failed: {}'.format(e))
        pass

    # Main loop
    try:
        wport = 80
        dport = 53
        if platform.utils.is_emulator():
            wport = 8080
            dport = 5335
        # Start services
        dns.run(host='0.0.0.0', port=dport, loop=loop)
        web.run(host='0.0.0.0', port=wport, loop_forever=False, loop=loop)
        mqtt.run(loop)
        ambi.run(loop)
        setupbtn.run(loop)
        status.run(loop)

        # Run main loop
        loop.run_forever()
    except KeyboardInterrupt as e:
        print(', terminating...')
        for s in [web, dns, mqtt, ambi, setupbtn, status]:
            s.shutdown()
        loop.run_until_complete(shutdown_wait())


if __name__ == '__main__':
    main()
