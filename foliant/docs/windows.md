# Foliant per Windows

Foliant funziona su **Windows 10 (versione 1809 o successiva) e Windows 11, a 64 bit**, con le
stesse funzioni della versione per Linux: lettura, commenti, modifica di testo e immagini, pagine,
OCR, moduli, firma, protezione, conversione, confronto e operazioni in blocco. Il codice è lo
stesso; le differenze tra i sistemi stanno in `foliant/core/platform.py` (e `core/winscan.py`
per lo scanner).

## Requisiti

- Windows 10 1809 o Windows 11, 64 bit (x64). Su Windows per processori ARM non è stato provato.
- Circa 600 MB liberi su disco.
- Facoltativo: **LibreOffice** (gratuito) solo per creare PDF da documenti Word, Excel e
  PowerPoint. Tutto il resto funziona senza.

L'installatore contiene già, con le rispettive licenze:

| Strumento | A cosa serve | Licenza |
|---|---|---|
| Tesseract OCR, con italiano, inglese e rilevamento dell'orientamento | riconoscimento del testo (OCR) | Apache-2.0 |
| Ghostscript | PDF/A, PDF/X, stampa in blocco | AGPL-3.0 |
| qpdf | visualizzazione web veloce | Apache-2.0 |

## Installazione passo per passo

1. **Scarica** il file `Foliant-<versione>-Setup.exe` (te lo fornisce chi gestisce Foliant; chi
   ha accesso al progetto su GitHub lo trova in *Actions › foliant-windows-build*, nell'ultima
   esecuzione riuscita, sezione *Artifacts*, voce `Foliant-Windows-Setup-<versione>`: si scarica
   un .zip che contiene l'installatore).
2. **Fai doppio clic** sul file. Se compare la finestra blu «Windows ha protetto il PC», fai clic
   su **Ulteriori informazioni** e poi su **Esegui comunque**: l'avviso compare perché
   l'installatore non è firmato digitalmente, non perché sia pericoloso.
3. Scegli se installare **solo per te** (consigliato, non servono i permessi di amministratore)
   oppure per tutti gli utenti del PC.
4. Accetta la licenza (AGPL-3.0) e lascia la cartella proposta.
5. Nella pagina **Altre opzioni** puoi spuntare:
   - **Crea un collegamento sul desktop**;
   - **Usa Foliant come lettore PDF predefinito**: alla fine si aprono le *Impostazioni › App
     predefinite* di Windows; scegli **Foliant** per i file `.pdf` (Windows chiede sempre questa
     conferma all'utente: nessun programma può impostarsi da solo come predefinito).
6. Fai clic su **Installa** e poi su **Fine**.

Foliant si trova ora nel **menu Start** («Foliant»). Puoi aprire un PDF anche con il tasto
destro › **Apri con › Foliant**, o trascinandolo sulla finestra.

### Creare PDF da documenti Office (facoltativo)

Installa LibreOffice da <https://www.libreoffice.org> (versione a 64 bit) con le opzioni
proposte. Non serve configurare nulla: al prossimo avvio Foliant lo trova da solo (cerca in
`C:\Program Files\LibreOffice`).

### Versione portatile (senza installazione)

Il file `Foliant-<versione>-portatile.zip` (artefatto `Foliant-Windows-portatile-<versione>`)
contiene la cartella completa. Estraila dove vuoi, anche su una chiavetta USB, e avvia
`Foliant.exe`. Finché nella cartella c'è il file `portatile.txt`, impostazioni e dati restano
nella sottocartella `dati` e niente viene scritto nel registro di Windows. La versione portatile
non si registra per i file PDF.

### Disinstallazione

*Impostazioni › App › App installate › Foliant › Disinstalla* (oppure dal Pannello di
controllo). Ti viene chiesto se eliminare anche le **preferenze** (tema, lingua, firme salvate,
profili dell'assistente IA e le loro chiavi in Gestione credenziali): rispondi **Sì** per non
lasciare nulla di Foliant sul PC. I tuoi documenti non vengono mai toccati.

Per una disinstallazione silenziosa: `unins000.exe /VERYSILENT /RIMUOVIPREFERENZE=1`.

## Dove stanno i dati di Foliant

| Cosa | Dove |
|---|---|
| Preferenze generali (tema, lingua, unità…) | registro: `HKEY_CURRENT_USER\Software\Foliant` |
| Firme salvate, profili dell'IA, dizionario personale, lingue OCR aggiunte | `%APPDATA%\foliant` |
| Sequenze delle operazioni in blocco | `%APPDATA%\Foliant` |
| Copie di recupero dopo un arresto anomalo (cancellate alla chiusura normale) | `%LOCALAPPDATA%\foliant\recovery` |
| Icone del tema (cache) | `%LOCALAPPDATA%\foliant\cache` |
| Chiavi API, password del server di marca temporale | Gestione credenziali di Windows (voci «foliant-…») |

## Riservatezza su Windows

Come su Linux, Foliant **non conserva nomi, percorsi o cartelle dei file** aperti o salvati, né
cronologie. In particolare su Windows:

- i dialoghi «Apri» e «Salva» sono quelli di Foliant (Qt), non quelli di Windows, che
  registrerebbero i file negli elementi recenti, nella Jump List e nelle cronologie dei dialoghi
  (`ComDlg32`); Foliant non chiama mai `SHAddToRecentDocs` e non crea Jump List;
- la cronologia delle cartelle dei dialoghi di Qt (registro, `HKCU\Software\QtProject`) viene
  rimessa com'era dopo ogni dialogo di Foliant;
- nel registro, sotto `HKCU\Software\Foliant`, ci sono solo preferenze generali, senza percorsi;
- il tipo di file «Foliant.PDF» creato dall'installatore ha l'opzione `NoRecentDocs`: anche i PDF
  aperti con un doppio clic da Esplora file non finiscono tra gli elementi recenti per Foliant.
  Esplora file può comunque ricordare le cartelle che *tu* sfogli con esso: è una sua funzione,
  che si disattiva in *Opzioni cartella › Privacy*.

Lo verifica il test a scatola chiusa `tests/test_no_file_traces.py`, che su Windows controlla il
profilo isolato (`%APPDATA%`, `%LOCALAPPDATA%`, `%TEMP%`), le chiavi di registro di Foliant e di Qt,
le cronologie della shell (RecentDocs, ComDlg32) e le Jump List.

## Differenze e limiti noti

- **Avviso di SmartScreen** all'installazione: l'eseguibile non è firmato (vedi sopra).
- **File aperto in un altro programma**: Windows non permette di sostituire un file aperto da un
  altro programma (ad esempio lo stesso PDF in Acrobat o nell'anteprima di Esplora file). Foliant
  riprova per un secondo; se non basta lo dice chiaramente e propone «Salva con nome».
- **Scanner**: Foliant usa i driver **WIA** di Windows (quelli che fanno funzionare lo scanner con
  l'app «Scanner di Windows»). Gli scanner che hanno solo un driver TWAIN non compaiono:
  acquisisci con il programma del produttore e apri il file in Foliant.
- **Lettura ad alta voce**: usa le voci di Windows (*Impostazioni › Ora e lingua › Voce*; per
  l'italiano aggiungi la lingua italiana con la voce). L'intonazione non è regolabile e tra una
  frase e l'altra c'è una breve pausa.
- **Stampa in blocco**: passa da Ghostscript con il driver della stampante scelta (o di quella
  predefinita); il fronte/retro segue le preferenze del driver. La stampa dalla finestra di
  Foliant (Ctrl+P) usa il dialogo di stampa di Qt come su Linux.
- **Posta elettronica** («Invia per e-mail»): apre un nuovo messaggio con l'allegato nel
  programma di posta predefinito che supporta MAPI (Outlook classico, Thunderbird…). Con la nuova
  app Outlook o con la posta via browser l'allegato va aggiunto a mano: Foliant lo segnala.
- **Font**: dove su Linux si usano Liberation e DejaVu, su Windows si usano i loro equivalenti
  con le stesse misure (Arial, Times New Roman, Courier New) e Segoe UI.
- **Miniature in Esplora file**: non c'è un generatore di miniature per Windows (quello di
  Foliant è solo per i desktop Linux).
- **Istanza singola**: un secondo avvio consegna i file alla finestra già aperta dello stesso
  utente nella stessa sessione di Windows (anche con più utenti collegati o Desktop remoto).

## Riga di comando

```bat
"%LOCALAPPDATA%\Programs\Foliant\foliant-cli.exe" --help
"%LOCALAPPDATA%\Programs\Foliant\foliant-cli.exe" documento.pdf --page 3
"%LOCALAPPDATA%\Programs\Foliant\foliant-cli.exe" blocco --help
```

(Con l'installazione per tutti gli utenti la cartella è `C:\Program Files\Foliant`.)
`Foliant.exe` accetta le stesse opzioni ma non scrive nulla sul terminale.

## Per chi sviluppa

```bat
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m foliant
set QT_QPA_PLATFORM=offscreen && python -m pytest
```

Strumenti per i test: `choco install tesseract ghostscript qpdf` (le lingue italiana e
dell'orientamento si aggiungono in `C:\Program Files\Tesseract-OCR\tessdata`). Foliant li trova
anche se non sono nel PATH; per indicarne uno a mano: variabili `FOLIANT_TESSERACT`,
`FOLIANT_GHOSTSCRIPT`, `FOLIANT_QPDF`, `FOLIANT_SOFFICE`.

GitHub Actions:

- `.github/workflows/foliant-windows.yml`: suite completa su Windows (Python 3.12 e 3.13) e su
  Ubuntu;
- `.github/workflows/foliant-windows-build.yml`: costruisce il pacchetto
  (`packaging/windows/`: PyInstaller con `foliant.spec`, Inno Setup con `foliant.iss`), lo prova,
  lo installa e disinstalla in silenzio e pubblica installatore e versione portatile come
  artefatti.

Il codice specifico di Windows è isolato in `foliant/core/platform.py` (cartelle dell'utente,
processi, strumenti esterni, font, posta, istanza singola, sostituzione dei file) e
`foliant/core/winscan.py` (scanner WIA); i moduli chiedono lì invece di controllare il sistema.
