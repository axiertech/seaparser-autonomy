# Förberett pass efter återhämtning 2026-10-18

Inget nytt långpass har startats under återhämtningen. Tidigare pass är avslutat.
Worktree /home/web/seaparser/worktrees/real-map-data, development/real-map-data.
Pin d9fd35df27ae88c15f30a461eee526cb2dcc24d1. Tidigare fem ofärdiga filer committade.

Start från vanlig SSH-terminal som seaparser (även efter su -):
/bin/bash /home/web/seaparser/autonomy/start-work.sh
Stopp:
/bin/bash /home/web/seaparser/autonomy/stop-work.sh
PuTTY kan stängas manuellt; startaren avslutar inte din ursprungliga SSH-session.

Tmux-session: seaparser-autonomy-work
Socket: /home/web/seaparser/autonomy/tmux.sock
Lås: /home/web/seaparser/autonomy/run.lock
Loggar: /home/web/seaparser/autonomy/logs/work-five-hours/latest/
latest/ avser ännu det gamla avslutade passet och uppdateras först vid ny start.
Ny Codex-session skapas; dess ID sparas i session-id.txt och återupptas sekventiellt.
Sluttiden är fem timmar från faktisk start och flyttas aldrig av väntan.

usage-wait-v2: vid okänd reset-tid, nya försök var 1800:e sekund genom hela
ursprungliga budgeten. Tillförlitlig explicit reset-tid används med 60 s marginal.
Om nästa försök inte ryms väntar arbetaren till budgetslutet och rapporterar det;
15 s reserveras för processavslut och modellfri slutrapport. Ingen fast försöksräknare.
Manuellt stopp avbryter väntan; permanent autentiserings-/miljöfel avslutas separat.
Samma lås, session, modell och ofärdiga filer behålls under all väntan.
Kapacitetsåterförsök oförändrade 60/180/600 s. Inga usage-resets/modellbyten.

22 offlineprov gröna, inklusive falsk CLI-process med upprepade usage-stopp,
framgång, manuellt stopp under andra väntan, tidsgräns, sparat sessions-ID,
modell, lås och oförändrat end_at. Inga riktiga modellkörningar under återhämtningen.
Modellfria delrapporter: usage-checkpoint-*.json. Slut: runner-summary.json och
final-report.md, inklusive explicit stop_reason även vid usage/tidsgräns/manuellt stopp.

URL-logg /home/web/seaparser/autonomy/published-urls.log.
Aktuell CI/överlämning: CURRENT-STATUS.md. Backendens regionala fria kartpunkter
är fortfarande inte interaktivt kvalificerade; PoC visar den sparade verkliga färden.
Permanent flytt från /tmp återstår som separat avslutande flyttpass utan aktiv körning.

Preflight godkänd 2026-10-18T19:09:42.875771+00:00: rätt användare/HEAD/branch, sparad autentisering,
tidigare verkliga prov, usage-wait-v2 och fritt gemensamt lås. REDO FÖR AUTONOM START.
Inget nytt långpass startat.
