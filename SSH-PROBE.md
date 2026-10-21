# Skrivskyddat autonomiprov via SSH och su -

Förberett för commit bd5600c602a8896dbfbcf6b185729ce44483d2e4 i
/tmp/seaparser-real-map-data på development/real-map-data. Användaren arbetar
som seaparser genom su - från sin ursprungliga PuTTY/SSH-inloggning.
Startaren kräver varken bevarade SSH_* variabler eller åtkomst till root-ägda
sshd-processer. Den kontrollerar faktisk användare, worktree, branch, exakt
commit och rent projekt före och efter frånkoppling. Gemensamt run.lock hålls
under provet; samtidig wrapperkörning avvisas med exit 75.

Kör som seaparser i su - shellen, direkt i PuTTY-fönstret:

```bash
bash /home/web/seaparser/autonomy/start-probe.sh
```

Använd inte exec framför kommandot. Startaren avslutas tillbaka till prompten
när den frikopplade arbetaren loggat start. Den stänger eller signalerar inte
ursprunglig SSH-session, login-shell eller su-shell. Stäng PuTTY manuellt inom
60 sekunder efter PROBE_STARTED. Återanslut efter cirka 15 sekunder och använd
su - seaparser igen för att läsa den privata loggen.

Frikoppling: tmux-session seaparser-autonomy-probe, explicit socket
/home/web/seaparser/autonomy/tmux.sock, cwd /tmp/seaparser-real-map-data.
Inget Codex-, utvecklings-, prognos-, nedladdnings- eller databasprogram startas.
Gitstatus läses med GIT_OPTIONAL_LOCKS=0. Endast loggar/lås utanför projektet skrivs.

Arbetaren observerar den ursprungliga terminal som su-shellen ärver via stdin.
När PuTTY/SSH stängs försvinner dess /dev/pts-terminal. Arbetaren väntar då
ytterligare fem sekunder, läser projektstatus igen och loggar slut och exitkod.
Att bara startaren eller su-shellen avslutas räcker inte medan ursprunglig
SSH-terminal fortfarande finns. SSH-sammanhanget kommer från användarens
bekräftade arbetssätt; ingen inspektion av privilegierad sshd krävs.
Kör utanför tmux/screen och använd vanlig su -, utan su --pty, så samma ursprungliga
terminal observeras. Om terminalen inte stängs före kontrollfönstrets slut blir
exitkod 124 och beständighet overifierad. Hela provet har två minuters sluttid.

Logg:
/home/web/seaparser/autonomy/logs/ssh-probe-bd5600c/latest.log
Maskinläsbart kvitto:
/home/web/seaparser/autonomy/logs/ssh-probe-bd5600c/latest.json
Varje körning bevaras i egen tidsstämplad katalog; senaste länkarna avser riktig
PuTTY/SSH-start, aldrig lokala simuleringar.

Godkänt först när loggen har START, SSH_DISCONNECT_OBSERVED med
METHOD=origin_terminal_closed, AFTER_DISCONNECT_STATUS och END med
EXIT_CODE=0 och SSH_PERSISTENCE_VERIFIED=1. JSON måste dessutom ange samma
förväntade commit före/efter, rätt workdir och mode=ssh-disconnect-proof.
startens exitkod eller en levande tmux-session räcker inte.

Lokal PTY-stängningskontroll simulerar endast terminalmekanismen, inte faktisk
SSH-överlevnad. Sådana tester märks mode=local-validation och skriver alltid
ssh_persistence_verified=false. Verkligt PuTTY-prov återstår tills ett korrekt
kvitto har skrivits. Ingen prognosutveckling före verifierat kvitto; work-läget
har kvar denna spärr samt kravet på uttrycklig sessionsöverlämning.
