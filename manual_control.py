import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tello import Tello

import cv2
import pygame
import numpy as np
import time
import threading

S = 60
FPS = 30


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

        pygame.time.set_timer(pygame.USEREVENT + 1, 1000 // FPS)

    def _battery_worker(self):
        while not self._stop_battery.is_set():
            # Ne pas interroger la batterie pendant le vol — perturbe le firmware
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

    def run(self):
        self.tello.connect()
        self.tello.set_speed(self.speed)
        self.tello.streamoff()
        self.tello.streamon()

        frame_read = self.tello.get_frame_read()

        threading.Thread(target=self._battery_worker, daemon=True).start()

        should_stop = False
        while not should_stop:

            for event in pygame.event.get():
                if event.type == pygame.USEREVENT + 1:
                    self.update()
                elif event.type == pygame.QUIT:
                    should_stop = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        should_stop = True
                    else:
                        self.keydown(event.key)
                elif event.type == pygame.KEYUP:
                    self.keyup(event.key)

            if frame_read.stopped:
                break

            raw = frame_read.frame
            if raw is None:
                self.clock.tick(FPS)
                continue

            self.screen.fill((0, 0, 0))

            frame = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
            frame = np.rot90(frame)
            frame = np.flipud(frame)
            surf = pygame.surfarray.make_surface(frame)
            self.screen.blit(surf, (0, 0))

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

        self._stop_battery.set()
        self.tello.end()
        pygame.quit()

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
            # Takeoff dans un thread — ne bloque pas la boucle pygame/vidéo
            threading.Thread(target=self._do_takeoff, daemon=True).start()
        elif key == pygame.K_l:
            threading.Thread(target=self._do_land, daemon=True).start()

    def _do_takeoff(self):
        self.tello.takeoff()
        self.send_rc_control = True

    def _do_land(self):
        self.send_rc_control = False
        self.tello.land()

    def update(self):
        if self.send_rc_control:
            self.tello.send_rc_control(
                self.left_right_velocity,
                self.for_back_velocity,
                self.up_down_velocity,
                self.yaw_velocity,
            )


def main():
    FrontEnd().run()


if __name__ == "__main__":
    main()
