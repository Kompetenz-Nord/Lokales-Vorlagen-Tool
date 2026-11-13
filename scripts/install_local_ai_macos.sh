#!/usr/bin/env bash
# Lokale KI (Ollama) für das Lokale Vorlagen-Tool unter macOS einrichten.
#
#   ./scripts/install_local_ai_macos.sh                      # Rechner prüfen, Profil vorschlagen, Schritte erfragen
#   ./scripts/install_local_ai_macos.sh standard             # Profil ausdrücklich wählen
#   ./scripts/install_local_ai_macos.sh standard --install-ollama   # fehlendes Ollama per Homebrew (nach Rückfrage)
#   ./scripts/install_local_ai_macos.sh --dry-run            # nur prüfen und anzeigen, nichts ändern
#
# Profile (Quelle: src/insurance_app/local_ai/profiles.py):
#   kompakt  = glm-ocr
#   standard = glm-ocr + qwen3-vl:4b          (empfohlen)
#   maximal  = glm-ocr + qwen3-vl:4b + gemma4:e4b
#
# Grundsätze:
#   * Nichts geschieht ohne Rückfrage (j/N). Standardantwort ist immer „Nein“.
#   * Nur lokale Modelle. Keine Anmeldung bei Ollama, kein API-Schlüssel, keine Cloud-Modelle.
#   * Das Skript spricht Ollama nur über 127.0.0.1 an. Downloads führt Ollama selbst aus.
#   * Mehrfach ausführbar: Vorhandenes (Ollama, Modelle, server.json) bleibt unverändert.
#
# Rückgabewert: 0 = in Ordnung (auch: bewusst übersprungen), 1 = Fehler, 2 = falscher Aufruf.
set -euo pipefail

OLLAMA_URL="http://127.0.0.1:11434"
SERVER_JSON="$HOME/.ollama/server.json"
SERVER_LOG="$HOME/.ollama/logs/server.log"
MODELS_DIR="${OLLAMA_MODELS:-$HOME/.ollama/models}"
MIN_OLLAMA_VERSION="0.30.0" # laut „ollama show glm-ocr“ bzw. „qwen3-vl:4b“ (Feld „requires“), 13.11.2026

PROFILE=""
INSTALL_OLLAMA=0
DRY_RUN=0
WARNINGS=0
FAILURES=0
SKIPPED=0
PULL_FAILED=" "
LOCAL_ONLY_WRITTEN=0
OLLAMA_BIN=""

usage() {
  cat <<'EOF'
Aufruf: ./scripts/install_local_ai_macos.sh [kompakt|standard|maximal] [--install-ollama] [--dry-run]

  kompakt|standard|maximal  Profil wählen. Ohne Angabe schlägt das Skript ein Profil vor.
  --install-ollama          Fehlt Ollama: nach Rückfrage mit „brew install --cask ollama“ installieren.
  --dry-run                 Testlauf: nur prüfen und anzeigen. Keine Installation, kein Download,
                            keine Datei wird geschrieben, Ollama wird nicht gestartet.
  -h, --help                Diese Hilfe.
EOF
}

# ---------------------------------------------------------------------------
# Ausgabe (Symbol + Text)
# ---------------------------------------------------------------------------
step() { printf '\n== %s ==\n' "$*"; }
ok() { printf '  ✔ %s\n' "$*"; }
info() { printf '  ℹ %s\n' "$*"; }
warn() {
  WARNINGS=$((WARNINGS + 1))
  printf '  ⚠ %s\n' "$*"
}
fail() {
  FAILURES=$((FAILURES + 1))
  printf '  ✖ %s\n' "$*" >&2
}

# Rückfrage j/N. Standard ist „Nein“ (auch bei leerer Eingabe oder ohne Eingabemöglichkeit).
ask_yes_no() {
  local answer=""
  if [ "$DRY_RUN" -eq 1 ]; then
    info "Testlauf – Frage übersprungen: $1"
    return 1
  fi
  printf '  ? %s [j/N] ' "$1"
  if ! read -r answer; then
    answer=""
    printf '\n'
  fi
  case "$answer" in
    j | J | ja | Ja | JA | y | Y | yes | Yes) return 0 ;;
    *) return 1 ;;
  esac
}

# Zahlen: Vergleich und deutsche Schreibweise (Komma)
lt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !((a + 0) < (b + 0)) }'; }
de() { printf '%s' "$1" | tr '.' ','; }

# Versionsvergleich ohne „sort -V“ (bash 3.2 auf macOS). Erfolgreich, wenn $1 < $2.
version_lt() {
  local IFS=.
  local -a a b
  local i x y
  a=($1)
  b=($2)
  for i in 0 1 2; do
    x="${a[i]:-0}"
    y="${b[i]:-0}"
    x="${x%%[!0-9]*}"
    y="${y%%[!0-9]*}"
    x="${x:-0}"
    y="${y:-0}"
    if ((10#$x < 10#$y)); then return 0; fi
    if ((10#$x > 10#$y)); then return 1; fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Profile und Modelle (Richtwerte aus src/insurance_app/local_ai/profiles.py und status.py)
# ---------------------------------------------------------------------------
profile_models() {
  case "$1" in
    kompakt) echo "glm-ocr" ;;
    standard) echo "glm-ocr qwen3-vl:4b" ;;
    maximal) echo "glm-ocr qwen3-vl:4b gemma4:e4b" ;;
  esac
}
profile_min_ram() { case "$1" in kompakt) echo 8 ;; standard) echo 16 ;; maximal) echo 16 ;; esac }
profile_rec_ram() { case "$1" in kompakt) echo 16 ;; standard) echo 16 ;; maximal) echo 32 ;; esac }
profile_disk() { case "$1" in kompakt) echo 4 ;; standard) echo 8 ;; maximal) echo 16 ;; esac }
profile_mac_text() {
  case "$1" in
    kompakt) echo "Apple Silicon (M1 oder neuer) mit mindestens 8 GB gemeinsamem Arbeitsspeicher" ;;
    standard) echo "Apple Silicon (M1 oder neuer) mit 16 GB gemeinsamem Arbeitsspeicher" ;;
    maximal) echo "Apple Silicon mit 32 GB gemeinsamem Arbeitsspeicher (z. B. M-Pro/Max); 16 GB möglich, aber langsamer" ;;
  esac
}
profile_lower() { case "$1" in maximal) echo standard ;; *) echo kompakt ;; esac }

# Downloadgröße laut Ollama-Registry-Manifest (abgerufen am 13.11.2026), 1 GB = 10^9 Byte
model_size() {
  case "$1" in
    glm-ocr) echo "2,66 GB" ;;
    qwen3-vl:4b) echo "3,34 GB" ;;
    gemma4:e4b) echo "6,58 GB" ;;
    *) echo "unbekannt" ;;
  esac
}
model_role() {
  case "$1" in
    glm-ocr) echo "Aktives Modell: liest schwierige Scans, Fotos und Tabellen als Text" ;;
    qwen3-vl:4b) echo "Ausweichmodell: liest Felder direkt aus dem Seitenbild (mit wörtlichem Zitat)" ;;
    gemma4:e4b) echo "Gegenprüfung: unabhängige zweite Meinung, Abweichung = Konflikt" ;;
  esac
}
model_license() {
  case "$1" in
    glm-ocr) echo "keine Lizenzangabe im Ollama-Manifest – beim Anbieter Z.ai prüfen" ;;
    qwen3-vl:4b | gemma4:e4b) echo "Apache 2.0 (laut Ollama-Manifest)" ;;
  esac
}

# ---------------------------------------------------------------------------
# Argumente
# ---------------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    kompakt | standard | maximal) PROFILE="$arg" ;;
    --install-ollama) INSTALL_OLLAMA=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unbekannte Angabe: %s\n\n' "$arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

IS_MAC=0
if [ "$(uname -s)" = "Darwin" ]; then IS_MAC=1; fi

printf 'Lokale KI für das Lokale Vorlagen-Tool einrichten (macOS)\n'
printf 'Alles bleibt auf diesem Rechner. Keine Anmeldung, kein API-Schlüssel, keine Cloud-Modelle.\n'
if [ "$DRY_RUN" -eq 1 ]; then
  printf 'TESTLAUF (--dry-run): Es wird nichts installiert, heruntergeladen, gestartet oder geschrieben.\n'
fi
if [ "$IS_MAC" -eq 0 ]; then
  if [ "$DRY_RUN" -eq 0 ]; then
    printf '✖ Dieses Skript ist für macOS gedacht. Windows: scripts\\install_local_ai_windows.ps1.\n' >&2
    printf '  Für einen Testlauf ohne Änderungen: --dry-run\n' >&2
    exit 2
  fi
  printf '⚠ Kein macOS erkannt – Testlauf mit Ersatzwerten (Linux: /proc/meminfo, /proc/cpuinfo).\n'
fi

# ---------------------------------------------------------------------------
# 1. Rechner prüfen
# ---------------------------------------------------------------------------
step "1. Rechner prüfen"

ram_bytes=""
cpu="unbekannt"
apple_silicon=0
if [ "$IS_MAC" -eq 1 ]; then
  ram_bytes="$(sysctl -n hw.memsize 2>/dev/null || true)"
  cpu="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unbekannt)"
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then apple_silicon=1; fi
  macos_version="$(sw_vers -productVersion 2>/dev/null || echo "")"
  info "System: macOS ${macos_version:-unbekannt} ($(uname -m))"
  if [ -n "$macos_version" ] && [ "${macos_version%%.*}" -lt 14 ] 2>/dev/null; then
    warn "Ollama setzt laut Dokumentation macOS 14 (Sonoma) oder neuer voraus."
  fi
else
  if [ -r /proc/meminfo ]; then
    kb="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"
    ram_bytes="$((kb * 1024))"
  fi
  cpu="$(awk -F': ' '/^model name/ { print $2; exit }' /proc/cpuinfo 2>/dev/null || echo unbekannt)"
  info "System: $(uname -s) $(uname -m) (kein macOS – nur Testlauf)"
fi

# Arbeitsspeicher in GB (1024^3, wie die App), zum Vergleich auf ganze GB gerundet
RAM_GB="$(awk -v b="$ram_bytes" 'BEGIN { if (b == "" || b + 0 <= 0) print ""; else printf "%.1f", b / 1073741824 }')"
RAM_CMP="$(awk -v r="$RAM_GB" 'BEGIN { if (r == "") print ""; else printf "%d", r + 0.5 }')"

# Freier Speicherplatz am Modellordner (nächster vorhandener Elternordner)
probe="$MODELS_DIR"
while [ ! -e "$probe" ] && [ "$probe" != "/" ] && [ "$probe" != "." ]; do probe="$(dirname "$probe")"; done
free_kb="$(df -Pk "$probe" 2>/dev/null | awk 'NR == 2 { print $4 }' || true)"
DISK_GB="$(awk -v k="$free_kb" 'BEGIN { if (k == "") print ""; else printf "%.1f", k / 1048576 }')"

info "Prozessor: ${cpu:-unbekannt}"
if [ "$IS_MAC" -eq 1 ]; then
  if [ "$apple_silicon" -eq 1 ]; then
    ok "Apple Silicon: Ollama nutzt die Grafikeinheit über Metal."
  else
    warn "Intel-Mac: Ollama rechnet hier laut Dokumentation nur auf dem Prozessor (CPU). Bildmodelle laufen damit deutlich langsamer. Alle Profile setzen auf dem Mac Apple Silicon voraus (Richtwert)."
  fi
else
  info "Grafikkarte: im Testlauf nicht geprüft."
fi
if [ -n "$RAM_GB" ]; then
  info "Arbeitsspeicher: $(de "$RAM_GB") GB"
else
  warn "Arbeitsspeicher konnte nicht ermittelt werden."
fi
if [ -n "$DISK_GB" ]; then
  info "Freier Speicherplatz am Modellordner ($MODELS_DIR): $(de "$DISK_GB") GB"
else
  warn "Freier Speicherplatz konnte nicht ermittelt werden ($MODELS_DIR)."
fi

# Vorschlag wie local_ai.profiles.recommend_profile (RAM auf ganze GB gerundet)
recommend() {
  local key
  if [ -z "$RAM_CMP" ]; then
    echo "standard"
    return
  fi
  if lt "$RAM_CMP" 8; then
    echo ""
    return
  elif lt "$RAM_CMP" 16; then
    key="kompakt"
  elif lt "$RAM_CMP" 32; then
    key="standard"
  else
    key="maximal"
  fi
  if [ -n "$DISK_GB" ]; then
    while [ "$key" != "kompakt" ] && lt "$DISK_GB" "$(profile_disk "$key")"; do key="$(profile_lower "$key")"; done
    if lt "$DISK_GB" "$(profile_disk kompakt)"; then
      echo ""
      return
    fi
  fi
  echo "$key"
}
RECOMMENDED="$(recommend)"
if [ -n "$RECOMMENDED" ]; then
  if [ -z "$RAM_CMP" ]; then
    ok "Vorschlag: $RECOMMENDED (Arbeitsspeicher unbekannt – bitte selbst prüfen)."
  else
    ok "Vorschlag: $RECOMMENDED ($RAM_CMP GB Arbeitsspeicher, $(de "${DISK_GB:-?}") GB frei)."
  fi
else
  warn "Für die lokale KI reicht dieser Rechner voraussichtlich nicht (unter 8 GB Arbeitsspeicher oder unter 4 GB freier Speicherplatz). Die App arbeitet auch ohne KI."
fi

if [ -z "$PROFILE" ]; then
  if [ -z "$RECOMMENDED" ]; then
    info "Wer es trotzdem versuchen will, nennt das Profil ausdrücklich, z. B.: $0 kompakt"
    exit 0
  fi
  PROFILE="$RECOMMENDED"
  info "Kein Profil angegeben – verwende den Vorschlag „$PROFILE“."
fi

MODELS="$(profile_models "$PROFILE")"
step "2. Profil „$PROFILE“ gegen den Rechner prüfen"
info "Modelle: $MODELS"
info "Richtwerte (nicht auf Zielgeräten gemessen): $(profile_mac_text "$PROFILE");"
info "  Arbeitsspeicher mindestens $(profile_min_ram "$PROFILE") GB, empfohlen $(profile_rec_ram "$PROFILE") GB; Speicherplatz $(profile_disk "$PROFILE") GB."
profile_ok=1
if [ -n "$RAM_CMP" ]; then
  if lt "$RAM_CMP" "$(profile_min_ram "$PROFILE")"; then
    warn "Arbeitsspeicher unter dem Mindestwert für „$PROFILE“. Es drohen sehr lange Laufzeiten oder Abbrüche."
    profile_ok=0
  elif lt "$RAM_CMP" "$(profile_rec_ram "$PROFILE")"; then
    warn "Arbeitsspeicher unter der Empfehlung für „$PROFILE“. Es läuft, aber langsamer."
  else
    ok "Arbeitsspeicher passt."
  fi
fi
if [ -n "$DISK_GB" ]; then
  if lt "$DISK_GB" "$(profile_disk "$PROFILE")"; then
    warn "Freier Speicherplatz unter dem Richtwert ($(profile_disk "$PROFILE") GB) für „$PROFILE“."
    profile_ok=0
  else
    ok "Speicherplatz passt."
  fi
fi
if [ "$IS_MAC" -eq 1 ] && [ "$apple_silicon" -eq 0 ]; then profile_ok=0; fi
if [ -n "$RECOMMENDED" ] && [ "$PROFILE" != "$RECOMMENDED" ]; then
  info "Hinweis: Für diesen Rechner wäre „$RECOMMENDED“ der Vorschlag."
fi
if [ "$profile_ok" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  if ! ask_yes_no "Trotz der Warnungen mit „$PROFILE“ fortfahren?"; then
    info "Abgebrochen. Nichts wurde geändert."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# 3. Ollama vorhanden?
# ---------------------------------------------------------------------------
step "3. Ollama prüfen"

find_ollama() {
  local p
  if command -v ollama >/dev/null 2>&1; then
    command -v ollama
    return 0
  fi
  for p in /Applications/Ollama.app/Contents/Resources/ollama \
    "$HOME/Applications/Ollama.app/Contents/Resources/ollama" \
    /opt/homebrew/bin/ollama /usr/local/bin/ollama; do
    if [ -x "$p" ]; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

OLLAMA_BIN="$(find_ollama || true)"
if [ -n "$OLLAMA_BIN" ]; then
  ok "Ollama gefunden: $OLLAMA_BIN"
else
  info "Ollama ist ein kostenloses Programm, das KI-Modelle auf diesem Rechner ausführt."
  if [ "$INSTALL_OLLAMA" -eq 1 ] && command -v brew >/dev/null 2>&1; then
    info "Vorgesehen: brew install --cask ollama (Homebrew lädt die Ollama-App herunter)."
    if ask_yes_no "Ollama jetzt mit Homebrew installieren?"; then
      if brew install --cask ollama; then
        OLLAMA_BIN="$(find_ollama || true)"
      fi
      if [ -z "$OLLAMA_BIN" ]; then
        fail "Installation nicht erfolgreich. Bitte manuell installieren: https://ollama.com/download"
        exit 1
      fi
      ok "Ollama installiert: $OLLAMA_BIN"
    elif [ "$DRY_RUN" -eq 0 ]; then
      info "Ohne Ollama keine lokale KI. Die App funktioniert auch ohne. Nichts wurde geändert."
      exit 0
    fi
  else
    if [ "$INSTALL_OLLAMA" -eq 1 ]; then
      info "Homebrew wurde nicht gefunden (siehe https://brew.sh)."
    fi
    info "So installieren Sie Ollama selbst:"
    info "  1. https://ollama.com/download öffnen und die macOS-Version laden."
    info "  2. Die Datei öffnen und Ollama in den Ordner „Programme“ ziehen."
    info "  3. Ollama einmal starten. Bei der Frage nach einer Anmeldung: nicht nötig für lokale Modelle."
    info "  4. Dieses Skript erneut ausführen."
    if [ "$INSTALL_OLLAMA" -eq 0 ] && command -v brew >/dev/null 2>&1; then
      info "Oder mit Homebrew (nach Rückfrage): $0 $PROFILE --install-ollama"
    fi
    if [ "$DRY_RUN" -eq 0 ]; then
      fail "Ollama fehlt."
      exit 1
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 4. Nur-lokal-Modus von Ollama (Cloud-Funktionen aus)
# ---------------------------------------------------------------------------
step "4. Nur-lokal-Modus von Ollama"
info "Die App lehnt Cloud-Modelle ohnehin ab. Dieser Schalter sperrt die Cloud-Funktionen zusätzlich in Ollama selbst."
LOCAL_ONLY_ACTIVE=0
if [ -f "$SERVER_JSON" ]; then
  if grep -Eq '"disable_ollama_cloud"[[:space:]]*:[[:space:]]*true' "$SERVER_JSON"; then
    ok "Bereits eingeschaltet ($SERVER_JSON)."
    LOCAL_ONLY_ACTIVE=1
  else
    warn "$SERVER_JSON enthält schon andere Einstellungen. Das Skript überschreibt sie nicht."
    info "Bitte selbst ergänzen:  \"disable_ollama_cloud\": true   (danach Ollama neu starten)"
    info "Alternative: Umgebungsvariable OLLAMA_NO_CLOUD=1 für Ollama setzen (launchctl setenv OLLAMA_NO_CLOUD 1)."
  fi
elif ask_yes_no "Nur-lokal-Modus einschalten (schreibt $SERVER_JSON)?"; then
  mkdir -p "$(dirname "$SERVER_JSON")"
  printf '{\n  "disable_ollama_cloud": true\n}\n' >"$SERVER_JSON"
  ok "Geschrieben: $SERVER_JSON"
  LOCAL_ONLY_WRITTEN=1
  LOCAL_ONLY_ACTIVE=1
else
  info "Nicht eingeschaltet. Später möglich: Datei $SERVER_JSON mit {\"disable_ollama_cloud\": true} anlegen."
fi
if [ "${OLLAMA_NO_CLOUD:-}" = "1" ]; then
  info "OLLAMA_NO_CLOUD=1 ist in dieser Shell gesetzt (wirkt nur, wenn Ollama aus dieser Umgebung startet)."
fi

# ---------------------------------------------------------------------------
# 5. Ollama-Dienst erreichbar (nur 127.0.0.1)?
# ---------------------------------------------------------------------------
step "5. Ollama-Dienst auf 127.0.0.1:11434"

server_up() { curl -fs --noproxy '*' --max-time 3 "$OLLAMA_URL/api/version" >/dev/null 2>&1; }
server_version() {
  curl -fs --noproxy '*' --max-time 3 "$OLLAMA_URL/api/version" 2>/dev/null |
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' || true
}
wait_up() {
  local i=0
  while [ "$i" -lt "$1" ]; do
    if server_up; then return 0; fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}
wait_down() {
  local i=0
  while [ "$i" -lt "$1" ]; do
    if ! server_up; then return 0; fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}
start_server() {
  if open -a Ollama >/dev/null 2>&1; then
    info "Ollama-App gestartet – warte bis zu 30 Sekunden ..."
  else
    info "Die Ollama-App wurde nicht gefunden. Bitte in einem zweiten Terminal starten:  ollama serve"
    info "Warte bis zu 30 Sekunden ..."
  fi
  wait_up 30
}

SERVER_RUNNING=0
if server_up; then
  SERVER_RUNNING=1
  ok "Ollama läuft (Version $(server_version))."
  if [ "$LOCAL_ONLY_WRITTEN" -eq 1 ]; then
    info "Damit der Nur-lokal-Modus wirkt, muss Ollama einmal neu starten."
    if ask_yes_no "Ollama jetzt neu starten?"; then
      osascript -e 'quit app "Ollama"' >/dev/null 2>&1 || true
      if wait_down 15; then
        if start_server; then
          ok "Ollama neu gestartet."
        else
          SERVER_RUNNING=0
        fi
      else
        warn "Ollama läuft weiter (vermutlich mit „ollama serve“ oder „brew services“ gestartet). Bitte selbst neu starten."
      fi
    else
      info "Bitte Ollama später einmal beenden (Menüleiste → Quit Ollama) und neu öffnen."
    fi
  fi
elif [ "$DRY_RUN" -eq 1 ]; then
  info "Ollama läuft nicht. (Testlauf: würde jetzt „open -a Ollama“ ausführen und bis zu 30 Sekunden warten.)"
elif [ -n "$OLLAMA_BIN" ]; then
  if start_server; then
    SERVER_RUNNING=1
    ok "Ollama läuft (Version $(server_version))."
  fi
fi

if [ "$SERVER_RUNNING" -eq 1 ]; then
  version="$(server_version)"
  if [ -n "$version" ] && version_lt "$version" "$MIN_OLLAMA_VERSION"; then
    warn "Ollama $version ist älter als $MIN_OLLAMA_VERSION. Die Modelle verlangen laut Modellbeschreibung mindestens $MIN_OLLAMA_VERSION. Bitte Ollama aktualisieren."
  fi
  if [ "$LOCAL_ONLY_ACTIVE" -eq 1 ]; then
    if [ -f "$SERVER_LOG" ] && grep -q "Ollama cloud disabled: true" "$SERVER_LOG"; then
      ok "Protokoll bestätigt: „Ollama cloud disabled: true“."
    else
      info "Im Ollama-Protokoll steht noch nicht „Ollama cloud disabled: true“. Nach dem nächsten Neustart prüfen:"
      info "  grep \"Ollama cloud disabled\" \"$SERVER_LOG\""
    fi
  fi
elif [ "$DRY_RUN" -eq 0 ]; then
  fail "Der Ollama-Dienst ist nicht erreichbar ($OLLAMA_URL). Bitte Ollama starten und das Skript erneut ausführen."
  exit 1
fi

# ---------------------------------------------------------------------------
# 6. Modelle herunterladen (je Modell eine Rückfrage)
# ---------------------------------------------------------------------------
step "6. Modelle für „$PROFILE“"
printf '  %-13s %-9s %s\n' "Modell" "Download" "Aufgabe / Lizenz"
for m in $MODELS; do
  printf '  %-13s %-9s %s\n' "$m" "$(model_size "$m")" "$(model_role "$m")"
  printf '  %-13s %-9s Lizenz: %s\n' "" "" "$(model_license "$m")"
done
info "Größen laut Ollama-Registry (Stand 13.11.2026). Der Download läuft über Ollama aus der Ollama-Bibliothek."
info "Dabei werden keine Dokumente übertragen."

model_present() { [ "$SERVER_RUNNING" -eq 1 ] && [ -n "$OLLAMA_BIN" ] && "$OLLAMA_BIN" show "$1" >/dev/null 2>&1; }

for m in $MODELS; do
  if model_present "$m"; then
    ok "$m ist bereits vorhanden – übersprungen."
    continue
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    info "Testlauf: würde fragen und dann „ollama pull $m“ ausführen ($(model_size "$m"))."
    continue
  fi
  if ask_yes_no "„$m“ jetzt herunterladen ($(model_size "$m"))?"; then
    if "$OLLAMA_BIN" pull "$m"; then
      ok "$m heruntergeladen."
    else
      fail "Download von $m fehlgeschlagen. Internetverbindung prüfen und erneut versuchen (bereits geladene Teile bleiben erhalten)."
      PULL_FAILED="$PULL_FAILED$m "
    fi
  else
    info "$m übersprungen."
    SKIPPED=$((SKIPPED + 1))
  fi
done

# ---------------------------------------------------------------------------
# 7. Kontrolle: ollama list + kurzer Rauchtest (ollama show, ohne Bild)
# ---------------------------------------------------------------------------
step "7. Kontrolle"
if [ "$SERVER_RUNNING" -eq 1 ] && [ -n "$OLLAMA_BIN" ]; then
  "$OLLAMA_BIN" list || warn "„ollama list“ konnte nicht ausgeführt werden."
  for m in $MODELS; do
    if "$OLLAMA_BIN" show "$m" >/dev/null 2>&1; then
      ok "$m: Modellbeschreibung lesbar (ollama show)."
    elif [ "$DRY_RUN" -eq 1 ] || [ "$SKIPPED" -gt 0 ]; then
      info "$m: nicht vorhanden."
    elif case "$PULL_FAILED" in *" $m "*) true ;; *) false ;; esac; then
      info "$m: nicht vorhanden (Download fehlgeschlagen, siehe oben)."
    else
      fail "$m: nicht vorhanden oder nicht lesbar."
    fi
  done
else
  info "Ollama läuft nicht – Kontrolle übersprungen."
fi

step "Ergebnis"
if [ "$DRY_RUN" -eq 1 ]; then
  info "Testlauf beendet. Nichts wurde installiert, heruntergeladen, gestartet oder geschrieben."
fi
if [ "$FAILURES" -gt 0 ]; then
  fail "$FAILURES Fehler, $WARNINGS Warnung(en). Details siehe oben."
  exit 1
fi
ok "Fertig ($WARNINGS Warnung(en), $SKIPPED Modell(e) übersprungen)."
cat <<EOF

Nächste Schritte in der App:
  „Einstellungen & Datenschutz“ → „Lokale KI (optional)“
  → Profil „$PROFILE“ übernehmen (oder unter „Modellauswahl“: „Aktives Modell“, „Ausweichmodell“, „Gegenprüfung“)
  → „Lokale KI aktivieren“ → „Status prüfen“.
Optional im Terminal ausprobieren (Beenden mit /bye):  ollama run qwen3-vl:4b
Anleitung: docs/LOKALE_KI.md
EOF
exit 0
