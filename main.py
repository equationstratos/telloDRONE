"""
Tello Drone Controller — Refactored
Améliorations : machine à états, assets chargés une fois, navigation autonome CV (Option 2)

Contrôles principaux :
    T           : Décollage
    L           : Atterrissage
    Flèches     : Avant / Arrière / Gauche / Droite
    W / S       : Monter / Descendre
    A / D       : Rotation gauche / droite
    KP8/2/4/6   : Flips (avant/arrière/gauche/droite)
    H           : Interface neutre / militaire
    C           : Cycle d'interface (mode courant)
    LCTRL       : Retour au mode précédent
    KP0         : Retour au mode principal
    P           : Mode prédateur (HSV)
    I           : Mode inversé
    O           : Mode contour
    G           : Mode niveaux de gris
    N           : Mode navigation autonome (IA vision)
    J           : Mode joystick
    ESCAPE      : Quitter
"""

from djitellopy import Tello
import cv2
import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame
from pygame.locals import *
import numpy as np
import time
import threading
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SPEED = 60
FPS = 25
SCREEN_W, SCREEN_H = 960, 720
FONT_NAME = "Times New Roman"
FONT_SIZE = 18
PICTURE_DIR = "frames"
RECORD_DIR = "frames_record"


# ---------------------------------------------------------------------------
# Enums de mode
# ---------------------------------------------------------------------------
class DisplayMode(Enum):
    NORMAL = auto()
    PREDATOR = auto()   # HSV
    THERMAL = auto()    # HLS
    INVERT = auto()
    CONTOUR = auto()
    GRAYSCALE = auto()
    AUTONOMOUS = auto()


class InterfaceTheme(Enum):
    NEUTRAL = auto()
    MILITARY = auto()
    IRONMAN = auto()
    PREDATOR = auto()


class AutonomousState(Enum):
    IDLE = auto()
    TAKEOFF = auto()
    EXPLORE = auto()
    AVOID = auto()
    RETURN = auto()
    LAND = auto()


# ---------------------------------------------------------------------------
# Module de navigation autonome par vision
# ---------------------------------------------------------------------------
class AutonomousNavigator:
    """
    Navigation autonome basée sur la vision caméra.

    Principe (Option 2) :
    - Divise l'image en 3 colonnes (gauche / centre / droite)
    - Calcule la densité de contours dans chaque zone via Canny
    - Si la zone centrale est obstruée → tourne
    - Si les zones latérales sont libres → avance
    - Revient au point de départ en inversant la séquence de mouvements
    """

    OBSTACLE_THRESHOLD = 0.08   # densité de pixels contour déclenchant l'évitement
    EXPLORE_SPEED = 30
    TURN_SPEED = 35
    VERTICAL_CROP = 0.25        # ignore le haut et le bas de l'image (plafond/sol)

    def __init__(self, tello: Tello):
        self.tello = tello
        self.state = AutonomousState.IDLE
        self.move_history: list[dict] = []  # log des commandes pour le retour
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self.debug_info = {
            "left": 0.0, "center": 0.0, "right": 0.0,
            "state": "IDLE", "action": ""
        }

    # ------------------------------------------------------------------
    def update_frame(self, frame: np.ndarray):
        with self._frame_lock:
            self._frame = frame.copy()

    def _get_frame(self) -> Optional[np.ndarray]:
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    # ------------------------------------------------------------------
    def _analyze_frame(self, frame: np.ndarray) -> dict[str, float]:
        """Retourne la densité d'obstacles dans les zones gauche/centre/droite."""
        h, w = frame.shape[:2]
        y0 = int(h * self.VERTICAL_CROP)
        y1 = int(h * (1 - self.VERTICAL_CROP))
        roi = frame[y0:y1, :]

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (7, 7), 0)
        edges = cv2.Canny(blur, 30, 90)

        col_w = w // 3
        zones = {
            "left":   edges[:, :col_w],
            "center": edges[:, col_w:2*col_w],
            "right":  edges[:, 2*col_w:],
        }
        total_pixels = (y1 - y0) * col_w
        return {k: float(np.count_nonzero(v)) / total_pixels for k, v in zones.items()}

    # ------------------------------------------------------------------
    def _send(self, lr: int, fb: int, ud: int, yaw: int):
        self.tello.send_rc_control(lr, fb, ud, yaw)
        self.move_history.append({"lr": lr, "fb": fb, "ud": ud, "yaw": yaw})

    def _stop_drone(self):
        self.tello.send_rc_control(0, 0, 0, 0)

    # ------------------------------------------------------------------
    def _navigate_step(self):
        """Appelé à chaque tick de la boucle autonome."""
        frame = self._get_frame()
        if frame is None:
            return

        density = self._analyze_frame(frame)
        self.debug_info.update(density)

        center_clear = density["center"] < self.OBSTACLE_THRESHOLD
        left_clear   = density["left"]   < self.OBSTACLE_THRESHOLD
        right_clear  = density["right"]  < self.OBSTACLE_THRESHOLD

        if self.state == AutonomousState.EXPLORE:
            if center_clear:
                self._send(0, self.EXPLORE_SPEED, 0, 0)
                self.debug_info["action"] = "AVANCE"
            else:
                self.state = AutonomousState.AVOID
                self.debug_info["state"] = "AVOID"

        elif self.state == AutonomousState.AVOID:
            if center_clear:
                self.state = AutonomousState.EXPLORE
                self.debug_info["state"] = "EXPLORE"
            elif right_clear:
                self._send(0, 0, 0, self.TURN_SPEED)
                self.debug_info["action"] = "TOURNE DROITE"
            elif left_clear:
                self._send(0, 0, 0, -self.TURN_SPEED)
                self.debug_info["action"] = "TOURNE GAUCHE"
            else:
                self._send(0, -self.EXPLORE_SPEED, 0, 0)
                self.debug_info["action"] = "RECULE"

        elif self.state == AutonomousState.RETURN:
            if not self.move_history:
                self.state = AutonomousState.LAND
                self.debug_info["state"] = "LAND"
                return
            cmd = self.move_history.pop()
            # Inverser la commande
            self.tello.send_rc_control(-cmd["lr"], -cmd["fb"], -cmd["ud"], -cmd["yaw"])
            self.debug_info["action"] = "RETOUR"

    # ------------------------------------------------------------------
    def _run_loop(self):
        while not self._stop_event.is_set():
            self._navigate_step()
            if self.state == AutonomousState.LAND:
                self._stop_drone()
                self.tello.land()
                self.state = AutonomousState.IDLE
                break
            time.sleep(0.1)
        self._stop_drone()

    # ------------------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self.move_history.clear()
        self.state = AutonomousState.TAKEOFF
        self.debug_info["state"] = "TAKEOFF"
        self.tello.takeoff()
        time.sleep(2)
        self.state = AutonomousState.EXPLORE
        self.debug_info["state"] = "EXPLORE"
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._stop_drone()
        self.state = AutonomousState.IDLE
        self.debug_info["state"] = "IDLE"

    def request_return(self):
        self.state = AutonomousState.RETURN
        self.debug_info["state"] = "RETURN"

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# ---------------------------------------------------------------------------
# Chargeur d'assets (charge une seule fois au démarrage)
# ---------------------------------------------------------------------------
def _load(path: str) -> Optional[pygame.Surface]:
    try:
        return pygame.image.load(path).convert_alpha()
    except Exception:
        try:
            return pygame.image.load(path)
        except Exception:
            print(f"[WARN] Image introuvable : {path}")
            return None


class Assets:
    def __init__(self):
        # --- Interfaces militaires ---
        self.mil = [
            _load("INTERFACES/MILITAIRE/HUDM1.png"),
            _load("INTERFACES/MILITAIRE/HUDM2.png"),
            _load("INTERFACES/MILITAIRE/HUDM3.png"),
            _load("INTERFACES/MILITAIRE/HUDM4.png"),
        ]
        # --- Interfaces Iron Man ---
        self.im = [
            _load("INTERFACES/INTERFACEIRONMAN/HUDIM11.png"),
            _load("INTERFACES/INTERFACEIRONMAN/HUDIM5.png"),
            _load("INTERFACES/INTERFACEIRONMAN/HUDIM4.png"),
            _load("INTERFACES/INTERFACEIRONMAN/HUDIM2.png"),
            _load("INTERFACES/INTERFACEIRONMAN/HUDIM3.png"),
            _load("INTERFACES/INTERFACEIRONMAN/HUDIM1.png"),
        ]
        # --- Interfaces Prédator ---
        self.pred = [
            _load("INTERFACES/INTERFACEPREDATOR/HUDP1.png"),
            _load("INTERFACES/INTERFACEPREDATOR/HUDP2.png"),
            _load("INTERFACES/INTERFACEPREDATOR/HUDP3.png"),
        ]
        # --- Interface neutre ---
        self.neutral = _load("INTERFACES/neutre.png")
        # --- Boutons Interface 1 ---
        self.btn = {
            "360c":     _load("INTERFACES/Interface1/360C.png"),
            "8":        _load("INTERFACES/Interface1/8Button.png"),
            "360i":     _load("INTERFACES/Interface1/360IButton.png"),
            "360e":     _load("INTERFACES/Interface1/360EButton.png"),
            "updown":   _load("INTERFACES/Interface1/UpDownButton.png"),
            "goback":   _load("INTERFACES/Interface1/GoBackButton.png"),
            "takeoff":  _load("INTERFACES/Interface1/TakeOffButton.png"),
            "land":     _load("INTERFACES/Interface1/LandButton.png"),
            "predator": _load("INTERFACES/Interface1/PredatorButton.png"),
            "thermal":  _load("INTERFACES/Interface1/ThermiqueButton.png"),
            "invert":   _load("INTERFACES/Interface1/InvertButton.png"),
            "contour":  _load("INTERFACES/Interface1/ContourButton.png"),
            "photo":    _load("INTERFACES/Interface1/PhotoButton.png"),
            "record":   _load("INTERFACES/Interface1/RecordButton.png"),
            "demo":     _load("INTERFACES/Interface1/DemoButton.png"),
        }
        # --- Icône ---
        self.icon = _load("INTERFACES/Interface1/icone.png")
        # --- Joystick overlay ---
        self.joystick_img = _load("INTERFACES/MANETTE01.png")


# ---------------------------------------------------------------------------
# Zones cliquables (définies une seule fois)
# ---------------------------------------------------------------------------
CLICK_ZONES = {
    "360c":    pygame.Rect(25,  25,  100, 25),
    "8":       pygame.Rect(175, 25,  100, 25),
    "360i":    pygame.Rect(325, 25,  100, 25),
    "360e":    pygame.Rect(475, 25,  100, 25),
    "updown":  pygame.Rect(625, 25,  100, 25),
    "goback":  pygame.Rect(775, 25,  100, 25),
    "takeoff": pygame.Rect(25,  633, 100, 25),
    "land":    pygame.Rect(142, 633, 100, 25),
    "predator":pygame.Rect(262, 633, 100, 25),
    "thermal": pygame.Rect(382, 633, 100, 25),
    "contour": pygame.Rect(502, 633, 100, 25),
    "invert":  pygame.Rect(622, 633, 100, 25),
    "photo":   pygame.Rect(777, 625, 50,  50),
    "record":  pygame.Rect(855, 625, 50,  50),
}


# ---------------------------------------------------------------------------
# Application principale
# ---------------------------------------------------------------------------
class TelloApp:
    def __init__(self):
        pygame.init()
        pygame.font.init()

        self.screen = pygame.display.set_mode([SCREEN_W, SCREEN_H])
        pygame.display.set_caption("Tello Controller — IA Vision")

        self.font = pygame.font.SysFont(FONT_NAME, FONT_SIZE)
        self.font_big = pygame.font.SysFont(FONT_NAME, 28, bold=True)
        self.clock = pygame.time.Clock()

        self.tello = Tello()
        self.assets = Assets()

        if self.assets.icon:
            pygame.display.set_icon(self.assets.icon)

        # Vélocités drone
        self.lr_vel = 0   # left-right
        self.fb_vel = 0   # forward-back
        self.ud_vel = 0   # up-down
        self.yaw_vel = 0  # rotation
        self.send_rc = False

        # Modes
        self.display_mode = DisplayMode.NORMAL
        self.theme = InterfaceTheme.MILITARY
        self.hud_index = 0   # index dans la liste HUD du thème courant
        self.recording = False
        self.record_count = 0
        self.photo_count = 0

        # Navigation autonome
        self.navigator: Optional[AutonomousNavigator] = None

        # Joystick
        self.joystick_active = False
        self.joystick: Optional[pygame.joystick.Joystick] = None

        # Timer RC
        pygame.time.set_timer(USEREVENT + 1, 50)

        # Créer dossiers de sortie
        os.makedirs(PICTURE_DIR, exist_ok=True)
        os.makedirs(RECORD_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # HUD courant
    # ------------------------------------------------------------------
    def _current_hud(self) -> Optional[pygame.Surface]:
        if self.theme == InterfaceTheme.MILITARY:
            huds = self.assets.mil
        elif self.theme == InterfaceTheme.IRONMAN:
            huds = self.assets.im
        elif self.theme == InterfaceTheme.PREDATOR:
            huds = self.assets.pred
        else:
            return self.assets.neutral

        if not huds:
            return None
        return huds[self.hud_index % len(huds)]

    def _cycle_hud(self, step: int = 1):
        if self.theme == InterfaceTheme.MILITARY:
            n = len(self.assets.mil)
        elif self.theme == InterfaceTheme.IRONMAN:
            n = len(self.assets.im)
        elif self.theme == InterfaceTheme.PREDATOR:
            n = len(self.assets.pred)
        else:
            return
        self.hud_index = (self.hud_index + step) % n

    # ------------------------------------------------------------------
    # Traitement vidéo
    # ------------------------------------------------------------------
    def _process_frame(self, raw: np.ndarray) -> pygame.Surface:
        """Applique le filtre du mode d'affichage courant."""
        mode = self.display_mode

        if mode == DisplayMode.PREDATOR:
            frame = cv2.cvtColor(raw, cv2.COLOR_BGR2HSV)
        elif mode == DisplayMode.THERMAL:
            frame = cv2.cvtColor(raw, cv2.COLOR_BGR2HLS)
        elif mode == DisplayMode.INVERT:
            frame = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
            frame = cv2.bitwise_not(frame)
        elif mode == DisplayMode.CONTOUR:
            gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (7, 7), 5.64)
            edges = cv2.Canny(blurred, 25, 75)
            frame = cv2.cvtColor(edges, cv2.COLOR_GRAY2RGB)
        elif mode == DisplayMode.GRAYSCALE:
            gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        elif mode == DisplayMode.AUTONOMOUS:
            frame = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
            # Overlay de debug CV
            frame = self._draw_autonomous_overlay(frame, raw)
        else:
            frame = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)

        frame = np.rot90(frame)
        frame = np.flipud(frame)
        return pygame.surfarray.make_surface(frame)

    def _draw_autonomous_overlay(self, frame_rgb: np.ndarray, raw: np.ndarray) -> np.ndarray:
        """Dessine les zones d'analyse sur le frame en mode autonome."""
        if self.navigator is None:
            return frame_rgb

        h, w = frame_rgb.shape[:2]
        y0 = int(h * AutonomousNavigator.VERTICAL_CROP)
        y1 = int(h * (1 - AutonomousNavigator.VERTICAL_CROP))
        col_w = w // 3

        info = self.navigator.debug_info

        def density_color(d: float):
            if d < AutonomousNavigator.OBSTACLE_THRESHOLD:
                return (0, 220, 0)   # vert = libre
            return (220, 30, 30)     # rouge = obstacle

        # Dessiner les 3 zones
        zones = [
            (0, col_w, "G", info["left"]),
            (col_w, 2*col_w, "C", info["center"]),
            (2*col_w, w, "D", info["right"]),
        ]
        overlay = frame_rgb.copy()
        for x0, x1, label, density in zones:
            color = density_color(density)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 2)
            cv2.putText(overlay, f"{label}:{density:.2f}", (x0+5, y0+20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # Infos état
        state_text = f"ETAT: {info['state']}  ACTION: {info.get('action','')}"
        cv2.putText(overlay, state_text, (10, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1)

        return cv2.addWeighted(overlay, 0.85, frame_rgb, 0.15, 0)

    # ------------------------------------------------------------------
    # Dessin de l'UI
    # ------------------------------------------------------------------
    def _draw_ui(self, frame_surf: pygame.Surface, battery: str):
        self.screen.fill((0, 0, 0))
        self.screen.blit(frame_surf, (0, 0))

        # HUD
        hud = self._current_hud()
        if hud:
            self.screen.blit(hud, (15, 90))

        # Boutons du haut
        btn = self.assets.btn
        for name, surf in [
            ("360c",  (20,  10)),
            ("8",     (170, 10)),
            ("360i",  (320, 10)),
            ("360e",  (470, 10)),
            ("updown",(620, 10)),
            ("goback",(770, 10)),
        ]:
            if btn.get(name):
                self.screen.blit(btn[name], surf)

        # Boutons du bas
        for name, surf in [
            ("takeoff",  (20,  620)),
            ("land",     (140, 620)),
            ("predator", (260, 620)),
            ("thermal",  (380, 620)),
            ("contour",  (500, 620)),
            ("invert",   (620, 620)),
            ("photo",    (760, 610)),
            ("record",   (850, 622)),
        ]:
            if btn.get(name):
                self.screen.blit(btn[name], surf)

        # Batterie
        bat_surf = self.font.render(battery, 1, (255, 255, 255))
        self.screen.blit(bat_surf, (900, 90))

        # Indicateurs mode
        mode_text = f"MODE: {self.display_mode.name}"
        mode_surf = self.font.render(mode_text, 1, (0, 220, 255))
        self.screen.blit(mode_surf, (10, SCREEN_H - 20))

        # Indicateur enregistrement
        if self.recording:
            rec_surf = self.font_big.render("● REC", 1, (255, 30, 30))
            self.screen.blit(rec_surf, (SCREEN_W - 90, 10))

        # Indicateur mode autonome
        if self.display_mode == DisplayMode.AUTONOMOUS and self.navigator:
            state = self.navigator.debug_info["state"]
            auto_surf = self.font_big.render(f"IA: {state}", 1, (255, 220, 0))
            self.screen.blit(auto_surf, (10, 10))

    # ------------------------------------------------------------------
    # Gestion clavier
    # ------------------------------------------------------------------
    def _on_keydown(self, key, frame_read) -> bool:
        """Retourne True si on doit quitter."""

        # --- Quitter ---
        if key == K_ESCAPE:
            return True

        # --- Décollage / atterrissage ---
        elif key == K_t:
            self.tello.takeoff()
            self.send_rc = True
        elif key == K_l:
            self.tello.land()
            self.send_rc = False

        # --- Flips ---
        elif key == K_KP8:
            self.tello.flip("f")
            self.send_rc = True
        elif key == K_KP2:
            self.tello.flip("b")
            self.send_rc = True
        elif key == K_KP4:
            self.tello.flip("l")
            self.send_rc = True
        elif key == K_KP6:
            self.tello.flip("r")
            self.send_rc = True

        # --- Modes d'affichage ---
        elif key == K_p:
            self.display_mode = DisplayMode.PREDATOR
        elif key == K_i:
            self.display_mode = DisplayMode.INVERT
        elif key == K_o:
            self.display_mode = DisplayMode.CONTOUR
        elif key == K_g:
            self.display_mode = DisplayMode.GRAYSCALE
        elif key == K_F1:
            self.display_mode = DisplayMode.NORMAL

        # --- Navigation autonome ---
        elif key == K_n:
            self._toggle_autonomous()

        # --- Retour (mode autonome) ---
        elif key == K_r:
            if self.navigator and self.navigator.is_running:
                self.navigator.request_return()

        # --- Cycle HUD ---
        elif key == K_c:
            self._cycle_hud(1)
        elif key == K_x:
            self._cycle_hud(-1)

        # --- Changement de thème ---
        elif key == K_h:
            themes = list(InterfaceTheme)
            idx = themes.index(self.theme)
            self.theme = themes[(idx + 1) % len(themes)]
            self.hud_index = 0

        # --- Photo ---
        elif key == K_F5:
            self._take_photo(frame_read)

        # --- Enregistrement ---
        elif key == K_F6:
            self.recording = not self.recording

        # --- Vélocités ---
        elif key == K_UP:
            self.fb_vel = SPEED
        elif key == K_DOWN:
            self.fb_vel = -SPEED
        elif key == K_LEFT:
            self.lr_vel = -SPEED
        elif key == K_RIGHT:
            self.lr_vel = SPEED
        elif key == K_w:
            self.ud_vel = SPEED
        elif key == K_s:
            self.ud_vel = -SPEED
        elif key == K_a:
            self.yaw_vel = -SPEED
        elif key == K_d:
            self.yaw_vel = SPEED

        return False

    def _on_keyup(self, key):
        if key in (K_UP, K_DOWN):
            self.fb_vel = 0
        elif key in (K_LEFT, K_RIGHT):
            self.lr_vel = 0
        elif key in (K_w, K_s):
            self.ud_vel = 0
        elif key in (K_a, K_d):
            self.yaw_vel = 0

    # ------------------------------------------------------------------
    # Navigation autonome
    # ------------------------------------------------------------------
    def _toggle_autonomous(self):
        if self.navigator and self.navigator.is_running:
            self.navigator.stop()
            self.display_mode = DisplayMode.NORMAL
            self.send_rc = False
        else:
            self.navigator = AutonomousNavigator(self.tello)
            self.display_mode = DisplayMode.AUTONOMOUS
            self.navigator.start()
            self.send_rc = False  # la nav gère elle-même le RC

    # ------------------------------------------------------------------
    # Gestion souris (zones cliquables)
    # ------------------------------------------------------------------
    def _on_click(self, pos, frame_read):
        for name, zone in CLICK_ZONES.items():
            if zone.collidepoint(pos):
                self._handle_click_zone(name, frame_read)
                break

    def _handle_click_zone(self, name: str, frame_read):
        if name == "takeoff":
            self.tello.takeoff()
            self.send_rc = True
        elif name == "land":
            self.tello.land()
            self.send_rc = False
        elif name == "predator":
            self.display_mode = DisplayMode.PREDATOR
        elif name == "thermal":
            self.display_mode = DisplayMode.THERMAL
        elif name == "invert":
            self.display_mode = DisplayMode.INVERT
        elif name == "contour":
            self.display_mode = DisplayMode.CONTOUR
        elif name == "photo":
            self._take_photo(frame_read)
        elif name == "record":
            self.recording = not self.recording
        elif name == "360c":
            self._run_mission_360c()
        elif name == "360i":
            self._run_mission_360i()
        elif name == "360e":
            self._run_mission_360e()
        elif name == "updown":
            self._run_mission_updown()
        elif name == "goback":
            self._run_mission_goback()
        elif name == "8":
            self._run_mission_8()

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    def _take_photo(self, frame_read):
        self.photo_count += 1
        raw = frame_read.frame
        path = os.path.join(PICTURE_DIR, f"photo_{self.photo_count:04d}.jpg")
        cv2.imwrite(path, raw)
        print(f"[PHOTO] Sauvegardée : {path}")

    def _record_frame(self, frame_read):
        raw = frame_read.frame
        path = os.path.join(RECORD_DIR, f"frame_{self.record_count:06d}.jpg")
        cv2.imwrite(path, raw)
        self.record_count += 1

    def _update_rc(self):
        if self.send_rc and not (self.navigator and self.navigator.is_running):
            self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)

    # ------------------------------------------------------------------
    # Missions préprogrammées (exécutées en thread pour ne pas bloquer)
    # ------------------------------------------------------------------
    def _run_mission(self, steps: list[tuple]):
        """steps = liste de (commande_rc_tuple, durée_secondes)"""
        def _worker():
            for (lr, fb, ud, yaw), duration in steps:
                self.tello.send_rc_control(lr, fb, ud, yaw)
                time.sleep(duration)
            self.tello.send_rc_control(0, 0, 0, 0)
        threading.Thread(target=_worker, daemon=True).start()

    def _run_mission_360c(self):
        self._run_mission([
            ((0, 20, 0, 0), 2.0),
            ((20, 0, 0, -20), 3.0),
            ((0, -20, 0, 0), 2.0),
            ((0, 0, 0, 0), 0.5),
        ])

    def _run_mission_360i(self):
        self._run_mission([
            ((60, 0, 0, -60), 7.0),
        ])

    def _run_mission_360e(self):
        self._run_mission([
            ((60, 0, 0, 60), 4.0),
        ])

    def _run_mission_updown(self):
        self._run_mission([
            ((0, 0, 60, 0), 3.0),
            ((0, 0, 0, 30), 4.0),
            ((0, 0, -60, 0), 3.0),
        ])

    def _run_mission_goback(self):
        self._run_mission([
            ((0, 60, 0, 0), 5.0),
            ((0, 0, 0, 40), 2.0),
            ((0, 60, 0, 0), 3.0),
            ((0, 0, 0, 0), 0.5),
        ])

    def _run_mission_8(self):
        self._run_mission([
            ((60, 60, 0, 60), 1.0),
            ((-60, 60, 0, -60), 1.0),
            ((-60, 60, 0, -60), 1.0),
            ((-60, 60, 0, -60), 1.0),
            ((-60, 60, 0, -60), 1.0),
            ((60, 60, 0, 60), 1.0),
            ((60, 60, 0, 60), 1.0),
            ((60, 60, 0, 60), 1.0),
            ((0, 0, 0, 0), 0.5),
        ])

    # ------------------------------------------------------------------
    # Gestion joystick
    # ------------------------------------------------------------------
    def _init_joystick(self):
        count = pygame.joystick.get_count()
        if count > 0:
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()
            print(f"[JOYSTICK] Détecté : {self.joystick.get_name()} ({self.joystick.get_numbuttons()} boutons)")
            self.joystick_active = True
        else:
            print("[JOYSTICK] Aucun joystick détecté.")

    def _handle_joystick_event(self, event, speed: int = 60):
        if not self.joystick_active:
            return

        if event.type == JOYAXISMOTION:
            if event.axis == 1:  # stick gauche vertical → avant/arrière
                self.fb_vel = int(-event.value * speed) if abs(event.value) > 0.2 else 0
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)
            elif event.axis == 3:  # stick droit horizontal → gauche/droite
                self.lr_vel = int(event.value * speed) if abs(event.value) > 0.2 else 0
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)

        elif event.type == JOYBUTTONDOWN:
            if event.button == 0:     # X → décollage
                self.tello.takeoff(); self.send_rc = True
            elif event.button == 1:   # O → atterrissage
                self.tello.land(); self.send_rc = False
            elif event.button == 2:   # Triangle → stop RC
                self.tello.send_rc_control(0, 0, 0, 0)
            elif event.button == 3:   # Carré → cycle HUD
                self._cycle_hud(1)
            elif event.button == 4:   # L1 → rotation gauche
                self.yaw_vel = -speed
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)
            elif event.button == 5:   # R1 → rotation droite
                self.yaw_vel = speed
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)
            elif event.button == 6:   # L2 → descendre
                self.ud_vel = -speed
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)
            elif event.button == 7:   # R2 → monter
                self.ud_vel = speed
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)

        elif event.type == JOYBUTTONUP:
            if event.button in (4, 5):
                self.yaw_vel = 0
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)
            elif event.button in (6, 7):
                self.ud_vel = 0
                self.tello.send_rc_control(self.lr_vel, self.fb_vel, self.ud_vel, self.yaw_vel)

        elif event.type == JOYHATMOTION:
            directions = {
                (0, 1):  "f",
                (0, -1): "b",
                (-1, 0): "l",
                (1, 0):  "r",
            }
            if event.value in directions:
                self.tello.flip(directions[event.value])
                self.send_rc = True

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------
    def run(self):
        # Connexion drone
        if not self.tello.connect():
            print("[ERREUR] Tello non connecté"); return
        if not self.tello.set_speed(SPEED):
            print("[ERREUR] Impossible de régler la vitesse"); return
        if not self.tello.streamoff():
            print("[ERREUR] Impossible d'arrêter le stream"); return
        if not self.tello.streamon():
            print("[ERREUR] Impossible de démarrer le stream"); return

        frame_read = self.tello.get_frame_read()
        battery = self.tello.get_battery().replace("\r\n", "%")

        print("[INFO] Connecté. Batterie :", battery)
        print("[INFO] Appuyez sur N pour activer la navigation autonome IA")
        print("[INFO] Appuyez sur R pour demander le retour (mode IA)")

        should_stop = False

        while not should_stop:
            # --- Événements ---
            for event in pygame.event.get():
                if event.type == QUIT:
                    should_stop = True

                elif event.type == USEREVENT + 1:
                    self._update_rc()

                elif event.type == KEYDOWN:
                    should_stop = self._on_keydown(event.key, frame_read)

                elif event.type == KEYUP:
                    self._on_keyup(event.key)

                elif event.type == MOUSEBUTTONUP and event.button == 1:
                    self._on_click(event.pos, frame_read)

                elif event.type in (JOYAXISMOTION, JOYBUTTONDOWN, JOYBUTTONUP, JOYHATMOTION):
                    self._handle_joystick_event(event)

            if frame_read.stopped:
                break

            # --- Récupération du frame ---
            raw = frame_read.frame
            if raw is None:
                continue

            # --- Mise à jour navigator ---
            if self.navigator and self.navigator.is_running:
                self.navigator.update_frame(raw)

            # --- Enregistrement ---
            if self.recording:
                self._record_frame(frame_read)

            # --- Affichage ---
            frame_surf = self._process_frame(raw)
            self._draw_ui(frame_surf, battery)
            pygame.display.update()
            self.clock.tick(FPS)

        # Nettoyage
        if self.navigator and self.navigator.is_running:
            self.navigator.stop()
        self.tello.end()
        pygame.quit()


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def main():
    app = TelloApp()
    app.run()


if __name__ == "__main__":
    main()
