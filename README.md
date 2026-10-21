# SeaParser: separat körunderlag 2026-10-13

Inget utvecklingspass startat och inget schemalagt. Projektets arbetsfiler har inte ändrats av förberedelsen.
Användare: seaparser. Aktuell arbetskatalog: /tmp/seaparser-real-map-data. Historiska provloggar kan avse app/.
Förberedelsens sessions-ID: 01a1338f-97d4-7b61-aa78-5fdff31d1f89.
Målsession för prognosarbetet: ännu inte säkert identifierad; välj dess exakta UUID i terminalen och kontrollera cwd och historik. Använd aldrig --last. Körunderlaget vägrar work utan UUID och en matchande handoff.session.
Sluttid anges obligatoriskt i ISO-8601 med tidszon. Exemplet nedan använder två timmar från manuell start. timeout stoppar efter sluttiden med högst 30 sekunders TERM-grace; timeout ger 124, upptaget lås 75, saknad överlämning 78.

## Exakta kommandon i vanlig Linux-terminal, utan sudo

Skrivskyddat prov i beständig tmux-session (inga modell-anrop):

```bash
cd /home/web/seaparser/autonomy
umask 077
END_AT=$(date -u -d '+2 minutes' +%FT%TZ)
tmux -S /home/web/seaparser/autonomy/tmux.sock new-session -d -s seaparser-autonomy-probe -c /home/web/seaparser/autonomy "/home/web/seaparser/autonomy/run.sh probe '$END_AT' ssh-probe"
tmux -S /home/web/seaparser/autonomy/tmux.sock has-session -t seaparser-autonomy-probe
ls -lt /home/web/seaparser/autonomy/logs
exit
```

Koppla tillbaka med SSH efter minst 25 sekunder:

```bash
cd /home/web/seaparser/autonomy
cat logs/*log
cat logs/*exit
tmux -S /home/web/seaparser/autonomy/tmux.sock has-session -t seaparser-autonomy-probe
```

Godkänt: en ssh-probe-logg har START före frånkoppling, END efter frånkoppling och EXIT_CODE=0; separat .exit innehåller 0. Efter avslut ska tmux-sessionen vara borta. Anteckna faktiskt frånkopplingsdatum så att det går att jämföra. Inloggningsstatus är inte bevis på fungerande modellåtkomst. Kontrollera det separat med följande korta prov:

```bash
cd /home/web/seaparser/autonomy
/home/web/seaparser/.local/bin/codex login status
timeout --signal=TERM --kill-after=5s 60s /home/web/seaparser/.local/bin/codex exec -C /home/web/seaparser/app -s read-only --ephemeral -c 'approval_policy="never"' --json 'Kör endast GIT_OPTIONAL_LOCKS=0 git status --short --branch. Ändra ingenting och läs inga hemligheter.' </dev/null >logs/terminal-codex-probe.log 2>&1
printf '%s\n' "$?" >logs/terminal-codex-probe.exit
```

Fortsatt arbetspass, endast efter att prognossessionens aktiva körning avslutats och automatiska återstarter stoppats. Ange dess verifierade UUID på frågan. Detta skapar ingen ny utvecklingssession utan återupptar den valda. Om samma session används i Work/VS Code måste den förbli inaktiv under passet.

```bash
cd /home/web/seaparser/autonomy
umask 077
read -r -p 'Verifierat sessions-UUID för avslutat prognospass: ' SESSION_ID
[[ $SESSION_ID =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || exit 64
printf '%s\n' "$SESSION_ID" >handoff.session
END_AT=$(date -u -d '+2 hours' +%FT%TZ)
printf 'SESSION_ID=%s\nEND_AT=%s\nCWD=/home/web/seaparser/app\n' "$SESSION_ID" "$END_AT" >planned-run.txt
tmux -S /home/web/seaparser/autonomy/tmux.sock new-session -d -s seaparser-autonomy-work -c /home/web/seaparser/autonomy "/home/web/seaparser/autonomy/run.sh work '$END_AT' '$SESSION_ID'"
tmux -S /home/web/seaparser/autonomy/tmux.sock has-session -t seaparser-autonomy-work
ls -lt logs
```

Stoppkommando för terminalens work-session:

```bash
tmux -S /home/web/seaparser/autonomy/tmux.sock send-keys -t seaparser-autonomy-work C-c
```

Kontrollera END/exit-loggen och att codex/timeout har avslutats innan något annat utvecklingspass startas. Avsluta sedan eventuell kvarvarande tmux-session med `tmux -S /home/web/seaparser/autonomy/tmux.sock kill-session -t seaparser-autonomy-work`. Provets motsvarande namn är seaparser-autonomy-probe.

## Schemaläggning (alternativ till tmux; inte installerad här)

Kontrollera först `crontab -l`, `systemctl --user is-system-running` och `loginctl show-user seaparser -p Linger` i vanlig terminal. Ändra inte root-inställningar. tmux -S /home/web/seaparser/autonomy/tmux.sock är här förstahandsalternativet för ett begränsat arbetspass; ingen cron krävs.
Om användar-systemd redan fungerar och överlever utloggning kan ett överlämnat pass bokas en minut framåt med de SESSION_ID/END_AT som fastställts ovan:

```bash
systemd-run --user --unit=seaparser-autonomy-work --on-active=1m --timer-property=AccuracySec=1s --property=WorkingDirectory=/home/web/seaparser/app --property=KillMode=control-group /home/web/seaparser/autonomy/run.sh work "$END_AT" "$SESSION_ID"
systemctl --user list-timers seaparser-autonomy-work.timer
systemctl --user status seaparser-autonomy-work.timer
```

Verifiera timer, faktisk START, END och exit-logg samt återanslutning efter SSH innan något beskrivs som fungerande schemaläggning. Utan redan fungerande utloggningsbeständig användarmanager väljs tmux; aktivera inte linger med root.
Stopp för systemd-alternativet:

```bash
systemctl --user stop seaparser-autonomy-work.timer seaparser-autonomy-work.service
```

## Lås, säkerhet och loggning

run.lock hålls med flock under hela passet och kan inte lämna ett stale aktivt lås efter processavslut. Alla starter via run.sh delar samma lås. En redan körande Work/VS Code-session tar inte detta lås: därför krävs faktisk avslutad prognoskörning och explicit överlämning, inte bara en låsfil. Ingen garant om externa oberoende utvecklingsstarter ges.
Loggar ligger i /home/web/seaparser/autonomy/logs och nya loggar får privata rättigheter via umask 077. JSONL från Codex och start/avslut/exitkod sparas. Loggar ska aldrig inkludera hemligheter; instruktionerna förbjuder läsning/utskrift av dem. .codex sessionslagring används för work; inga authfiler kopieras. Ingen modell byts och inga köp görs.

## Verifierat i den begränsade körmiljön

Codex CLI 0.162.1; login status: Logged in using ChatGPT. sessionsmetadata finns beständigt men katalogen är skrivskyddad i denna körmiljö. Resume med explicit UUID stöds enligt installerad help.
Installerat: systemd-run/systemctl, crontab, tmux, screen, flock, timeout, nohup, setsid. at saknas.
Systemd: DBUS_SESSION_BUS_ADDRESS/XDG_RUNTIME_DIR saknas och /run/user/1000 saknas. systemd-run --user-provet blockerades före start.
Crontab: Permission denied. tmux: Operation not permitted. screen: /run/screen skrivskyddat.
Offline statusprov: START 2026-10-13T04:41:44Z, END 04:42:04Z, exit 0; branch development/geographic-api-map, befintlig ändring docs/progress.md.
Codex read-only --ephemeral-prov: exit 1, failed to initialize in-process app-server client: Read-only file system. Inget modellarbete startade.
setsid --fork-försök: ingen startlogg efter verktygsavslut; beständighet inte verifierad. Verklig SSH-frånkoppling kan inte utföras från den här körmiljön.
Ett mellanliggande låsprov avvisade andra starten med 75 men första körningen gav 127 eftersom körskriptet uppdaterades under provet. Körskript får inte uppdateras under aktiva pass. Se lock-verification-final.log för slutligt prov av den färdiga versionen. Arbetsfilerna i app har inte ändrats av dessa prov.

Officiell dokumentation: https://learn.chatgpt.com/docs/non-interactive-mode (exec, JSONL och explicita resume-sessioner). Ingen scheduler/timer installerad och ingen andra utvecklingskörning startad.

## Faktisk status efter ny kontroll 2026-10-13

Ingen tmux-server/socket skapades av förberedelsen. /tmp/tmux-1000 är tom i den tillgängliga vyn. Alla lyckade prov hittills har körts som korta foreground-processer, inte som beständiga tmux-sessioner. Felet No such file or directory i SSH-terminalen betyder att default-socket saknas där; tidigare stoppkommando var endast avsett för en framtida startad session.
Ny foreground-provlogg: /home/web/seaparser/autonomy/logs/20261013T044945Z-2.log. START=2026-10-13T04:49:45Z, END=2026-10-13T04:50:05Z, EXIT_CODE=0. SESSION_ID=readonly-verification är provets etikett, inte en Codex-modellsession. Codex-förberedelsens UUID är 01a1338f-97d4-7b61-aa78-5fdff31d1f89.
Framtida explicit tmux-socket: /home/web/seaparser/autonomy/tmux.sock; den är ännu inte skapad. Kör först endast probe-kommandot ovan och verifiera SSH-frånkoppling. Aktivera inte längre work-pass innan detta har lyckats och prognosarbetet överlämnats.

Förenklat exakt startkommando för enbart SSH-prov: `bash /home/web/seaparser/autonomy/start-probe.sh`. Scriptet använder explicit tmux.sock och startar aldrig Codex eller utvecklingsarbete. Stopp: `tmux -S /home/web/seaparser/autonomy/tmux.sock send-keys -t seaparser-autonomy-probe C-c`. Kontrollera ssh-probe-loggens START/END/EXIT_CODE efter faktisk SSH-frånkoppling.

## Copernicus-underlag efter separat publicerad commit

Granskad snapshot från d0bd906: /home/web/seaparser/autonomy/copernicus-prepared-d0bd906/. Exakt terminalkommando: `bash /home/web/seaparser/autonomy/copernicus-prepared-d0bd906/scripts/download-copernicus.sh`. Detta är endast bounded datatransport/offlineaudit, inte ett utvecklingspass. Worktree ändras samtidigt av prognosarbete; snapshoten bevarar den granskade koden. Commit-/CI-kvitto: /home/web/seaparser/autonomy/logs/copernicus-publication.json.

Slutkvitto: commit d0bd906d56825f843d89d286bc0ec889d0fe5a07 är verifierat publicerad på development/real-map-data. CI 38349844052 completed/success för exakt commit; kvitto autonomy/logs/copernicus-ci-success.json. Nytt explicit tmux-startförsök 2026-10-13 05:09:53 UTC blockerades med Operation not permitted, exit 1; tmux.sock skapades inte. Logg autonomy/logs/tmux-start-probe-attempt.log. Inget längre arbetspass är aktiverat.

## Gemensam aktuell status efter filverifiering

Se /home/web/seaparser/autonomy/CURRENT-STATUS.md och current-status.json. Befintliga Copernicus-filer har lokaliserats och verifierats mot manifest/kopior. Återanvänd /tmp/seaparser-real-map-data/fixtures/vinga-copernicus/; kör inte det äldre nedladdningsscriptet igen för samma uttag. Befintlig lokal adapter återanvänder d0bd906-hjälpfunktionerna. Lokala prognosändringar är bevarade, inga nya adaptrar eller nedladdningar skapade.

Senaste verifierade överlämning: /home/web/seaparser/autonomy/HANDOFF.md. Aktuell status: CURRENT-STATUS.md. Slutcommit bd5600c, CI success. run.sh använder nu prognosworktreet. Inget är aktiverat eller schemalagt.

Aktuellt femtimmarspass: FIVE-HOUR-PASS.md. SSH-prov verifierat. Pass förberett,
inte startat. Start-/stoppkommandon och loggar i detta dokument ersätter äldre
arbetsstarts-exempel. Inget är schemalagt.

## Låsdesign

Tre `flock`-baserade lås används, alla kraschsäkra:

- `run.lock` – delas av `run.sh`, `work_session.py` (worker) och
  `ssh_probe.py` (worker). Bara en worker åt gången.
- `ssh-probe-launch.lock` – separat lås för `ssh_probe.py` launch-fas.
- `work-launch.lock` – separat lås för `work_session.py` launch-fas.

Låsfilerna är tomma och skapas automatiskt av `open('a')`. De får inte tas bort
manuellt medan en process kör – `flock` släpper dem automatiskt vid processdöd.

## Editing autonomy itself with the autonomous runner

The SeaParser runner (`~/app/scripts/runner.py`) supports `--root autonomy`.
It creates a worktree of this repo and lets DeepSeek edit the scripts.

Prepare a task in `~/autonomy/AUTONOMOUS_TASK.md` and run:

    cd ~/app && source .venv/bin/activate
    .venv/bin/python -B -m scripts.runner --root autonomy --fast

Verify and merge with:

    .venv/bin/python -B -m scripts.verify_run --root autonomy <date>_<n> --fast

## Known issue fixed 2026-10-21: verify_cmd masked test failures

Between run 12 (D2) and run 14 (D4) the autonomy verify_cmd piped
unittest through tail without pipefail, so a failing test suite was
reported as passing. D2 and D3 were merged while
test_capacity_retry.WorkerIntegration was broken (7 failures).

Root cause of the test failures: worker() used SSH_HEAD/HEAD without
calling resolve_pins(), and the UnresolvedPin sentinels raise on any
use. The checkpoint() helper inside worker() hit this first.

Both are fixed. The verify_cmd now has 'set -o pipefail', and
worker() calls resolve_pins() at the top before any checkpoint.

Lesson: a verification step that itself can fail silently is worse
than no verification. Always check the exit code of pipelines.
