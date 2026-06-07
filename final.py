from djitellopy import Tello
import cv2
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame
from pygame.locals import *
import numpy as np
import time
import threading

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
S    = 90
FPS  = 30
SCREEN_W, SCREEN_H = 960, 720

pygame.font.init()
font = pygame.font.SysFont("Times New Roman", 18)
font_big = pygame.font.SysFont("Times New Roman", 26, bold=True)

# ---------------------------------------------------------------------------
# Zones cliquables (définies UNE FOIS)
# ---------------------------------------------------------------------------
ZONE_360C   = pygame.Rect(25,  25,  100, 25)
ZONE_8      = pygame.Rect(175, 25,  100, 25)
ZONE_360I   = pygame.Rect(325, 25,  100, 25)
ZONE_360E   = pygame.Rect(475, 25,  100, 25)
ZONE_UPDOWN = pygame.Rect(625, 25,  100, 25)
ZONE_GOBACK = pygame.Rect(775, 25,  100, 25)
ZONE_TAKEOF = pygame.Rect(25,  633, 100, 25)
ZONE_LAND   = pygame.Rect(142, 633, 100, 25)
ZONE_PRED   = pygame.Rect(262, 633, 100, 25)
ZONE_THERM  = pygame.Rect(382, 633, 100, 25)
ZONE_CONT   = pygame.Rect(502, 633, 100, 25)
ZONE_INV    = pygame.Rect(622, 633, 100, 25)
ZONE_PHOTO  = pygame.Rect(777, 625, 50,  50)
ZONE_REC    = pygame.Rect(855, 625, 50,  50)


def load_img(path):
    try:
        return pygame.image.load(path).convert_alpha()
    except Exception:
        try:
            return pygame.image.load(path)
        except Exception:
            print(f"[WARN] image manquante : {path}")
            return None


# ---------------------------------------------------------------------------
class FrontEnd:
    """
    Contrôles :
        T / L       : Décollage / Atterrissage
        Flèches     : Avant / Arrière / Gauche / Droite
        W / S       : Monter / Descendre
        A / D       : Rotation gauche / droite
        KP8/2/4/6   : Flips
        H           : Cycle thème HUD (neutre → militaire)
        C / X       : Cycle HUD dans le thème courant
        LSHIFT      : Entrer mode Iron Man
        LCTRL       : Retour au niveau précédent
        KP0         : Retour au menu principal
        J           : Activer joystick (dans mode Iron Man)
        ESCAPE      : Quitter
    """

    # ------------------------------------------------------------------
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode([SCREEN_W, SCREEN_H])
        pygame.display.set_caption("Tello Controller")
        self.clock = pygame.time.Clock()

        # Icône
        icon = load_img('INTERFACES/Interface1/icone.png')
        if icon:
            pygame.display.set_icon(icon)

        # Drone
        self.tello = Tello()
        self.for_back_velocity  = 0
        self.left_right_velocity = 0
        self.up_down_velocity   = 0
        self.yaw_velocity       = 0
        self.speed              = 60
        self.send_rc_control    = False

        # Batterie (thread séparé — ne bloque jamais la vidéo)
        self._battery_str  = "??%"
        self._battery_lock = threading.Lock()
        self._stop_bat     = threading.Event()

        # Enregistrement
        self.recording     = False
        self.rec_count     = 0
        self.photo_count   = 0
        os.makedirs("frames",  exist_ok=True)
        os.makedirs("frames2", exist_ok=True)

        # Timer RC 50 ms
        pygame.time.set_timer(USEREVENT + 1, 50)

        # ---- Chargement des assets UNE SEULE FOIS ----
        self._load_assets()

    # ------------------------------------------------------------------
    def _load_assets(self):
        # HUD militaire
        self.hud_mil = [
            load_img("INTERFACES/MILITAIRE/HUDM1.png"),
            load_img("INTERFACES/MILITAIRE/HUDM2.png"),
            load_img("INTERFACES/MILITAIRE/HUDM3.png"),
            load_img("INTERFACES/MILITAIRE/HUDM4.png"),
        ]
        # HUD Iron Man
        self.hud_im = [
            load_img("INTERFACES/INTERFACEIRONMAN/HUDIM11.png"),
            load_img("INTERFACES/INTERFACEIRONMAN/HUDIM5.png"),
            load_img("INTERFACES/INTERFACEIRONMAN/HUDIM4.png"),
            load_img("INTERFACES/INTERFACEIRONMAN/HUDIM2.png"),
            load_img("INTERFACES/INTERFACEIRONMAN/HUDIM3.png"),
            load_img("INTERFACES/INTERFACEIRONMAN/HUDIM1.png"),
        ]
        # HUD Prédator
        self.hud_pred = [
            load_img("INTERFACES/INTERFACEPREDATOR/HUDP1.png"),
            load_img("INTERFACES/INTERFACEPREDATOR/HUDP2.png"),
            load_img("INTERFACES/INTERFACEPREDATOR/HUDP3.png"),
        ]
        self.hud_neutre = load_img("INTERFACES/neutre.png")

        # Boutons Interface 1
        self.btn = {
            "360c":    load_img("INTERFACES/Interface1/360C.png"),
            "8":       load_img("INTERFACES/Interface1/8Button.png"),
            "360i":    load_img("INTERFACES/Interface1/360IButton.png"),
            "360e":    load_img("INTERFACES/Interface1/360EButton.png"),
            "updown":  load_img("INTERFACES/Interface1/UpDownButton.png"),
            "goback":  load_img("INTERFACES/Interface1/GoBackButton.png"),
            "takeoff": load_img("INTERFACES/Interface1/TakeOffButton.png"),
            "land":    load_img("INTERFACES/Interface1/LandButton.png"),
            "pred":    load_img("INTERFACES/Interface1/PredatorButton.png"),
            "therm":   load_img("INTERFACES/Interface1/ThermiqueButton.png"),
            "invert":  load_img("INTERFACES/Interface1/InvertButton.png"),
            "contour": load_img("INTERFACES/Interface1/ContourButton.png"),
            "photo":   load_img("INTERFACES/Interface1/PhotoButton.png"),
            "rec":     load_img("INTERFACES/Interface1/RecordButton.png"),
        }
        # Boutons Iron Man
        self.btn_im = {
            "btn01":   load_img("INTERFACES/INTERFACEIRONMAN/Button01IM.png"),
            "takeoff": load_img("INTERFACES/INTERFACEIRONMAN/TakeOffButtonIM.png"),
            "land":    load_img("INTERFACES/INTERFACEIRONMAN/TakeOffButtonIM.png"),
            "rtl":     load_img("INTERFACES/INTERFACEIRONMAN/ReturnToLandButtonIM.png"),
            "m01":     load_img("INTERFACES/INTERFACEIRONMAN/Mission01ButtonIM.png"),
            "m02":     load_img("INTERFACES/INTERFACEIRONMAN/Mission02ButtonIM.png"),
            "m03":     load_img("INTERFACES/INTERFACEIRONMAN/Mission03ButtonIM.png"),
            "m04":     load_img("INTERFACES/INTERFACEIRONMAN/Mission04ButtonIM.png"),
            "pred":    load_img("INTERFACES/INTERFACEIRONMAN/PredatorButtonIM.png"),
            "therm":   load_img("INTERFACES/INTERFACEIRONMAN/ThermiqueButtonIM.png"),
            "invert":  load_img("INTERFACES/INTERFACEIRONMAN/InvertButtonIM.png"),
            "contour": load_img("INTERFACES/INTERFACEIRONMAN/ContourButtonIM.png"),
        }
        # Boutons Prédator
        self.btn_pred = {
            "btn01": load_img("INTERFACES/INTERFACEPREDATOR/button01P.png"),
        }
        self.img_manette = load_img("INTERFACES/MANETTE01.png")

        # État HUD courant
        self.hud_theme = "mil"   # "mil" | "im" | "pred" | "neutre"
        self.hud_idx   = 0       # index dans la liste du thème

    # ------------------------------------------------------------------
    # Batterie en thread
    # ------------------------------------------------------------------
    def _battery_worker(self):
        while not self._stop_bat.is_set():
            try:
                val = self.tello.get_battery()
                with self._battery_lock:
                    self._battery_str = f"{val}%" if val else "??%"
            except Exception:
                pass
            time.sleep(30)

    def _get_battery(self):
        with self._battery_lock:
            return self._battery_str

    # ------------------------------------------------------------------
    # Utilitaires HUD
    # ------------------------------------------------------------------
    def _current_hud(self):
        if self.hud_theme == "mil":
            lst = self.hud_mil
        elif self.hud_theme == "im":
            lst = self.hud_im
        elif self.hud_theme == "pred":
            lst = self.hud_pred
        else:
            return self.hud_neutre
        return lst[self.hud_idx % len(lst)] if lst else None

    def _cycle_hud(self, step=1):
        if self.hud_theme == "mil":
            n = len(self.hud_mil)
        elif self.hud_theme == "im":
            n = len(self.hud_im)
        elif self.hud_theme == "pred":
            n = len(self.hud_pred)
        else:
            return
        self.hud_idx = (self.hud_idx + step) % n

    # ------------------------------------------------------------------
    # Traitement du frame selon le filtre actif
    # ------------------------------------------------------------------
    @staticmethod
    def _apply_filter(raw, mode="normal"):
        if mode == "hsv":
            return cv2.cvtColor(raw, cv2.COLOR_BGR2HSV)
        elif mode == "invert":
            rgb = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
            return cv2.bitwise_not(rgb)
        elif mode == "contour":
            g = cv2.GaussianBlur(cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY), (7, 7), 5.64)
            edges = cv2.Canny(g, 25, 75)
            return cv2.cvtColor(edges, cv2.COLOR_GRAY2RGB)
        elif mode == "gray":
            g = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
            gray_inv = cv2.bitwise_not(g)
            return cv2.cvtColor(gray_inv, cv2.COLOR_GRAY2RGB)
        elif mode == "hls2rgb":
            return cv2.cvtColor(raw, cv2.COLOR_HLS2RGB)
        elif mode == "rgb2hls":
            return cv2.cvtColor(raw, cv2.COLOR_RGB2HLS)
        else:
            return cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)

    @staticmethod
    def _to_surface(frame_rgb):
        f = np.rot90(frame_rgb)
        f = np.flipud(f)
        return pygame.surfarray.make_surface(f)

    # ------------------------------------------------------------------
    # Dessin UI principal
    # ------------------------------------------------------------------
    def _draw_main_ui(self, frame_surf, battery):
        self.screen.fill((0, 0, 0))
        self.screen.blit(frame_surf, (0, 0))

        hud = self._current_hud()
        if hud:
            self.screen.blit(hud, (15, 90))

        b = self.btn
        for img, pos in [
            (b["360c"],  (20,  10)),
            (b["8"],     (170, 10)),
            (b["360i"],  (320, 10)),
            (b["360e"],  (470, 10)),
            (b["updown"],(620, 10)),
            (b["goback"],(770, 10)),
            (b["takeoff"],(20,  620)),
            (b["land"],  (140, 620)),
            (b["pred"],  (260, 620)),
            (b["therm"], (380, 620)),
            (b["contour"],(500, 620)),
            (b["invert"],(620, 620)),
            (b["photo"], (760, 610)),
            (b["rec"],   (850, 622)),
        ]:
            if img:
                self.screen.blit(img, pos)

        bat = font.render(battery, 1, (255, 255, 255))
        self.screen.blit(bat, (900, 90))

        if self.recording:
            rec = font_big.render("● REC", 1, (255, 30, 30))
            self.screen.blit(rec, (SCREEN_W - 90, 10))

    # ------------------------------------------------------------------
    # Missions (threads non-bloquants)
    # ------------------------------------------------------------------
    def _mission(self, steps):
        def _run():
            for (lr, fb, ud, yaw), dur in steps:
                self.tello.send_rc_control(lr, fb, ud, yaw)
                time.sleep(dur)
            self.tello.send_rc_control(0, 0, 0, 0)
        threading.Thread(target=_run, daemon=True).start()

    def _mission_360c(self):
        self._mission([((0,20,0,0),2),((20,0,0,-20),3),((0,-20,0,0),2),((0,0,0,0),0.5)])

    def _mission_8(self):
        self._mission([
            ((60,60,0,60),1),((-60,60,0,-60),1),((-60,60,0,-60),1),((-60,60,0,-60),1),
            ((-60,60,0,-60),1),((60,60,0,60),1),((60,60,0,60),1),((60,60,0,60),1),((0,0,0,0),0.5)
        ])

    def _mission_360i(self):
        self._mission([((60,0,0,-60),7)])

    def _mission_360e(self):
        self._mission([((60,0,0,60),4)])

    def _mission_updown(self):
        self._mission([((0,0,60,0),3),((0,0,0,30),4),((0,0,-60,0),3)])

    def _mission_goback(self):
        self._mission([((0,60,0,0),5),((0,0,0,40),2),((0,60,0,0),3),((0,0,0,0),0.5)])

    # ------------------------------------------------------------------
    # Photo / Enregistrement
    # ------------------------------------------------------------------
    def _take_photo(self, raw):
        self.photo_count += 1
        path = f"frames/photo_{self.photo_count:04d}.jpg"
        cv2.imwrite(path, raw)
        print(f"[PHOTO] {path}")

    def _record_frame(self, raw):
        path = f"frames2/frame_{self.rec_count:06d}.jpg"
        cv2.imwrite(path, raw)
        self.rec_count += 1

    # ------------------------------------------------------------------
    # Clics souris
    # ------------------------------------------------------------------
    def _handle_click(self, pos, raw):
        if ZONE_360C.collidepoint(pos):   self._mission_360c()
        elif ZONE_8.collidepoint(pos):    self._mission_8()
        elif ZONE_360I.collidepoint(pos): self._mission_360i()
        elif ZONE_360E.collidepoint(pos): self._mission_360e()
        elif ZONE_UPDOWN.collidepoint(pos): self._mission_updown()
        elif ZONE_GOBACK.collidepoint(pos): self._mission_goback()
        elif ZONE_TAKEOF.collidepoint(pos):
            self.tello.takeoff(); self.send_rc_control = True
        elif ZONE_LAND.collidepoint(pos):
            self.tello.land(); self.send_rc_control = False
        elif ZONE_PRED.collidepoint(pos):
            self._enter_filter_loop("hsv")
        elif ZONE_THERM.collidepoint(pos):
            self._enter_filter_loop("rgb2hls")
        elif ZONE_INV.collidepoint(pos):
            self._enter_filter_loop("invert")
        elif ZONE_CONT.collidepoint(pos):
            self._enter_filter_loop("contour")
        elif ZONE_PHOTO.collidepoint(pos):
            self._take_photo(raw)
        elif ZONE_REC.collidepoint(pos):
            self.recording = not self.recording

    # ------------------------------------------------------------------
    # Boucle de filtre vidéo (LCTRL pour quitter)
    # ------------------------------------------------------------------
    def _enter_filter_loop(self, mode, frame_read=None):
        if frame_read is None:
            return
        running = True
        while running:
            raw = frame_read.frame
            if raw is None:
                continue
            f = self._apply_filter(raw, mode)
            surf = self._to_surface(f)
            self.screen.blit(surf, (0, 0))
            hud = self._current_hud()
            if hud:
                self.screen.blit(hud, (15, 90))
            bat = font.render(self._get_battery(), 1, (255, 255, 255))
            self.screen.blit(bat, (900, 90))
            label = font_big.render(f"MODE: {mode.upper()}  |  LCTRL = retour", 1, (0, 220, 255))
            self.screen.blit(label, (10, SCREEN_H - 30))
            pygame.display.update()
            self.clock.tick(FPS)
            for ev in pygame.event.get():
                if ev.type == KEYDOWN:
                    if ev.key == K_LCTRL:
                        running = False

    # ------------------------------------------------------------------
    # Mode Iron Man (LSHIFT)
    # ------------------------------------------------------------------
    def _ironman_mode(self, frame_read, s=90):
        """Sous-boucle Iron Man avec ses propres contrôles."""
        self.hud_theme = "im"
        self.hud_idx   = 0
        b = self.btn_im

        # Filtre vidéo courant dans ce mode
        filter_cycle = ["normal", "hsv", "invert", "contour", "gray", "hls2rgb", "rgb2hls"]
        filter_idx   = 0

        active = True
        while active:
            raw = frame_read.frame
            if raw is None:
                self.clock.tick(FPS)
                continue

            mode = filter_cycle[filter_idx % len(filter_cycle)]
            f    = self._apply_filter(raw, mode)
            surf = self._to_surface(f)

            self.screen.fill((0, 0, 0))
            self.screen.blit(surf, (0, 0))

            for img, pos in [
                (b["btn01"],  (180, 10)),
                (b["rtl"],    (20,  10)),
                (b["m01"],    (320, 10)),
                (b["m02"],    (470, 10)),
                (b["m03"],    (620, 10)),
                (b["m04"],    (770, 10)),
                (b["takeoff"],(20,  620)),
                (b["land"],   (140, 620)),
                (b["pred"],   (260, 620)),
                (b["therm"],  (380, 620)),
                (b["contour"],(500, 620)),
                (b["invert"], (620, 620)),
            ]:
                if img:
                    self.screen.blit(img, pos)

            hud = self._current_hud()
            if hud:
                self.screen.blit(hud, (0, 0))

            bat = font.render(self._get_battery(), 1, (255, 255, 255))
            self.screen.blit(bat, (900, 90))

            mode_lbl = font.render(f"FILTRE: {mode}  |  LSHIFT=filtre suivant  LCTRL=retour", 1, (0, 220, 255))
            self.screen.blit(mode_lbl, (10, SCREEN_H - 20))

            pygame.display.update()
            self.clock.tick(FPS)

            for ev in pygame.event.get():
                if ev.type == USEREVENT + 1:
                    if self.send_rc_control:
                        self.tello.send_rc_control(
                            self.left_right_velocity, self.for_back_velocity,
                            self.up_down_velocity, self.yaw_velocity)

                elif ev.type == KEYDOWN:
                    if ev.key == K_LCTRL:
                        active = False
                        self.hud_theme = "mil"
                        self.hud_idx   = 0

                    elif ev.key == K_LSHIFT:
                        filter_idx += 1

                    elif ev.key == K_c:
                        self._cycle_hud(1)
                    elif ev.key == K_x:
                        self._cycle_hud(-1)

                    elif ev.key == K_t:
                        self.tello.takeoff(); self.send_rc_control = True
                    elif ev.key == K_l:
                        self.tello.land(); self.send_rc_control = False

                    elif ev.key == K_UP:    self.for_back_velocity = s
                    elif ev.key == K_DOWN:  self.for_back_velocity = -s
                    elif ev.key == K_RIGHT: self.left_right_velocity = s
                    elif ev.key == K_LEFT:  self.left_right_velocity = -s
                    elif ev.key == K_w:     self.up_down_velocity = s
                    elif ev.key == K_s:     self.up_down_velocity = -s
                    elif ev.key == K_d:     self.yaw_velocity = s
                    elif ev.key == K_a:     self.yaw_velocity = -s

                    elif ev.key == K_j:
                        self._joystick_mode(frame_read, s)

                    elif ev.key == K_ESCAPE:
                        active = False
                        return True  # signal quitter

                elif ev.type == KEYUP:
                    if ev.key in (K_UP, K_DOWN):    self.for_back_velocity = 0
                    if ev.key in (K_LEFT, K_RIGHT):  self.left_right_velocity = 0
                    if ev.key in (K_w, K_s):         self.up_down_velocity = 0
                    if ev.key in (K_a, K_d):         self.yaw_velocity = 0
                    if self.send_rc_control:
                        self.tello.send_rc_control(
                            self.left_right_velocity, self.for_back_velocity,
                            self.up_down_velocity, self.yaw_velocity)

        return False

    # ------------------------------------------------------------------
    # Mode Joystick
    # ------------------------------------------------------------------
    def _joystick_mode(self, frame_read, s=60):
        n = pygame.joystick.get_count()
        if n == 0:
            print("[JOYSTICK] Aucun joystick détecté")
            return
        joy = pygame.joystick.Joystick(0)
        joy.init()
        print(f"[JOYSTICK] {joy.get_name()}")

        active = True
        while active:
            raw = frame_read.frame
            if raw is None:
                self.clock.tick(FPS); continue

            surf = self._to_surface(cv2.cvtColor(raw, cv2.COLOR_BGR2RGB))
            self.screen.fill((0, 0, 0))
            self.screen.blit(surf, (0, 0))
            if self.img_manette:
                self.screen.blit(self.img_manette, (25, 25))
            hud = self._current_hud()
            if hud:
                self.screen.blit(hud, (0, 0))
            bat = font.render(self._get_battery(), 1, (255, 255, 255))
            self.screen.blit(bat, (900, 90))
            lbl = font.render("Mode JOYSTICK  |  ESCAPE = retour", 1, (0, 220, 255))
            self.screen.blit(lbl, (10, SCREEN_H - 20))
            pygame.display.update()
            self.clock.tick(FPS)

            for ev in pygame.event.get():
                if ev.type == JOYAXISMOTION:
                    if ev.axis == 1:
                        self.for_back_velocity = int(-ev.value * s) if abs(ev.value) > 0.2 else 0
                        self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)
                    elif ev.axis == 3:
                        self.left_right_velocity = int(ev.value * s) if abs(ev.value) > 0.2 else 0
                        self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)

                elif ev.type == JOYBUTTONDOWN:
                    if ev.button == 0:   self.tello.takeoff(); self.send_rc_control = True
                    elif ev.button == 1: self.tello.land(); self.send_rc_control = False
                    elif ev.button == 2: self.tello.send_rc_control(0, 0, 0, 0)
                    elif ev.button == 3: self._cycle_hud(1)
                    elif ev.button == 4: self.yaw_velocity = -s; self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)
                    elif ev.button == 5: self.yaw_velocity = s;  self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)
                    elif ev.button == 6: self.up_down_velocity = -s; self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)
                    elif ev.button == 7: self.up_down_velocity = s;  self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)

                elif ev.type == JOYBUTTONUP:
                    if ev.button in (4, 5):   self.yaw_velocity = 0
                    if ev.button in (6, 7):   self.up_down_velocity = 0
                    self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity, self.yaw_velocity)

                elif ev.type == JOYHATMOTION:
                    flips = {(0,1):"f",(0,-1):"b",(-1,0):"l",(1,0):"r"}
                    if ev.value in flips:
                        self.tello.flip(flips[ev.value])

                elif ev.type == KEYDOWN:
                    if ev.key == K_ESCAPE:
                        active = False

    # ------------------------------------------------------------------
    # Keydown / Keyup (boucle principale)
    # ------------------------------------------------------------------
    def keydown(self, key):
        if key == K_UP:    self.for_back_velocity = S
        elif key == K_DOWN:  self.for_back_velocity = -S
        elif key == K_LEFT:  self.left_right_velocity = -S
        elif key == K_RIGHT: self.left_right_velocity = S
        elif key == K_w:     self.up_down_velocity = S
        elif key == K_s:     self.up_down_velocity = -S
        elif key == K_a:     self.yaw_velocity = -S
        elif key == K_d:     self.yaw_velocity = S

    def keyup(self, key):
        if key in (K_UP, K_DOWN):    self.for_back_velocity = 0
        elif key in (K_LEFT, K_RIGHT): self.left_right_velocity = 0
        elif key in (K_w, K_s):       self.up_down_velocity = 0
        elif key in (K_a, K_d):       self.yaw_velocity = 0
        elif key == K_t:
            self.tello.takeoff(); self.send_rc_control = True
        elif key == K_l:
            self.tello.land(); self.send_rc_control = False
        elif key == K_KP8: self.tello.flip("f"); self.send_rc_control = True
        elif key == K_KP2: self.tello.flip("b"); self.send_rc_control = True
        elif key == K_KP4: self.tello.flip("l"); self.send_rc_control = True
        elif key == K_KP6: self.tello.flip("r"); self.send_rc_control = True

    def update(self):
        if self.send_rc_control:
            self.tello.send_rc_control(
                self.left_right_velocity, self.for_back_velocity,
                self.up_down_velocity, self.yaw_velocity)

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------
    def run(self):
        if not self.tello.connect():
            print("Tello non connecté"); return
        if not self.tello.set_speed(self.speed):
            print("Impossible de régler la vitesse"); return

        self.tello.streamoff()
        self.tello.streamon()
        time.sleep(2)  # laisse le drone initialiser le stream H264

        frame_read = self.tello.get_frame_read()

        # Premier relevé batterie (bloquant, avant la boucle)
        try:
            val = self.tello.get_battery()
            with self._battery_lock:
                self._battery_str = f"{val}%" if val else "??%"
        except Exception:
            pass

        # Thread batterie (30s refresh)
        threading.Thread(target=self._battery_worker, daemon=True).start()

        should_stop = False
        while not should_stop:

            for event in pygame.event.get():
                if event.type == USEREVENT + 1:
                    self.update()

                elif event.type == QUIT:
                    should_stop = True

                elif event.type == KEYDOWN:
                    if event.key == K_ESCAPE:
                        should_stop = True

                    elif event.key == K_LSHIFT:
                        # Entrée mode Iron Man
                        quit_signal = self._ironman_mode(frame_read)
                        if quit_signal:
                            should_stop = True

                    elif event.key == K_h:
                        # Cycle thème : mil → neutre → mil
                        themes = ["mil", "neutre"]
                        idx = themes.index(self.hud_theme) if self.hud_theme in themes else 0
                        self.hud_theme = themes[(idx + 1) % len(themes)]
                        self.hud_idx = 0

                    elif event.key == K_c:
                        self._cycle_hud(1)
                    elif event.key == K_x:
                        self._cycle_hud(-1)

                    else:
                        self.keydown(event.key)

                elif event.type == KEYUP:
                    self.keyup(event.key)

                elif event.type == MOUSEBUTTONUP and event.button == 1:
                    raw = frame_read.frame
                    if raw is not None:
                        self._handle_click(event.pos, raw)

            if frame_read.stopped:
                break

            raw = frame_read.frame
            if raw is None:
                self.clock.tick(FPS)
                continue

            if self.recording:
                self._record_frame(raw)

            f    = self._apply_filter(raw, "normal")
            surf = self._to_surface(f)
            self._draw_main_ui(surf, self._get_battery())
            pygame.display.update()
            self.clock.tick(FPS)

        self._stop_bat.set()
        self.tello.end()
        pygame.quit()


# ---------------------------------------------------------------------------
def main():
    FrontEnd().run()

if __name__ == '__main__':
    main()
