#!/usr/bin/env python3
# NeoPixel library strandtest example
# Author: Tony DiCola (tony@tonydicola.com)
#
# Direct port of the Arduino NeoPixel library strandtest example.  Showcases
# various animations on a strip of NeoPixels.
import argparse
import base64
import hashlib
import json
import logging
import os
import random
import socket
import ssl
import sys
import requests
import time
import fcntl
import struct
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from rpi_ws281x import *
from socketserver import ThreadingMixIn
from socketserver import ThreadingMixIn
from subprocess import Popen, check_output, call
from threading import Thread
from time import sleep, strftime

# LED strip configuration:
LED_COUNT      = 120      # Number of LED pixels.
LED_PIN        = 18      # GPIO pin connected to the pixels (18 uses PWM!).
#LED_PIN        = 10      # GPIO pin connected to the pixels (10 uses SPI /dev/spidev0.0).
LED_FREQ_HZ    = 800000  # LED signal frequency in hertz (usually 800khz)
LED_DMA        = 10      # DMA channel to use for generating signal (try 10)
LED_BRIGHTNESS = 255     # Set to 0 for darkest and 255 for brightest
LED_INVERT     = False   # True to invert the signal (when using NPN transistor level shift)
LED_CHANNEL    = 0       # set to '1' for GPIOs 13, 19, 41, 45 or 53

LEDS_PER_HUE_LIGHT = 30
HUE_LIGHTS_COUNT = LED_COUNT / LEDS_PER_HUE_LIGHT


# Define functions which animate LEDs in various ways.
def colorWipe(strip, color, wait_ms=50):
    """Wipe color across display a pixel at a time."""
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
        strip.show()
        time.sleep(wait_ms/1000.0)

def theaterChase(strip, color, wait_ms=50, iterations=10):
    """Movie theater light style chaser animation."""
    for j in range(iterations):
        for q in range(3):
            for i in range(0, strip.numPixels(), 3):
                strip.setPixelColor(i+q, color)
            strip.show()
            time.sleep(wait_ms/1000.0)
            for i in range(0, strip.numPixels(), 3):
                strip.setPixelColor(i+q, 0)

def wheel(pos):
    """Generate rainbow colors across 0-255 positions."""
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    else:
        pos -= 170
        return Color(0, pos * 3, 255 - pos * 3)

def rainbow(strip, wait_ms=20, iterations=1):
    """Draw rainbow that fades across all pixels at once."""
    for j in range(256*iterations):
        for i in range(strip.numPixels()):
            strip.setPixelColor(i, wheel((i+j) & 255))
        strip.show()
        time.sleep(wait_ms/1000.0)

def rainbowCycle(strip, wait_ms=20, iterations=5):
    """Draw rainbow that uniformly distributes itself across all pixels."""
    for j in range(256*iterations):
        for i in range(strip.numPixels()):
            strip.setPixelColor(i, wheel((int(i * 256 / strip.numPixels()) + j) & 255))
        strip.show()
        time.sleep(wait_ms/1000.0)

def theaterChaseRainbow(strip, wait_ms=50):
    """Rainbow movie theater light style chaser animation."""
    for j in range(256):
        for q in range(3):
            for i in range(0, strip.numPixels(), 3):
                strip.setPixelColor(i+q, wheel((i+j) % 255))
            strip.show()
            time.sleep(wait_ms/1000.0)
            for i in range(0, strip.numPixels(), 3):
                strip.setPixelColor(i+q, 0)

# protocols = [yeelight, tasmota, native_single, native_multi]

cwd = os.path.split(os.path.abspath(__file__))[0]

def pretty_json(data):
    return json.dumps(data, sort_keys=True,                  indent=4, separators=(',', ': '))

run_service = True

bridge_config = defaultdict(lambda:defaultdict(str))
new_lights = {}
sensors_state = {}

class HueHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'nginx'
    sys_version = ''

    def _set_headers(self, plain=False):
        self.send_response(200)
        mimetypes = {"json": "application/json", "map": "application/json", "html": "text/html", "xml": "application/xml", "js": "text/javascript", "css": "text/css", "png": "image/png"}
        if plain:
            self.send_header('Content-type', 'text/plain')
        elif self.path.endswith((".html",".json",".css",".map",".png",".js", ".xml")):
            self.send_header('Content-type', mimetypes[self.path.split(".")[-1]])
        elif self.path.startswith("/api"):
            self.send_header('Content-type', mimetypes["json"])
        else:
            self.send_header('Content-type', mimetypes["html"])

    def _set_AUTHHEAD(self):
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm=\"Hue\"')
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def _set_end_headers(self, data):
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        #Some older Philips Tv's sent non-standard HTTP GET requests with a Content-Length and a
        # body. The HTTP body needs to be consumed and ignored in order to request be handle correctly.
        global bridge_config
        self.read_http_request_body()

        if self.path == '/' or self.path == '/index.html':
            self._set_headers()
            f = open(cwd + '/web-ui/index.html')
            self._set_end_headers(bytes(f.read(), "utf8"))
        elif self.path.endswith((".css",".map",".png",".js")):
            self._set_headers()
            f = open(cwd + '/web-ui' + self.path, 'rb')
            self._set_end_headers(f.read())
        elif self.path == '/detect':
            self._set_headers(plain=True)
            self._set_end_headers(bytes(json.dumps(
                    {"hue": "strip","lights": HUE_LIGHTS_COUNT ,"name": 'StriPi',"modelid": "LST002", "mac": bridge_config["config"]["mac"]}
                ), "utf8"))
        # elif self.path == '/get':
        #     self._set_headers(plain=True)
        #     self._set_end_headers(bytes(json.dumps([{"on": power_status,
        #                                              "bri": bri[light],
        #                                              "xy": [x[light], y[light]],
        #                                              "ct": ct[light],
        #                                              "sat": sat[light],
        #                                              "hue": hue[light],
        #                                              "colormode": colormode}])))
        elif self.path == '/on':
            self._set_headers()
            colorWipe(strip, Color(255, 0, 0))  # Red wipe
            self._set_end_headers(bytes(json.dumps([{"success":{"configuration":"saved","filename":"/opt/hue-emulator/config.json"}}] ,separators=(',', ':')), "utf8"))
        elif self.path == '/off':
            self._set_headers()
            colorWipe(strip, Color(0, 0, 0))  # Clear wipe
            self._set_end_headers(bytes(json.dumps([{"success":{"configuration":"saved","filename":"/opt/hue-emulator/config.json"}}] ,separators=(',', ':')), "utf8"))
        else:
            url_pices = self.path.rstrip('/').split('/')
            if len(url_pices) < 3:
                #self._set_headers_error()
                self.send_error(404, 'not found')
                return
            else:
                self._set_headers()
            if url_pices[2] in bridge_config["config"]["whitelist"]: #if username is in whitelist
                bridge_config["config"]["UTC"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                bridge_config["config"]["localtime"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                bridge_config["config"]["whitelist"][url_pices[2]]["last use date"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                bridge_config["config"]["linkbutton"] = int(bridge_config["linkbutton"]["lastlinkbuttonpushed"]) + 30 >= int(datetime.now().strftime("%s"))
                if len(url_pices) == 3: #print entire config
                    self._set_end_headers(bytes(json.dumps({"lights": bridge_config["lights"], "groups": bridge_config["groups"], "config": bridge_config["config"], "scenes": bridge_config["scenes"], "schedules": bridge_config["schedules"], "rules": bridge_config["rules"], "sensors": bridge_config["sensors"], "resourcelinks": bridge_config["resourcelinks"]},separators=(',', ':')), "utf8"))
                elif len(url_pices) == 4: #print specified object config
                    self._set_end_headers(bytes(json.dumps(bridge_config[url_pices[3]],separators=(',', ':')), "utf8"))
                elif len(url_pices) == 5:
                    if url_pices[4] == "new": #return new lights and sensors only
                        new_lights.update({"lastscan": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")})
                        self._set_end_headers(bytes(json.dumps(new_lights ,separators=(',', ':')), "utf8"))
                        new_lights.clear()
                    elif url_pices[3] == "groups" and url_pices[4] == "0":
                        any_on = False
                        all_on = True
                        for group_state in bridge_config["groups"].keys():
                            if bridge_config["groups"][group_state]["state"]["any_on"] == True:
                                any_on = True
                            else:
                                all_on = False
                        self._set_end_headers(bytes(json.dumps({"name":"Group 0","lights": [l for l in bridge_config["lights"]],"sensors": [s for s in bridge_config["sensors"]],"type":"LightGroup","state":{"all_on":all_on,"any_on":any_on},"recycle":False,"action":{"on":False,"alert":"none"}},separators=(',', ':')), "utf8"))
                    elif url_pices[3] == "info" and url_pices[4] == "timezones":
                        self._set_end_headers(bytes(json.dumps(bridge_config["capabilities"][url_pices[4]]["values"],separators=(',', ':')), "utf8"))
                    else:
                        self._set_end_headers(bytes(json.dumps(bridge_config[url_pices[3]][url_pices[4]],separators=(',', ':')), "utf8"))
            elif (url_pices[2] == "nouser" or url_pices[2] == "none" or url_pices[2] == "config"): #used by applications to discover the bridge
                self._set_end_headers(bytes(json.dumps({"name": bridge_config["config"]["name"],"datastoreversion": 70, "swversion": bridge_config["config"]["swversion"], "apiversion": bridge_config["config"]["apiversion"], "mac": bridge_config["config"]["mac"], "bridgeid": bridge_config["config"]["bridgeid"], "factorynew": False, "replacesbridgeid": None, "modelid": bridge_config["config"]["modelid"],"starterkitid":""},separators=(',', ':')), "utf8"))
            else: #user is not in whitelist
                self._set_end_headers(bytes(json.dumps([{"error": {"type": 1, "address": self.path, "description": "unauthorized user" }}],separators=(',', ':')), "utf8"))

    def read_http_request_body(self):
        return b"{}" if self.headers['Content-Length'] is None or self.headers[
            'Content-Length'] == '0' else self.rfile.read(int(self.headers['Content-Length']))

    def do_POST(self):
        self._set_headers()
        logging.info("in post method")
        logging.info(self.path)
        self.data_string = self.read_http_request_body()
        if self.path == "/updater":
            logging.info("check for updates")
            update_data = json.loads(sendRequest("https://raw.githubusercontent.com/diyhue/diyHue/master/BridgeEmulator/updater", "GET", "{}"))
            for category in update_data.keys():
                for key in update_data[category].keys():
                    logging.info("patch " + category + " -> " + key )
                    bridge_config[category][key] = update_data[category][key]
            self._set_end_headers(bytes(json.dumps([{"success": {"/config/swupdate/checkforupdate": True}}],separators=(',', ':')), "utf8"))
        else:
            raw_json = self.data_string.decode('utf8')
            raw_json = raw_json.replace("\t","")
            raw_json = raw_json.replace("\n","")
            post_dictionary = json.loads(raw_json)
            logging.info(self.data_string)
        url_pices = self.path.rstrip('/').split('/')
        if len(url_pices) == 4: #data was posted to a location
            if url_pices[2] in bridge_config["config"]["whitelist"]:
                if ((url_pices[3] == "lights" or url_pices[3] == "sensors") and not bool(post_dictionary)):
                    #if was a request to scan for lights of sensors
                    Thread(target=scanForLights).start()
                    sleep(7) #give no more than 5 seconds for light scanning (otherwise will face app disconnection timeout)
                    self._set_end_headers(bytes(json.dumps([{"success": {"/" + url_pices[3]: "Searching for new devices"}}],separators=(',', ':')), "utf8"))
                elif url_pices[3] == "":
                    self._set_end_headers(bytes(json.dumps([{"success": {"clientkey": "321c0c2ebfa7361e55491095b2f5f9db"}}],separators=(',', ':')), "utf8"))
                else: #create object
                    # find the first unused id for new object
                    new_object_id = nextFreeId(bridge_config, url_pices[3])
                    if url_pices[3] == "scenes":
                        post_dictionary.update({"lightstates": {}, "version": 2, "picture": "", "lastupdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), "owner" :url_pices[2]})
                        if "locked" not in post_dictionary:
                            post_dictionary["locked"] = False
                    elif url_pices[3] == "groups":
                        post_dictionary.update({"action": {"on": False}, "state": {"any_on": False, "all_on": False}})
                    elif url_pices[3] == "schedules":
                        try:
                            post_dictionary.update({"created": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), "time": post_dictionary["localtime"]})
                        except KeyError:
                            post_dictionary.update({"created": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), "localtime": post_dictionary["time"]})
                        if post_dictionary["localtime"].startswith("PT"):
                            post_dictionary.update({"starttime": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")})
                        if not "status" in post_dictionary:
                            post_dictionary.update({"status": "enabled"})
                    elif url_pices[3] == "rules":
                        post_dictionary.update({"owner": url_pices[2], "lasttriggered" : "none", "created": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"), "timestriggered": 0})
                        if not "status" in post_dictionary:
                            post_dictionary.update({"status": "enabled"})
                    elif url_pices[3] == "sensors":
                        if "state" not in post_dictionary:
                            post_dictionary["state"] = {}
                        if post_dictionary["modelid"] == "PHWA01":
                            post_dictionary.update({"state": {"status": 0}})
                        elif post_dictionary["modelid"] == "PHA_CTRL_START":
                            post_dictionary.update({"state": {"flag": False, "lastupdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")}, "config": {"on": True,"reachable": True}})
                    elif url_pices[3] == "resourcelinks":
                        post_dictionary.update({"owner" :url_pices[2]})
                    generateSensorsState()
                    bridge_config[url_pices[3]][new_object_id] = post_dictionary
                    logging.info(json.dumps([{"success": {"id": new_object_id}}], sort_keys=True, indent=4, separators=(',', ': ')))
                    self._set_end_headers(bytes(json.dumps([{"success": {"id": new_object_id}}], separators=(',', ':')), "utf8"))
            else:
                self._set_end_headers(bytes(json.dumps([{"error": {"type": 1, "address": self.path, "description": "unauthorized user" }}], separators=(',', ':')), "utf8"))
                logging.info(json.dumps([{"error": {"type": 1, "address": self.path, "description": "unauthorized user" }}],sort_keys=True, indent=4, separators=(',', ': ')))
        elif self.path.startswith("/api") and "devicetype" in post_dictionary: #new registration by linkbutton
            if int(bridge_config["linkbutton"]["lastlinkbuttonpushed"])+30 >= int(datetime.now().strftime("%s")) or bridge_config["config"]["linkbutton"]:
                username = hashlib.new('ripemd160', post_dictionary["devicetype"][0].encode('utf-8')).hexdigest()[:32]
                bridge_config["config"]["whitelist"][username] = {"last use date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),"create date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),"name": post_dictionary["devicetype"]}
                response = [{"success": {"username": username}}]
                if "generateclientkey" in post_dictionary and post_dictionary["generateclientkey"]:
                    response[0]["success"]["clientkey"] = "321c0c2ebfa7361e55491095b2f5f9db"
                self._set_end_headers(bytes(json.dumps(response,separators=(',', ':')), "utf8"))
                logging.info(json.dumps(response, sort_keys=True, indent=4, separators=(',', ': ')))
            else:
                self._set_end_headers(bytes(json.dumps([{"error": {"type": 101, "address": self.path, "description": "link button not pressed" }}], separators=(',', ':')), "utf8"))
        saveConfig()

    def do_PUT(self):
        self._set_headers()
        logging.info("in PUT method")
        self.data_string = self.rfile.read(int(self.headers['Content-Length']))
        put_dictionary = json.loads(self.data_string.decode('utf8'))
        url_pices = self.path.rstrip('/').split('/')
        logging.info(self.path)
        logging.info(self.data_string)
        if url_pices[2] in bridge_config["config"]["whitelist"]:
            if len(url_pices) == 4:
                bridge_config[url_pices[3]].update(put_dictionary)
                response_location = "/" + url_pices[3] + "/"
            if len(url_pices) == 5:
                if url_pices[3] == "schedules":
                    if "status" in put_dictionary and put_dictionary["status"] == "enabled" and bridge_config["schedules"][url_pices[4]]["localtime"].startswith("PT"):
                        put_dictionary.update({"starttime": (datetime.utcnow()).strftime("%Y-%m-%dT%H:%M:%S")})
                elif url_pices[3] == "scenes":
                    if "storelightstate" in put_dictionary:
                        for light in bridge_config["scenes"][url_pices[4]]["lightstates"]:
                            bridge_config["scenes"][url_pices[4]]["lightstates"][light] = {}
                            bridge_config["scenes"][url_pices[4]]["lightstates"][light]["on"] = bridge_config["lights"][light]["state"]["on"]
                            bridge_config["scenes"][url_pices[4]]["lightstates"][light]["bri"] = bridge_config["lights"][light]["state"]["bri"]
                            if "colormode" in bridge_config["lights"][light]["state"]:
                                if bridge_config["lights"][light]["state"]["colormode"] in ["ct", "xy"]:
                                    bridge_config["scenes"][url_pices[4]]["lightstates"][light][bridge_config["lights"][light]["state"]["colormode"]] = bridge_config["lights"][light]["state"][bridge_config["lights"][light]["state"]["colormode"]]
                                elif bridge_config["lights"][light]["state"]["colormode"] == "hs" and "hue" in bridge_config["scenes"][url_pices[4]]["lightstates"][light]:
                                    bridge_config["scenes"][url_pices[4]]["lightstates"][light]["hue"] = bridge_config["lights"][light]["state"]["hue"]
                                    bridge_config["scenes"][url_pices[4]]["lightstates"][light]["sat"] = bridge_config["lights"][light]["state"]["sat"]
                if url_pices[3] == "sensors":
                    current_time = datetime.now()
                    for key, value in put_dictionary.items():
                        if key not in sensors_state[url_pices[4]]:
                            sensors_state[url_pices[4]][key] = {}
                        if type(value) is dict:
                            bridge_config["sensors"][url_pices[4]][key].update(value)
                            for element in value.keys():
                                sensors_state[url_pices[4]][key][element] = current_time
                        else:
                            bridge_config["sensors"][url_pices[4]][key] = value
                            sensors_state[url_pices[3]][url_pices[4]][key] = current_time
                    rulesProcessor(url_pices[4], current_time)
                    if url_pices[4] == "1" and bridge_config[url_pices[3]][url_pices[4]]["modelid"] == "PHDL00":
                        bridge_config["sensors"]["1"]["config"]["configured"] = True ##mark daylight sensor as configured
                elif url_pices[3] == "groups" and "stream" in put_dictionary:
                    if "active" in put_dictionary["stream"]:
                        if put_dictionary["stream"]["active"]:
                            logging.info("start hue entertainment")
                            Popen(["/opt/hue-emulator/entertainment-srv", "server_port=2100", "dtls=1", "psk_list=" + url_pices[2] + ",321c0c2ebfa7361e55491095b2f5f9db"])
                            sleep(0.2)
                            bridge_config["groups"][url_pices[4]]["stream"].update({"active": True, "owner": url_pices[2], "proxymode": "auto", "proxynode": "/bridge"})
                        else:
                            logging.info("stop hue entertainent")
                            Popen(["killall", "entertainment-srv"])
                            bridge_config["groups"][url_pices[4]]["stream"].update({"active": False, "owner": None})
                    else:
                        bridge_config[url_pices[3]][url_pices[4]].update(put_dictionary)
                elif url_pices[3] == "lights" and "config" in put_dictionary:
                    bridge_config["lights"][url_pices[4]]["config"].update(put_dictionary["config"])
                    if "startup" in put_dictionary["config"] and bridge_config["lights_address"][url_pices[4]]["protocol"] == "native":
                        if put_dictionary["config"]["startup"]["mode"] == "safety":
                            sendRequest("http://" + bridge_config["lights_address"][url_pices[4]]["ip"] + "/", "POST", {"startup": 1})
                        elif put_dictionary["config"]["startup"]["mode"] == "powerfail":
                            sendRequest("http://" + bridge_config["lights_address"][url_pices[4]]["ip"] + "/", "POST", {"startup": 0})

                        #add exception on json output as this dictionary has tree levels
                        response_dictionary = {"success":{"/lights/" + url_pices[4] + "/config/startup": {"mode": put_dictionary["config"]["startup"]["mode"]}}}
                        self._set_end_headers(bytes(json.dumps(response_dictionary,separators=(',', ':')), "utf8"))
                        logging.info(json.dumps(response_dictionary, sort_keys=True, indent=4, separators=(',', ': ')))
                        return
                else:
                    bridge_config[url_pices[3]][url_pices[4]].update(put_dictionary)

                response_location = "/" + url_pices[3] + "/" + url_pices[4] + "/"
            if len(url_pices) == 6:
                if url_pices[3] == "groups": #state is applied to a group
                    if url_pices[5] == "stream":
                        if "active" in put_dictionary:
                            if put_dictionary["active"]:
                                logging.info("start hue entertainment")
                                Popen(["/opt/hue-emulator/entertainment-srv", "server_port=2100", "dtls=1", "psk_list=" + url_pices[2] + ",321c0c2ebfa7361e55491095b2f5f9db"])
                                sleep(0.2)
                                bridge_config["groups"][url_pices[4]]["stream"].update({"active": True, "owner": url_pices[2], "proxymode": "auto", "proxynode": "/bridge"})
                            else:
                                Popen(["killall", "entertainment-srv"])
                                bridge_config["groups"][url_pices[4]]["stream"].update({"active": False, "owner": None})
                    elif "scene" in put_dictionary: #scene applied to group
                        splitLightsToDevices(url_pices[4], {}, bridge_config["scenes"][put_dictionary["scene"]]["lightstates"])

                    elif "bri_inc" in put_dictionary or "ct_inc" in put_dictionary:
                        splitLightsToDevices(url_pices[4], put_dictionary)
                    elif "scene_inc" in put_dictionary:
                        switchScene(url_pices[4], put_dictionary["scene_inc"])
                    elif url_pices[4] == "0": #if group is 0 the scene applied to all lights
                        groupZero(put_dictionary)
                    else: # the state is applied to particular group (url_pices[4])
                        if "on" in put_dictionary:
                            bridge_config["groups"][url_pices[4]]["state"]["any_on"] = put_dictionary["on"]
                            bridge_config["groups"][url_pices[4]]["state"]["all_on"] = put_dictionary["on"]
                        bridge_config["groups"][url_pices[4]][url_pices[5]].update(put_dictionary)
                        splitLightsToDevices(url_pices[4], put_dictionary)
                elif url_pices[3] == "lights": #state is applied to a light
                    for key in put_dictionary.keys():
                        if key in ["ct", "xy"]: #colormode must be set by bridge
                            bridge_config["lights"][url_pices[4]]["state"]["colormode"] = key
                        elif key in ["hue", "sat"]:
                            bridge_config["lights"][url_pices[4]]["state"]["colormode"] = "hs"

                    updateGroupStats(url_pices[4])
                    sendLightRequest(url_pices[4], put_dictionary)
                if not url_pices[4] == "0": #group 0 is virtual, must not be saved in bridge configuration
                    try:
                        bridge_config[url_pices[3]][url_pices[4]][url_pices[5]].update(put_dictionary)
                    except KeyError:
                        bridge_config[url_pices[3]][url_pices[4]][url_pices[5]] = put_dictionary
                if url_pices[3] == "sensors" and url_pices[5] == "state":
                    current_time = datetime.now()
                    for key in put_dictionary.keys():
                        sensors_state[url_pices[4]]["state"].update({key: current_time})
                    rulesProcessor(url_pices[4], current_time)
                response_location = "/" + url_pices[3] + "/" + url_pices[4] + "/" + url_pices[5] + "/"
            if len(url_pices) == 7:
                try:
                    bridge_config[url_pices[3]][url_pices[4]][url_pices[5]][url_pices[6]].update(put_dictionary)
                except KeyError:
                    bridge_config[url_pices[3]][url_pices[4]][url_pices[5]][url_pices[6]] = put_dictionary
                bridge_config[url_pices[3]][url_pices[4]][url_pices[5]][url_pices[6]] = put_dictionary
                response_location = "/" + url_pices[3] + "/" + url_pices[4] + "/" + url_pices[5] + "/" + url_pices[6] + "/"
            response_dictionary = []
            for key, value in put_dictionary.items():
                response_dictionary.append({"success":{response_location + key: value}})
            self._set_end_headers(bytes(json.dumps(response_dictionary,separators=(',', ':')), "utf8"))
            logging.info(json.dumps(response_dictionary, sort_keys=True, indent=4, separators=(',', ': ')))
        else:
            self._set_end_headers(bytes(json.dumps([{"error": {"type": 1, "address": self.path, "description": "unauthorized user" }}],separators=(',', ':')), "utf8"))

    def do_DELETE(self):
        self._set_headers()
        url_pices = self.path.rstrip('/').split('/')
        if url_pices[2] in bridge_config["config"]["whitelist"]:
            if len(url_pices) == 6:
                del bridge_config[url_pices[3]][url_pices[4]][url_pices[5]]
            else:
                if url_pices[3] == "resourcelinks":
                    Thread(target=resourceRecycle).start()
                elif url_pices[3] == "sensors":
                    ## delete also related sensors
                    for sensor in list(bridge_config["sensors"]):
                        if sensor != url_pices[4] and "uniqueid" in bridge_config["sensors"][sensor] and bridge_config["sensors"][sensor]["uniqueid"].startswith(bridge_config["sensors"][url_pices[4]]["uniqueid"][:26]):
                            del bridge_config["sensors"][sensor]
                            logging.info('Delete related sensor ' + sensor)
                del bridge_config[url_pices[3]][url_pices[4]]
            if url_pices[3] == "lights":
                del bridge_config["lights_address"][url_pices[4]]
                for light in list(bridge_config["deconz"]["lights"]):
                    if bridge_config["deconz"]["lights"][light]["bridgeid"] == url_pices[4]:
                        del bridge_config["deconz"]["lights"][light]
                for scene in list(bridge_config["scenes"]):
                    if "lights" in bridge_config["scenes"][scene] and url_pices[4] in bridge_config["scenes"][scene]["lights"]:
                        bridge_config["scenes"][scene]["lights"].remove(url_pices[4])
                        del bridge_config["scenes"][scene]["lightstates"][url_pices[4]]
                        if len(bridge_config["scenes"][scene]["lights"]) == 0:
                            del bridge_config["scenes"][scene]
            elif url_pices[3] == "sensors":
                for sensor in list(bridge_config["deconz"]["sensors"]):
                    if bridge_config["deconz"]["sensors"][sensor]["bridgeid"] == url_pices[4]:
                        del bridge_config["deconz"]["sensors"][sensor]
            self._set_end_headers(bytes(json.dumps([{"success": "/" + url_pices[3] + "/" + url_pices[4] + " deleted."}],separators=(',', ':')), "utf8"))


class ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
    pass

def get_ip_address(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x8915,  # SIOCGIFADDR
        struct.pack('256s', bytes(ifname[:15], 'utf-8')))[20:24])

def run(iface, https, server_class=ThreadingSimpleServer, handler_class=HueHandler):
    ip = get_ip_address(iface)
    print ('ip address: ', ip)
    if https:
        server_address = (ip, 443)
        httpd = server_class(server_address, handler_class)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(certfile="./cert.pem")
        ctx.options |= ssl.OP_NO_TLSv1
        ctx.options |= ssl.OP_NO_TLSv1_1
        ctx.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
        ctx.set_ciphers('ECDHE-ECDSA-AES128-GCM-SHA256')
        ctx.set_ecdh_curve('prime256v1')
        #ctx.set_alpn_protocols(['h2', 'http/1.1'])
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        logging.info('Starting ssl httpd...')
    else:
        server_address = (ip, 80)
        httpd = server_class(server_address, handler_class)
        logging.info('Starting httpd...')
    httpd.serve_forever()
    httpd.server_close()

if __name__ == '__main__':
    # Process arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--clear', action='store_true', help='clear the display on exit')
    parser.add_argument('-i', '--interface', default='wlan0.1', help='free network interface to use')
    args = parser.parse_args()

    # Create NeoPixel object with appropriate configuration.
    strip = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL)
    # Intialize the library (must be called once before other functions).
    strip.begin()

    print ('Press Ctrl-C to quit.')
    if not args.clear:
        print('Use "-c" argument to clear LEDs on exit')

    try:

        Thread(target=run, args=[args.interface, False]).start()
        # Thread(target=run, args=[args.interface, True]).start()

        while True:
            sleep(10)
            # print ('Color wipe animations.')
            # colorWipe(strip, Color(255, 0, 0))  # Red wipe
            # colorWipe(strip, Color(0, 255, 0))  # Blue wipe
            # colorWipe(strip, Color(0, 0, 255))  # Green wipe
            # print ('Theater chase animations.')
            # theaterChase(strip, Color(127, 127, 127))  # White theater chase
            # theaterChase(strip, Color(127,   0,   0))  # Red theater chase
            # theaterChase(strip, Color(  0,   0, 127))  # Blue theater chase
            # print ('Rainbow animations.')
            # rainbow(strip)
            # rainbowCycle(strip)
            # theaterChaseRainbow(strip)

    except KeyboardInterrupt:
        if args.clear:
            colorWipe(strip, Color(0,0,0), 10)
        logging.exception('Server Stopped')
    finally:
        run_service = False

