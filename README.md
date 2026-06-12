# telloDRONE

Outils pour piloter un drone **Ryze Tello** sans dépendre de l'application
officielle (qui plante au lancement sur les Android récents) :

- **`android/`** — *Tello Pilote*, une appli Android maison (Kotlin), compatible
  Android 8 → 15.
- **`*.py`** — scripts Python pour piloter le Tello depuis un PC
  (`main.py`, `manual_control.py`, `final.py`, `tello.py`).

---

## 📱 Appli Android « Tello Pilote »

### Fonctionnalités

- Décollage / atterrissage / **URGENCE** (appui long = coupe les moteurs)
- Deux joysticks virtuels (mode 2) : gauche = gaz + rotation, droit = avancer/reculer + gauche/droite
- Vitesse **Lent / Rapide**
- Flips dans les 4 directions
- **Vidéo en direct** (flux H.264 du drone décodé par MediaCodec)
- Télémétrie : batterie, hauteur, temps de vol
- **Liaison forcée au Wi-Fi du Tello** : pas besoin de couper les données
  mobiles, l'appli route son trafic vers le drone même si Android préfère la 4G/5G
  (c'est la cause n°1 des applis Tello qui « ne se connectent pas » depuis Android 10)

### Récupérer l'APK

L'APK est compilé automatiquement par GitHub Actions à chaque modification :

1. Ouvrez l'onglet **Actions** du dépôt GitHub.
2. Cliquez sur le dernier run vert du workflow **« Build Android APK »**.
3. En bas de la page, section **Artifacts**, téléchargez **TelloPilote-APK**.
4. Dézippez : vous obtenez `TelloPilote.apk`.

(Vous pouvez aussi relancer un build à la main : Actions → Build Android APK →
*Run workflow*.)

### Installer sur le téléphone

1. Copiez `TelloPilote.apk` sur le téléphone (ou téléchargez-le directement
   depuis le navigateur du téléphone en étant connecté à GitHub).
2. Ouvrez le fichier ; Android demande d'autoriser l'installation d'applis
   inconnues pour cette source → acceptez.
3. L'appli s'installe. Aucune autorisation spéciale n'est demandée au premier
   lancement (pas de localisation, pas de caméra).

### Utiliser

1. Allumez le Tello.
2. Sur le téléphone : **Paramètres → Wi-Fi → connectez-vous au réseau
   `TELLO-XXXXXX`** (restez-y même si Android signale « pas d'Internet »).
3. Ouvrez *Tello Pilote* (écran en paysage) et touchez **Connecter**.
4. La vidéo apparaît, la batterie s'affiche en haut → **Décoller**.

⚠️ Sécurité :
- Le bouton **URGENCE** coupe les moteurs instantanément (le drone tombe) —
  c'est pour ça qu'il demande un **appui long**.
- Si l'appli est fermée en plein vol, le Tello atterrit tout seul après 15 s
  sans commande (sécurité intégrée au drone).
- Les flips sont refusés par le drone sous ~50 % de batterie (réponse `error`).

### Compiler soi-même (optionnel)

Ouvrez le dossier `android/` dans Android Studio, ou en ligne de commande :

```bash
cd android
gradle assembleDebug
# APK : app/build/outputs/apk/debug/app-debug.apk
```

### Détails techniques

L'appli parle le même protocole que `tello.py` (SDK officiel Tello) :

| Port UDP | Rôle |
|----------|------|
| 8889 → 192.168.10.1 | commandes texte (`command`, `takeoff`, `rc a b c d`…) |
| 8890 (local) | télémétrie (`bat`, `h`, `time`…) |
| 11111 (local) | flux vidéo H.264 (paquets de 1460 octets, fin de trame = paquet court) |

La consigne `rc` est renvoyée toutes les 50 ms et sert aussi de signal de vie.

---

## 🖥️ Scripts Python

- `main.py` — programme principal (vidéo via FFmpeg basse latence)
- `manual_control.py` — pilotage clavier
- `tello.py` — wrapper du SDK Tello (UDP)
- `final.py` — version consolidée

Lancez-les depuis un PC connecté au Wi-Fi du Tello, par ex. :

```bash
python3 main.py
```
