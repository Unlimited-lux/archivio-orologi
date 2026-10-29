# Foliant per Windows

Foliant funziona su **Windows 10 (versione 1809 o successiva) e Windows 11, a 64 bit**, con le
stesse funzioni della versione per Linux: lettura, commenti, modifica di testo e immagini, pagine,
OCR, moduli, firma, protezione, conversione, confronto e operazioni in blocco. Il codice è lo
stesso; le differenze tra i sistemi stanno in `foliant/core/platform.py` (e `core/winscan.py`
per lo scanner).

## In breve

1. Scarica `Foliant-Setup-<versione>.exe` da GitHub (vedi sotto).
2. Fai doppio clic; se Windows mostra «Windows ha protetto il PC», scegli **Ulteriori
   informazioni › Esegui comunque**.
3. Avanti, Avanti, **Installa**. Foliant è nel menu Start.

## Requisiti

- Windows 10 (versione 1809 o successiva) o Windows 11, 64 bit (x64). Su Windows per processori
  ARM non è stato provato.
- Circa 600 MB liberi su disco. Non servono i permessi di amministratore.
- Facoltativo: **LibreOffice** (gratuito), solo per creare PDF da documenti Word, Excel e
  PowerPoint. Tutto il resto funziona senza.

L'installatore contiene già, con i testi delle licenze nella cartella `licenze` del programma:

| Strumento | A cosa serve | Licenza |
|---|---|---|
| Tesseract OCR, con italiano, inglese e rilevamento dell'orientamento | riconoscimento del testo (OCR) | Apache-2.0 |
| Ghostscript | PDF/A, PDF/X, stampa in blocco | AGPL-3.0 |
| qpdf | visualizzazione web veloce | Apache-2.0 |

Foliant stesso è distribuito con licenza AGPL-3.0.

## 1. Scaricare l'installatore da GitHub

L'installatore viene costruito automaticamente da GitHub a ogni aggiornamento di Foliant e resta
scaricabile per **30 giorni**. Serve un account GitHub con accesso al progetto.

1. Apri la pagina del progetto su GitHub e fai clic sulla scheda **Actions** in alto.
2. Nella colonna di sinistra scegli **foliant-windows-build**.
3. Fai clic sull'esecuzione più recente con il **segno di spunta verde** (✓): quella con la
   croce rossa non è andata a buon fine.
4. Scorri la pagina fino a **Artifacts** e fai clic su:
   - **Foliant-Setup-<versione>** per l'installatore (consigliato);
   - **Foliant-portable-<versione>** per la versione portatile, senza installazione.
5. GitHub scarica un file `.zip`. Aprilo dalla cartella *Download* (doppio clic) e trascina il file
   `Foliant-Setup-<versione>.exe` sul desktop, oppure fai clic destro sul .zip › **Estrai tutto**.

Se ricevi l'installatore da un collega (chiavetta, cartella condivisa), parti dal punto 2 della
sezione seguente.

## 2. Installare

1. **Fai doppio clic** su `Foliant-Setup-<versione>.exe`.
2. Se compare l'avviso di SmartScreen (finestra blu «Windows ha protetto il PC»), vedi la
   sezione [Avviso di Windows SmartScreen](#avviso-di-windows-smartscreen): fai clic su
   **Ulteriori informazioni** e poi su **Esegui comunque**.
3. Scegli se installare **solo per te** (consigliato: non servono i permessi di amministratore)
   oppure per tutti gli utenti del PC (Windows chiederà la password di un amministratore).
4. Accetta la licenza (AGPL-3.0) e lascia la cartella proposta.
5. Nella pagina **Altre opzioni** puoi spuntare:
   - **Crea un collegamento sul desktop**;
   - **Usa Foliant come lettore PDF predefinito**: alla fine si aprono le *Impostazioni › App
     predefinite* di Windows; scegli **Foliant** per i file `.pdf`. Windows chiede sempre questa
     conferma all'utente: nessun programma può impostarsi da solo come predefinito.
6. Fai clic su **Installa** e poi su **Fine**.

### Avviso di Windows SmartScreen

L'installatore di Foliant **non è firmato digitalmente** (la firma richiede un certificato a
pagamento). Per questo, la prima volta, Windows può mostrare:

> **Windows ha protetto il PC** — Microsoft Defender SmartScreen ha impedito l'avvio di un'app
> non riconosciuta.

Per procedere:

1. fai clic sul collegamento **Ulteriori informazioni** (sotto il testo);
2. controlla che il nome del file sia `Foliant-Setup-<versione>.exe` e che l'editore sia
   «Editore sconosciuto»;
3. fai clic su **Esegui comunque**.

L'avviso compare perché il programma è nuovo e non firmato, non perché sia pericoloso. Procedi
solo se hai scaricato il file dalla pagina GitHub del progetto o lo hai ricevuto da una persona
di fiducia. Se il pulsante **Esegui comunque** non c'è, il PC è gestito da un'organizzazione che
blocca i programmi non firmati: chiedi all'amministratore. Se il browser (Edge, Chrome) segnala
che il file «non viene scaricato di frequente», scegli **Mantieni** dal menu del download.

## 3. Primo avvio

- Apri **Start** e scrivi «Foliant», oppure usa il collegamento sul desktop.
- Per aprire un PDF: **Apri** nella schermata iniziale, trascina il file sulla finestra, oppure
  in Esplora file tasto destro sul PDF › **Apri con › Foliant**.
- La lingua dell'interfaccia (italiano o inglese), il tema chiaro o scuro e il nome dell'autore
  dei commenti si cambiano in **Preferenze** (Ctrl+,). Ctrl+? mostra tutte le scorciatoie.
- L'OCR in italiano e in inglese funziona subito: Tesseract è già incluso.
- Se un altro programma (Acrobat, l'anteprima di Esplora file) tiene aperto lo stesso PDF,
  Windows non permette di sovrascriverlo: Foliant lo dice e propone «Salva con nome».

## 4. Creare PDF da documenti Office (facoltativo: LibreOffice)

Per trasformare in PDF documenti Word, Excel e PowerPoint Foliant usa **LibreOffice**, che non è
incluso (è grande e serve solo a questo). Se manca, Foliant mostra il messaggio «Serve il
programma LibreOffice… Installa LibreOffice da https://www.libreoffice.org (gratuito) e riprova».

1. Vai su <https://www.libreoffice.org> › **Scarica** e prendi la versione per **Windows x86-64**.
2. Installalo con le opzioni proposte.
3. Riapri Foliant: lo trova da solo (cerca in `C:\Program Files\LibreOffice`), senza configurare nulla.

## Versione portatile (senza installazione)

Il file `Foliant-portable-<versione>.zip` (artefatto `Foliant-portable-<versione>`) contiene la
cartella completa. Estraila dove vuoi, anche su una chiavetta USB, e avvia `Foliant.exe`. Finché
nella cartella c'è il file `portatile.txt`, impostazioni e dati di Foliant restano nella
sottocartella `dati`, non nel registro né nel profilo di Windows (resta solo l'aspetto del dialogo
dei file, vista e colonne, che Qt tiene in `HKCU\Software\QtProject`). La versione portatile non
si registra per i file PDF e non compare in *App installate*: per toglierla basta cancellare la
cartella.

## 5. Disinstallare

*Impostazioni › App › App installate* (Windows 11) o *App e funzionalità* (Windows 10) ›
**Foliant** › **Disinstalla** (oppure dal Pannello di controllo › Programmi). Viene chiesto se
eliminare anche le **preferenze** (tema, lingua, firme salvate, profili dell'assistente IA e le
loro chiavi in Gestione credenziali):

- **Sì**: non resta nulla di Foliant sul PC (né `HKCU\Software\Foliant` nel registro, né le
  cartelle `foliant` in `%APPDATA%` e `%LOCALAPPDATA%`);
- **No** (risposta proposta): le preferenze restano e ritrovi tutto se reinstalli Foliant.

I tuoi documenti non vengono mai toccati. Programma, collegamenti, voce «Apri con» e
associazione dei PDF vengono sempre rimossi.

Disinstallazione silenziosa (per chi gestisce più PC), dalla cartella del programma:
`unins000.exe /VERYSILENT` (preferenze conservate) oppure `unins000.exe /VERYSILENT
/RIMUOVIPREFERENZE=1` (preferenze eliminate). Installazione silenziosa:
`Foliant-Setup-<versione>.exe /VERYSILENT /CURRENTUSER /TASKS="desktopicon"`.

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

- **Avviso di SmartScreen** all'installazione: l'installatore e i programmi non sono firmati
  digitalmente (vedi sopra); alcuni antivirus possono segnalarli per lo stesso motivo.
- **Aggiornamenti**: Foliant non si aggiorna da solo. Per una nuova versione scarica il nuovo
  installatore e installalo sopra: le preferenze restano.
- **LibreOffice** non è incluso (vedi sopra): senza, non si creano PDF da documenti Office.
- **Lingue OCR**: sono incluse italiano e inglese; altre lingue si aggiungono dalle preferenze
  dell'OCR.
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
- `.github/workflows/foliant-windows-build.yml`, lavoro **build**: costruisce il pacchetto
  (`packaging/windows/`: PyInstaller in cartella unica con `foliant.spec`, Foliant.exe senza console
  e foliant-cli.exe; strumenti raccolti da `collect_tools.ps1`; Inno Setup con `foliant.iss`) e
  pubblica `Foliant-Setup-<versione>.exe` e `Foliant-portable-<versione>.zip` come artefatti per
  30 giorni;
- stesso workflow, lavoro **test-installer** su una macchina Windows pulita
  (`packaging/windows/prova/prova_installatore.ps1`): installazione silenziosa, file, collegamenti,
  tipo di file e versione; `foliant-cli.exe --version`; una sessione senza schermo di Foliant.exe
  che apre, modifica, salva e chiude un PDF (`sessione.py`); con `foliant-cli blocco` OCR in
  italiano di una pagina scansionata con il Tesseract incluso e conversione in DOCX, compressione e
  PDF/A con il Ghostscript incluso, più una compressione diretta con Ghostscript
  (`verifica_risultati.py` controlla il testo riconosciuto e i file); poi la **riservatezza**:
  nomi e percorsi dei file di prova non devono comparire in `%APPDATA%`, `%LOCALAPPDATA%`, negli
  elementi recenti di Windows né nel registro (`HKCU\Software\Foliant`, `HKCU\Software\QtProject`,
  RecentDocs, ComDlg32) (`cerca_tracce.py`); infine disinstallazione senza preferenze (restano),
  reinstallazione e disinstallazione con `/RIMUOVIPREFERENZE=1` (spariscono), con il controllo
  che file, collegamenti e voci del registro del programma siano spariti.

La compressione di Foliant («Comprimi») usa il motore PDF (PyMuPDF) e non Ghostscript; Ghostscript
serve a PDF/A, PDF/X e stampa in blocco. Per questo la prova usa Ghostscript attraverso Foliant
con la conversione in PDF/A, che riscrive e comprime il file, e direttamente con `/ebook`.

Il codice specifico di Windows è isolato in `foliant/core/platform.py` (cartelle dell'utente,
processi, strumenti esterni, font, posta, istanza singola, sostituzione dei file) e
`foliant/core/winscan.py` (scanner WIA); i moduli chiedono lì invece di controllare il sistema.
