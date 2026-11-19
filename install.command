#!/bin/bash
set -euo pipefail
export PATH="/opt/local/bin:/opt/local/sbin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

APP_NAME="Mixxx Live Downloader.app"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SOURCE_APP="$SCRIPT_DIR/$APP_NAME"

say() { printf '\n%s\n' "$1"; }
confirm() {
  local answer
  read -r -p "$1 [j/N] " answer
  [[ "$answer" =~ ^([jJyY]|ja|Ja|JA)$ ]]
}
fail() {
  say "Fehler: $1"
  read -r -p "Mit Eingabetaste schließen …" _ || true
  exit 1
}

if [[ "$(uname -m)" != "x86_64" ]]; then
  fail "Dieses Release ist für Intel-Macs (x86_64) gebaut."
fi

macos_major="$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)"
if [[ ! "$macos_major" =~ ^[0-9]+$ ]] || (( macos_major < 15 )); then
  fail "Dieses Paket benötigt macOS 15 oder neuer."
fi

if [[ ! -d "$SOURCE_APP" ]]; then
  fail "Die App liegt nicht neben diesem Installer. Öffne Install.command direkt aus dem bereitgestellten DMG."
fi

BREW="$(command -v brew || true)"
if [[ -z "$BREW" ]]; then
  for candidate in /usr/local/bin/brew /opt/homebrew/bin/brew; do
    if [[ -x "$candidate" ]]; then BREW="$candidate"; break; fi
  done
fi

if [[ -z "$BREW" ]]; then
  say "Homebrew fehlt. Der offizielle Homebrew-Installer wird benötigt, um yt-dlp und ffmpeg einzurichten."
  if ! confirm "Soll der offizielle Homebrew-Installer jetzt ausgeführt werden?"; then
    fail "Homebrew wurde nicht installiert."
  fi
  /bin/bash -c "$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  BREW="$(command -v brew || true)"
  if [[ -z "$BREW" && -x /usr/local/bin/brew ]]; then BREW=/usr/local/bin/brew; fi
  if [[ -z "$BREW" && -x /opt/homebrew/bin/brew ]]; then BREW=/opt/homebrew/bin/brew; fi
fi

if [[ -z "$BREW" || ! -x "$BREW" ]]; then
  fail "Homebrew ist nach der Installation nicht verfügbar."
fi

eval "$("$BREW" shellenv)"
missing=()
if ! command -v yt-dlp >/dev/null 2>&1; then missing+=(yt-dlp); fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then missing+=(ffmpeg); fi

if (( ${#missing[@]} > 0 )); then
  say "Fehlende Pakete: ${missing[*]}"
  if ! confirm "Soll Homebrew diese Pakete samt erforderlichen Abhängigkeiten installieren?"; then
    fail "Die erforderlichen Audio-Tools fehlen."
  fi
  if ! "$BREW" install "${missing[@]}"; then
    say "Homebrew konnte keine passenden fertigen Pakete installieren. Auf Intel-Macs fehlen diese Pakete derzeit häufig (Tier 3)."
    if ! confirm "Soll Homebrew es erneut versuchen und die Pakete aus dem Quellcode bauen? Das kann lange dauern und zusätzliche Entwicklungswerkzeuge benötigen."; then
      fail "Quellcode-Build abgelehnt. Die Audio-Tools sind noch nicht installiert."
    fi
    if ! "$BREW" install --build-from-source "${missing[@]}"; then
      say "Homebrew kann die Intel-Abhängigkeiten nicht bereitstellen. MacPorts bietet passende Ports für macOS Sequoia."
      say "Installiere MacPorts über https://www.macports.org/install.php, öffne danach ein neues Terminal und führe aus:"
      say "sudo port selfupdate && sudo port install yt-dlp ffmpeg"
      fail "Installiere anschließend dieses Paket erneut, damit der Installer die Tools prüft und die App einrichtet."
    fi
  fi
fi

for tool in yt-dlp ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool ist nach der Homebrew-Installation nicht verfügbar."
done

if [[ -w /Applications ]]; then
  DEST_ROOT=/Applications
else
  DEST_ROOT="$HOME/Applications"
fi
DEST_APP="$DEST_ROOT/$APP_NAME"
mkdir -p "$DEST_ROOT"

if [[ -e "$DEST_APP" ]]; then
  if ! confirm "Es gibt bereits eine App unter $DEST_APP. Soll sie ersetzt werden?"; then
    say "Vorhandene App bleibt unverändert."
    open "$DEST_APP"
    exit 0
  fi
  rm -rf "$DEST_APP"
fi

ditto "$SOURCE_APP" "$DEST_APP"
say "Installiert: $DEST_APP"
say "yt-dlp, ffmpeg und ffprobe sind verfügbar."
say "Beim ersten Öffnen Admin-Nutzer und Passwort in der App festlegen."
open "$DEST_APP"
