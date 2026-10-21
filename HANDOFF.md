# Aktuell Trelleborg-leverans och API-förberedelse 2026-10-19T20:45:05.481325+00:00

Worktree /home/web/seaparser/worktrees/real-map-data, development/real-map-data.
HEAD/remote a880ccf2db99c36613482024fdb74d70526a3b56; fem tidigare ostagade
deployment-sökvägsändringar är bevarade. Ingen utvecklingskörning startad här.
Statisk sparad Trelleborgfärd är publicerad på
https://seaparser.com/poc/?example=trelleborg-stromstad .
Exakt CI success: https://github.com/axiertech/seaparser/actions/runs/39318986105

Nya API-releasen är uppackad i /home/web/seaparser/releases/poc-a880ccf2db99c36613482024fdb74d70526a3b56;636 filer verifierade mot
arkiv och Git. Root skapade katalogen; inga nya katalogrättigheter behövs.
Verifierad lokalt från denna release: health, Vinga-scen, exakt Trelleborgresultat
och identisk GPX med ändpunkter. Två rollback-/externändringsprov gröna.
FÖRBERETT, INTE AKTIVERAT: kör som root i vanlig SSH:
/bin/bash /home/web/seaparser/backups/trelleborg-2ee29f7/api-ready/activate.sh
Endast befintlig PoC-unit WorkingDirectory ändras. Runtime/proxy/gränser/
databas/statisk PoC oförändrade. Verifiering och rollback ingår i kommandot.
Logg efter start /home/web/seaparser/backups/trelleborg-2ee29f7/api-ready/activation.log; kvitto activation-success.json
först efter faktisk lokal/publik verifiering. Se /home/web/seaparser/backups/trelleborg-2ee29f7/api-ready/ADMIN.md.
Inga nya datauttag, commits, modellkörningar eller autonomipass startade här.
Sparad Trelleborgfärd är en kandidat/en avgång; fria Trelleborgpunkter ej aktiva.
Offshorefallen kvarstår. Tidigare överlämning finns i paketets *.before-api.
