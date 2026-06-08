import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tello import Tello

import cv2
import pygame
import numpy as np
import time
import threading
import subprocess
from threading import Thread

S = 60
FPS = 30

# ----------------------------------------------------------------------
# BackgroundFrameRead embarqué — ffmpeg subprocess, zéro cv2.VideoCapture.
# On l'embarque ici pour ne pas dépendre de la version installée de
# djitellopy qui utilise cv2.VideoCapture avec un timeout OpenCV de 30s
# qui coupait le programme (et donc le drone) en plein vol.
# ----------------------------------------------------------------------
class FFmpegFrameRead:
    W, H = 960, 720

    def __init__(self, address):
        self.address = address
        self._lock  = threading.Lock()
        self._frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        self.grabbed = False
        self.stopped = False
        self._proc  = None

    def _start_ffmpeg(self):
        subprocess.run(['pkill', '-f', 'ffmpeg.*11111'], capture_output=True)
        time.sleep(0.2)
        cmd = [
            'ffmpeg',
            '-loglevel', 'quiet',          # silence les warnings h264
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-analyzeduration', '0',
            '-probesize', '32',
            '-i', self.address,
            '-f', 'rawvideo',
            '-pix_fmt', 'bgr24',
            '-vf', f'scale={self.W}:{self.H}',
            '-vsync', '0',
            '-'
        ]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, bufsize=0)

    def start(self):
        self._start_ffmpeg()
        Thread(target=self._update, daemon=True).start()
        return self

    def _update(self):
        size = self.W * self.H * 3
        while not self.stopped:
            if self._proc is None or self._proc.poll() is not None:
                time.sleep(1)
                self._start_ffmpeg()
                continue
            try:
                raw = self._proc.stdout.read(size)
            except Exception:
                continue
            if len(raw) == size:
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.H, self.W, 3))
                with self._lock:
                    self._frame = frame
                    self.grabbed = True

    @property
    def frame(self):
        with self._lock:
            return self._frame.copy()

    def stop(self):
        self.stopped = True
        if self._proc:
            self._proc.terminate()


class FrontEnd:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Tello — Contrôle manuel")
        self.screen = pygame.display.set_mode([960, 720])
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Arial", 20)

        self.tello = Tello()

        self.for_back_velocity = 0
        self.left_right_velocity = 0
        self.up_down_velocity = 0
        self.yaw_velocity = 0
        self.speed = 10
        self.send_rc_control = False

        self._battery = "?"
        self._battery_lock = threading.Lock()
        self._stop_battery = threading.Event()
        self._stop_rc = threading.Event()

    # ------------------------------------------------------------------
    # Heartbeat RC dédié — 20Hz, indépendant de pygame
    # ------------------------------------------------------------------
    def _rc_worker(self):
        while not self._stop_rc.is_set():
            if self.send_rc_control:
                try:
                    self.tello.send_rc_control(
                        self.left_right_velocity,
                        self.for_back_velocity,
                        self.up_down_velocity,
                        self.yaw_velocity,
                    )
                except Exception:
                    pass
            self._stop_rc.wait(0.05)

    def _battery_worker(self):
        while not self._stop_battery.is_set():
            if not self.send_rc_control:
                try:
                    val = self.tello.get_battery()
                    with self._battery_lock:
                        self._battery = str(val).replace("\r\n", "")
                except Exception:
                    pass
            self._stop_battery.wait(30)

    def _get_battery(self) -> str:
        with self._battery_lock:
            return self._battery

    # ------------------------------------------------------------------
    def run(self):
        self.tello.connect()
        self.tello.set_speed(self.speed)
        self.tello.streamoff()
        self.tello.streamon()

        udp = ('udp://@0.0.0.0:11111'
               '?overrun_nonfatal=1&fifo_size=500000&reuse=1')
        frame_read = FFmpegFrameRead(udp).start()

        threading.Thread(target=self._battery_worker, daemon=True).start()
        threading.Thread(target=self._rc_worker, daemon=True).start()

        should_stop = False
        while not should_stop:

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    should_stop = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        should_stop = True
                    else:
                        self.keydown(event.key)
                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)

            self.screen.fill((0, 0, 0))

            try:
                raw = frame_read.frame
                frame = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
                frame = np.rot90(frame)
                frame = np.flipud(frame)
                surf = pygame.surfarray.make_surface(frame)
                self.screen.blit(surf, (0, 0))
            except Exception:
                pass  # flux vidéo temporairement indisponible — on continue

            bat_text = f"Batterie : {self._get_battery()}%"
            bat_surf = self.font.render(bat_text, True, (0, 255, 80))
            self.screen.blit(bat_surf, (10, 10))

            controls = [
                "T : Décollage   L : Atterrissage",
                "↑↓←→ : Avant/Arrière/Gauche/Droite",
                "W/S : Monter/Descendre   A/D : Rotation",
                "ESCAPE : Quitter",
            ]
            for i, line in enumerate(controls):
                surf_c = self.font.render(line, True, (200, 200, 200))
                self.screen.blit(surf_c, (10, 680 - (len(controls) - i) * 22))

            pygame.display.update()
            self.clock.tick(FPS)

        self._stop_rc.set()
        self._stop_battery.set()
        frame_read.stop()
        self.tello.end()
        pygame.quit()

    # ------------------------------------------------------------------
    def keydown(self, key):
        if key == pygame.K_UP:
            self.for_back_velocity = S
        elif key == pygame.K_DOWN:
            self.for_back_velocity = -S
        elif key == pygame.K_LEFT:
            self.left_right_velocity = -S
        elif key == pygame.K_RIGHT:
            self.left_right_velocity = S
        elif key == pygame.K_w:
            self.up_down_velocity = S
        elif key == pygame.K_s:
            self.up_down_velocity = -S
        elif key == pygame.K_a:
            self.yaw_velocity = -S
        elif key == pygame.K_d:
            self.yaw_velocity = S

    def keyup(self, key):
        if key in (pygame.K_UP, pygame.K_DOWN):
            self.for_back_velocity = 0
        elif key in (pygame.K_LEFT, pygame.K_RIGHT):
            self.left_right_velocity = 0
        elif key in (pygame.K_w, pygame.K_s):
            self.up_down_velocity = 0
        elif key in (pygame.K_a, pygame.K_d):
            self.yaw_velocity = 0
        elif key == pygame.K_t:
            threading.Thread(target=self._do_takeoff, daemon=True).start()
        elif key == pygame.K_l:
            threading.Thread(target=self._do_land, daemon=True).start()

    def _do_takeoff(self):
        self.send_rc_control = True
        self.tello.takeoff()

    def _do_land(self):
        self.send_rc_control = False
        self.tello.land()


def main():
    FrontEnd().run()


if __name__ == "__main__":
    main()
