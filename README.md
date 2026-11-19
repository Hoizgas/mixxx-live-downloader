# Mixxx Live Downloader

Lokale macOS-App mit Admin-Anmeldung, Download-Warteschlange und optionalem LAN-Zugriff.

## Funktionen

- Das native Kontrollpanel startet und stoppt den Webserver.
- Beim ersten Start wird ein Admin-Konto angelegt; ab dann erfordert das Kontrollpanel eine Anmeldung.
- Das Webpanel verlangt dieselben Admin-Zugangsdaten. Passwörter werden als scrypt-Hash in `~/Library/Application Support/MixxxLiveDownloader/config.json` gespeichert.
- Beim Start prüft das Kontrollpanel `yt-dlp`, `ffmpeg` und `ffprobe`. Fehlen Tools, fragt die App, ob Homebrew die fehlenden Formeln samt benötigter Abhängigkeiten installieren soll. Die Installation läuft im Hintergrund; das Fenster bleibt bedienbar.
- Ohne LAN-Schalter lauscht der Server nur auf `127.0.0.1`.
- Mit LAN-Schalter lauscht er auf der aktiven LAN-IP und nutzt HTTPS mit einem lokal erzeugten, selbstsignierten Zertifikat. Auf anderen Geräten muss das Zertifikat ggf. als vertrauenswürdig installiert werden.
- Die eingebaute macOS-Firewall verwaltet Freigaben für Apps, nicht einzelne Ports. Die App fordert keine Administratorrechte an und ändert keine systemweiten Firewallregeln. Wenn der LAN-Schalter aus ist, wird der LAN-Listener beendet und der Dienst ist nur lokal gebunden; eine zuvor erteilte allgemeine App-Freigabe kann in den Firewall-Einstellungen bestehen bleiben.
- YouTube-Links werden nur nach Verantwortung des Nutzers verarbeitet. Für Audioextraktion sind `yt-dlp` und `ffmpeg` nötig.

## Abhängigkeiten

```bash
brew install python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`yt-dlp` und `ffmpeg` müssen nicht vorab manuell installiert werden. Beim Serverstart werden sie geprüft und bei Zustimmung über Homebrew ergänzt. Homebrew selbst muss bereits vorhanden sein.

## macOS-App bauen

```bash
zsh ./build_app.sh
```

Das Ergebnis wird unter `outputs/Mixxx Live Downloader.app` abgelegt. Beim ersten Start das Admin-Konto erstellen. macOS kann beim Öffnen einer lokal gebauten, nicht signierten App eine Sicherheitsbestätigung verlangen.

## LAN-Freigabe

Aktiviere im Kontrollpanel **Im lokalen Netzwerk erreichbar (HTTPS)** und starte den Server. Die angezeigte Adresse enthält die LAN-IP und den Port. Ohne Häkchen wird der Server an `127.0.0.1` gebunden. macOS kann beim ersten eingehenden Zugriff eine Firewall-Abfrage anzeigen; erteile Zugriff nur, wenn du den Dienst im lokalen Netz nutzen willst.

Das Zertifikat ist selbstsigniert. Der Browser zeigt deshalb zunächst eine Zertifikatswarnung. Die Zugangsdaten sollten nur in einem vertrauenswürdigen Netzwerk verwendet werden; für Geräte mit hoher Sicherheitsanforderung sollte das Zertifikat dort vor der Anmeldung als vertrauenswürdig installiert werden.
