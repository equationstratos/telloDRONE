# coding=utf-8
import socket
import time
import threading
import cv2
from threading import Thread
from djitellopy.decorators import accepts


class Tello:
    """Python wrapper to interact with the Ryze Tello drone using the official Tello api."""

    UDP_IP = '192.168.10.1'
    UDP_PORT = 8889
    RESPONSE_TIMEOUT = 7       # secondes — était 0.5 (trop court pour Tello EDU)
    TIME_BTW_COMMANDS = 0.1    # secondes entre commandes
    TIME_BTW_RC_CONTROL_COMMANDS = 50  # millisecondes — était 0.5s mal comparé en ms

    VS_UDP_IP = '0.0.0.0'
    VS_UDP_PORT = 11111

    cap = None
    background_frame_read = None
    stream_on = False

    def __init__(self):
        self.address = (self.UDP_IP, self.UDP_PORT)
        self.clientSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.clientSocket.bind(('', self.UDP_PORT))
        self.response = None
        self.stream_on = False
        self._last_command_time = 0       # en secondes
        self._last_rc_sent_ms = 0         # en millisecondes

        thread = threading.Thread(target=self.run_udp_receiver, args=())
        thread.daemon = True
        thread.start()

    def run_udp_receiver(self):
        while True:
            try:
                self.response, _ = self.clientSocket.recvfrom(2048)
            except Exception as e:
                print(e)
                break

    def get_udp_video_address(self):
        # fifo_size réduit à 500000 (était 5000000) — gros buffer = latence accumulée
        # overrun_nonfatal=1 évite les crashes sur perte de paquets WiFi
        return ('udp://@' + self.VS_UDP_IP + ':' + str(self.VS_UDP_PORT)
                + '?overrun_nonfatal=1&fifo_size=500000')

    def get_video_capture(self):
        if self.cap is None:
            self.cap = cv2.VideoCapture(self.get_udp_video_address())
        if not self.cap.isOpened():
            self.cap.open(self.get_udp_video_address())
        return self.cap

    def get_frame_read(self):
        if self.background_frame_read is None:
            self.background_frame_read = BackgroundFrameRead(
                self, self.get_udp_video_address()
            ).start()
        return self.background_frame_read

    def stop_video_capture(self):
        return self.streamoff()

    @accepts(command=str)
    def send_command_with_return(self, command):
        """Envoie une commande et attend la réponse."""
        # Respect du délai minimum entre commandes
        elapsed = time.time() - self._last_command_time
        if elapsed < self.TIME_BTW_COMMANDS:
            time.sleep(self.TIME_BTW_COMMANDS - elapsed)

        print('Send command: ' + command)
        self.response = None
        self.clientSocket.sendto(command.encode('utf-8'), self.address)
        deadline = time.time() + self.RESPONSE_TIMEOUT

        # Attente non-bloquante avec micro-sleep (évite 100% CPU)
        while self.response is None:
            if time.time() > deadline:
                print('Timeout exceeded on command: ' + command)
                return False
            time.sleep(0.001)  # libère le CPU — était une busy loop pure

        response = self.response.decode('utf-8')
        self.response = None
        self._last_command_time = time.time()

        print('Response: ' + response)
        return response

    @accepts(command=str)
    def send_command_without_return(self, command):
        """Envoie une commande sans attendre de réponse (rc, go, curve...)."""
        self.clientSocket.sendto(command.encode('utf-8'), self.address)

    @accepts(command=str)
    def send_control_command(self, command):
        response = self.send_command_with_return(command)
        if response in ('OK', 'ok'):
            return True
        return self.return_error_on_send_command(command, response)

    @accepts(command=str)
    def send_read_command(self, command):
        response = self.send_command_with_return(command)
        try:
            response = str(response)
        except TypeError as e:
            print(e)
            return False

        if response and ('error' not in response.lower()) and ('false' not in response.lower()):
            return int(response) if response.strip().isdigit() else response
        return self.return_error_on_send_command(command, response)

    @staticmethod
    def return_error_on_send_command(command, response):
        print('Command ' + command + ' was unsuccessful. Message: ' + str(response))
        return False

    # ------------------------------------------------------------------
    # Commandes de vol
    # ------------------------------------------------------------------
    def connect(self):
        return self.send_control_command("command")

    def takeoff(self):
        return self.send_control_command("takeoff")

    def land(self):
        return self.send_control_command("land")

    def streamon(self):
        result = self.send_control_command("streamon")
        if result is True:
            self.stream_on = True
        return result

    def streamoff(self):
        result = self.send_control_command("streamoff")
        if result is True:
            self.stream_on = False
        return result

    def emergency(self):
        return self.send_control_command("emergency")

    @accepts(direction=str, x=int)
    def move(self, direction, x):
        return self.send_control_command(direction + ' ' + str(x))

    def move_up(self, x):      return self.move("up", x)
    def move_down(self, x):    return self.move("down", x)
    def move_left(self, x):    return self.move("left", x)
    def move_right(self, x):   return self.move("right", x)
    def move_forward(self, x): return self.move("forward", x)
    def move_back(self, x):    return self.move("back", x)

    @accepts(x=int)
    def rotate_clockwise(self, x):
        return self.send_control_command("cw " + str(x))

    @accepts(x=int)
    def rotate_counter_clockwise(self, x):
        return self.send_control_command("ccw " + str(x))

    @accepts(x=str)
    def flip(self, direction):
        return self.send_control_command("flip " + direction)

    def flip_left(self):    return self.flip("l")
    def flip_right(self):   return self.flip("r")
    def flip_forward(self): return self.flip("f")
    def flip_back(self):    return self.flip("b")

    def go_xyz_speed(self, x, y, z, speed):
        return self.send_command_without_return('go %s %s %s %s' % (x, y, z, speed))

    def curve_xyz_speed(self, x1, y1, z1, x2, y2, z2, speed):
        return self.send_command_without_return(
            'curve %s %s %s %s %s %s %s' % (x1, y1, z1, x2, y2, z2, speed))

    @accepts(x=int)
    def set_speed(self, x):
        return self.send_control_command("speed " + str(x))

    @accepts(left_right_velocity=int, forward_backward_velocity=int,
             up_down_velocity=int, yaw_velocity=int)
    def send_rc_control(self, left_right_velocity, forward_backward_velocity,
                        up_down_velocity, yaw_velocity):
        """Envoie commande RC — throttle correct en millisecondes."""
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_rc_sent_ms < self.TIME_BTW_RC_CONTROL_COMMANDS:
            return
        self._last_rc_sent_ms = now_ms
        self.send_command_without_return(
            'rc %s %s %s %s' % (left_right_velocity, forward_backward_velocity,
                                up_down_velocity, yaw_velocity))

    def set_wifi_credentials(self, ssid, password):
        return self.send_control_command('wifi %s %s' % (ssid, password))

    def connect_to_wifi(self, ssid, password):
        return self.send_control_command('ap %s %s' % (ssid, password))

    # ------------------------------------------------------------------
    # Lecture de capteurs
    # ------------------------------------------------------------------
    def get_battery(self):
        return self.send_read_command('battery?')

    def get_speed(self):       return self.send_read_command('speed?')
    def get_flight_time(self): return self.send_read_command('time?')
    def get_height(self):      return self.send_read_command('height?')
    def get_temperature(self): return self.send_read_command('temp?')
    def get_attitude(self):    return self.send_read_command('attitude?')
    def get_barometer(self):   return self.send_read_command('baro?')
    def get_distance_tof(self):return self.send_read_command('tof?')
    def get_wifi(self):        return self.send_read_command('wifi?')

    def end(self):
        if self.stream_on:
            self.streamoff()
        if self.background_frame_read is not None:
            self.background_frame_read.stop()
        if self.cap is not None:
            self.cap.release()


class BackgroundFrameRead:
    """Lit les frames vidéo en arrière-plan. Accède à .frame pour le dernier frame."""

    def __init__(self, tello, address):
        # Forcer le backend FFmpeg — évite que Ubuntu 26 choisisse GStreamer
        tello.cap = cv2.VideoCapture(address, cv2.CAP_FFMPEG)
        self.cap = tello.cap

        # Réduire le buffer interne OpenCV à 1 frame → toujours le frame le plus récent
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            self.cap.open(address)

        self.grabbed, self.frame = self.cap.read()
        self.stopped = False

    def start(self):
        Thread(target=self.update_frame, daemon=True).start()
        return self

    def update_frame(self):
        while not self.stopped:
            if not self.grabbed or not self.cap.isOpened():
                self.stop()
            else:
                self.grabbed, self.frame = self.cap.read()
            # Pas de sleep ici — on lit aussi vite que possible pour vider le buffer

    def stop(self):
        self.stopped = True
