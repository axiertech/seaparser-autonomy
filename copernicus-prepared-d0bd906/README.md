Fryst transport-/auditunderlag från granskad commit d0bd906d56825f843d89d286bc0ec889d0fe5a07. Inget startat här.
Exakt kommando som seaparser i vanlig SSH-terminal:
bash /home/web/seaparser/autonomy/copernicus-prepared-d0bd906/scripts/download-copernicus.sh

Använder /tmp/seaparser-real-map-data/.venv/bin/python och tidigare sparade Copernicus-credentials. Inga hemligheter ingår. Endast shellscriptets Python-sökväg har anpassats till worktreets befintliga .venv; övrig kod är identisk med committen.
Logg och data: /home/web/seaparser/autonomy/copernicus/<UTC>-<pid>[.log|.exit|/].
Timeout: 570 sekunder för transport, 20 för audit; totalt under 10 minuter plus högst 10 sekunders TERM-grace. flock hindrar samtidiga nedladdningar.
Stopp för foreground-transport: Ctrl-C. Ingen Codex-session, tmux, schemaläggning, utvecklingskörning eller databasskrivning startas.
Regional vind är separat blockerare. Native geografisk routingadapter, strömmask, modellrun och verkliga marin-filer är inte verifierade i detta underlag.
