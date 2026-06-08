"""
Tello Manual Control — standalone, zéro djitellopy, zéro cv2.VideoCapture
"""
import socket
import subprocess
import threading
import time
from threading import Thread

import cv2
import numpy as np
import pygame


# ─────────────────────────────────────────────────────────────────────────────
# Tello SDK minimal — socket UDP brut, pas de djitellopy
# ─────────────────────────────────────────────────────────────────────────────
class TelloSDK:
    IP   = '192.168.10.1'
    PORT = 8889
    TIMEOUT = 7  # secondes

    def __init__(self):
        self._addr = (self.IP, self.PORT)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(('', self.PORT))
        self._sock.settimeout(self.TIMEOUT)
        self._lock = threading.Lock()  # sérialise les commandes avec retour

    def _cmd(self, text: str) -> str:
        """Envoie une commande et attend la réponse — thread-safe."""
        with self._lock:
            print('Send:', text)
            self._sock.sendto(text.encode(), self._addr)
            try:
                data, _ = self._sock.recvfrom(1024)
                resp = data.decode().strip()
                print('Recv:', resp)
                return resp
            except socket.timeout:
                print('Timeout:', text)
                return 'timeout'

    def _send(self, text: str):
        """Envoie sans attendre de réponse (rc — feu-et-oubli)."""
        self._sock.sendto(text.encode(), self._addr)

    def connect(self):     return self._cmd('command')  in ('ok', 'OK')
    def takeoff(self):     return self._cmd('takeoff')  in ('ok', 'OK')
    def land(self):        return self._cmd('land')     in ('ok', 'OK')
    def streamon(self):    return self._cmd('streamon') in ('ok', 'OK')
    def streamoff(self):   return self._cmd('streamoff') in ('ok', 'OK')
    def set_speed(self, v): return self._cmd(f'speed {v}') in ('ok', 'OK')

    def battery(self) -> str:
        r = self._cmd('battery?').replace('\r\n', '').strip()
        return r if r.isdigit() else '?'

    def rc(self, lr: int, fb: int, ud: int, yaw: int):
        self._send(f'rc {lr} {fb} {ud} {yaw}')

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Flux vidéo via ffmpeg subprocess — pas de cv2.VideoCapture, pas de timeout
# ─────────────────────────────────────────────────────────────────────────────
class VideoStream:
    W, H = 960, 720
    URL  = 'udp://@0.0.0.0:11111?overrun_nonfatal=1&fifo_size=500000&reuse=1'

    def __init__(self):
        self._lock  = threading.Lock()
        self._frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        self._proc  = None
        self._stop  = False

    def _launch(self):
        subprocess.run(['pkill', '-f', 'ffmpeg.*11111'], capture_output=True)
        time.sleep(0.3)
        cmd = [
            'ffmpeg',
            '-loglevel', 'error',
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-i', self.URL,
            '-f', 'rawvideo',
            '-pix_fmt', 'bgr24',
            '-vf', f'scale={self.W}:{self.H}',
            '-vsync', '0',
            '-',
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=None, bufsize=0
        )
        print(f'[VIDEO] ffmpeg PID={self._proc.pid}')

    def start(self):
        self._launch()
        Thread(target=self._read, daemon=True).start()
        return self

    def _read(self):
        size = self.W * self.H * 3
        while not self._stop:
            if self._proc is None or self._proc.poll() is not None:
                print('[VIDEO] ffmpeg terminé, relance...')
                time.sleep(1)
                self._launch()
                continue
            try:
                raw = self._proc.stdout.read(size)
            except Exception:
                continue
            if len(raw) == size:
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.H, self.W, 3))
                with self._lock:
                    self._frame = frame

    @property
    def frame(self) -> np.ndarray:
        with self._lock:
            return self._frame.copy()

    def stop(self):
        self._stop = True
        if self._proc:
            self._proc.terminate()


# ─────────────────────────────────────────────────────────────────────────────
# Interface principale
# ─────────────────────────────────────────────────────────────────────────────
S   = 60   # vitesse RC
FPS = 30


class FrontEnd:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption('Tello — Contrôle manuel')
        self.screen = pygame.display.set_mode([960, 720])
        self.clock  = pygame.time.Clock()
        self.font   = pygame.font.SysFont('Arial', 20)

        self.drone = TelloSDK()
        self.video = VideoStream()

        # Vélocités RC courantes
        self.lr = self.fb = self.ud = self.yaw = 0
        self.flying = False

        # Batterie
        self._bat      = '?'
        self._bat_lock = threading.Lock()
        self._stop_ev  = threading.Event()

    # ── threads ──────────────────────────────────────────────────────────────

    def _rc_thread(self):
        """Heartbeat RC à 20 Hz, indépendant de pygame."""
        while not self._stop_ev.is_set():
            if self.flying:
                try:
                    self.drone.rc(self.lr, self.fb, self.ud, self.yaw)
                except Exception as e:
                    print('[RC erreur]', e)
            self._stop_ev.wait(0.05)

    def _bat_thread(self):
        """Lecture batterie toutes les 30s hors vol."""
        while not self._stop_ev.is_set():
            if not self.flying:
                try:
                    v = self.drone.battery()
                    with self._bat_lock:
                        self._bat = v
                except Exception:
                    pass
            self._stop_ev.wait(30)

    def _get_bat(self) -> str:
        with self._bat_lock:
            return self._bat

    # ── commandes vol (threads séparés — ne bloquent pas pygame) ─────────────

    def _do_takeoff(self):
        self.flying = True      # heartbeat armé avant le décollage
        self.drone.takeoff()

    def _do_land(self):
        self.flying = False
        self.drone.land()

    # ── boucle principale ────────────────────────────────────────────────────

    def run(self):
        print('[INIT] connexion au drone...')
        self.drone.connect()
        self.drone.set_speed(10)
        self.drone.streamoff()
        self.drone.streamon()

        self.video.start()

        Thread(target=self._rc_thread,  daemon=True).start()
        Thread(target=self._bat_thread, daemon=True).start()

        print('[INIT] prêt — T:décollage  L:atterrissage  ESCAPE:quitter')

        running = True
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE:
                        running = False
                    else:
                        self._keydown(ev.key)
                elif ev.type == pygame.KEYUP:
                    self._keyup(ev.key)

            # Rendu vidéo — le programme ne s'arrête JAMAIS à cause de la vidéo
            self.screen.fill((0, 0, 0))
            try:
                bgr  = self.video.frame
                rgb  = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                surf = pygame.surfarray.make_surface(
                    np.ascontiguousarray(np.flipud(np.rot90(rgb)))
                )
                self.screen.blit(surf, (0, 0))
            except Exception:
                pass

            # HUD
            hud = [
                (f'Batterie : {self._get_bat()}%', (0, 255, 80),  (10, 10)),
                ('T:Décollage  L:Atterrissage',    (200,200,200), (10, 650)),
                ('↑↓←→:Avancer  W/S:Haut/Bas  A/D:Rotation', (200,200,200), (10, 672)),
                ('ESCAPE:Quitter',                 (200,200,200), (10, 694)),
            ]
            for text, color, pos in hud:
                self.screen.blit(self.font.render(text, True, color), pos)

            pygame.display.update()
            self.clock.tick(FPS)

        # Nettoyage
        self._stop_ev.set()
        self.video.stop()
        try:
            self.drone.streamoff()
        except Exception:
            pass
        self.drone.close()
        pygame.quit()

    def _keydown(self, key):
        if   key == pygame.K_UP:    self.fb  =  S
        elif key == pygame.K_DOWN:  self.fb  = -S
        elif key == pygame.K_LEFT:  self.lr  = -S
        elif key == pygame.K_RIGHT: self.lr  =  S
        elif key == pygame.K_w:     self.ud  =  S
        elif key == pygame.K_s:     self.ud  = -S
        elif key == pygame.K_a:     self.yaw = -S
        elif key == pygame.K_d:     self.yaw =  S

    def _keyup(self, key):
        if   key in (pygame.K_UP,    pygame.K_DOWN):  self.fb  = 0
        elif key in (pygame.K_LEFT,  pygame.K_RIGHT):  self.lr  = 0
        elif key in (pygame.K_w,     pygame.K_s):      self.ud  = 0
        elif key in (pygame.K_a,     pygame.K_d):      self.yaw = 0
        elif key == pygame.K_t:
            Thread(target=self._do_takeoff, daemon=True).start()
        elif key == pygame.K_l:
            Thread(target=self._do_land, daemon=True).start()


if __name__ == '__main__':
    FrontEnd().run()
