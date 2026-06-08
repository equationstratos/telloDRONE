import sys
import os
import glob
import subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tello import Tello

import cv2
import pygame
import numpy as np
import time
import threading

S = 60
FPS = 30

BANNER = "=== manual_control v4 — heartbeat thread + diagnostic ==="


# ----------------------------------------------------------------------
# Diagnostic WiFi : le power-save d'Ubuntu 26 retarde/jette les petits
# paquets de commande (rc) tout en laissant passer la vidéo bufferisée.
# C'est le suspect nº1 de l'atterrissage automatique.
# ----------------------------------------------------------------------
def find_wifi_iface():
    for path in glob.glob('/sys/class/net/*/wireless'):
        return path.split('/')[-2]
    return None


def check_power_save(iface):
    try:
        out = subprocess.run(['iw', 'dev', iface, 'get', 'power_save'],
                             capture_output=True, text=True, timeout=3)
        return 'on' in out.stdout.lower()
    except Exception:
        return None


def warn_power_save():
    iface = find_wifi_iface()
    if not iface:
        return
    state = check_power_save(iface)
    if state is True:
        print("\n" + "!" * 60)
        print(f"  POWER-SAVE WIFI ACTIF sur {iface} — cause probable de")
        print("  l'atterrissage automatique. Désactive-le AVANT de voler :")
        print(f"     sudo iw dev {iface} set power_save off")
        print("!" * 60 + "\n")
    elif state is False:
        print(f"[WiFi] power-save déjà désactivé sur {iface} — OK")


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

        self._rc_count = 0          # diagnostic : nb de rc envoyés
        self._takeoff_time = None   # horodatage du décollage

    # ------------------------------------------------------------------
    # Heartbeat RC — thread dédié à 20Hz, DÉCOUPLÉ de pygame.
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
                    self._rc_count += 1
                except Exception as e:
                    print("[RC] erreur:", e)
            self._stop_rc.wait(0.05)

    # ------------------------------------------------------------------
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
        print(BANNER)
        warn_power_save()

        self.tello.connect()
        self.tello.set_speed(self.speed)
        self.tello.streamoff()
        self.tello.streamon()

        frame_read = self.tello.get_frame_read()

        threading.Thread(target=self._battery_worker, daemon=True).start()
        threading.Thread(target=self._rc_worker, daemon=True).start()

        # Diagnostic : affiche l'état du heartbeat 1×/seconde
        last_diag = time.time()
        last_count = 0

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

            if frame_read.stopped:
                break

            # Diagnostic 1×/s — montre si le heartbeat tourne quand le drone se pose
            now = time.time()
            if now - last_diag >= 1.0:
                rc_per_sec = self._rc_count - last_count
                last_count = self._rc_count
                last_diag = now
                if self._takeoff_time:
                    vol_t = int(now - self._takeoff_time)
                    print(f"[vol {vol_t:>3}s] rc/s={rc_per_sec:>2}  "
                          f"send_rc={self.send_rc_control}  bat={self._get_battery()}")

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

        self._stop_rc.set()
        self._stop_battery.set()
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
        # Heartbeat armé AVANT le décollage → contrôle réactif dès l'envol.
        # Note : le Tello ignore le rc pendant ~3-5s (routine de stabilisation
        # interne du décollage) — ce court délai est normal et incompressible.
        self.send_rc_control = True
        self._takeoff_time = time.time()
        print("[TAKEOFF] décollage + stabilisation (~5s)...")
        self.tello.takeoff()
        print("[TAKEOFF] terminé, contrôle manuel actif")

    def _do_land(self):
        self.send_rc_control = False
        self._takeoff_time = None
        self.tello.land()


def main():
    FrontEnd().run()


if __name__ == "__main__":
    main()
