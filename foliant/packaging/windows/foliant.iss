; Installatore di Foliant per Windows 10 e 11 a 64 bit (Inno Setup 6).
;   iscc packaging\windows\foliant.iss /DAppVersion=0.1.0 /DSourceDir=dist\Foliant /DOutputDir=out /DIconFile=build\foliant.ico
; SourceDir è la cartella prodotta da PyInstaller (foliant.spec) con dentro «tools» e «licenze».
;
; Scelte:
; - installazione per l'utente corrente senza diritti di amministratore (o per tutti, a scelta);
; - voce nel menu Start, collegamento sul desktop facoltativo;
; - Foliant registrato tra i programmi per i PDF («Apri con», App predefinite); la casella «Usa
;   Foliant come lettore PDF predefinito» apre le impostazioni di Windows sulla scelta, perché da
;   Windows 10 nessun programma può imporsi da sé come predefinito;
; - riservatezza: il tipo di file di Foliant ha «NoRecentDocs» (i PDF aperti con Foliant non
;   finiscono negli elementi recenti né nella Jump List);
; - disinstallazione pulita; a richiesta anche preferenze, dati e credenziali di Foliant.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef FileVersion
  ; versione numerica (fino a 4 numeri) per i metadati dei file; AppVersion può avere suffissi
  #define FileVersion AppVersion
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Foliant"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\out"
#endif
#ifndef IconFile
  #define IconFile "..\..\build\foliant.ico"
#endif

[Setup]
AppId={{6F2B1C7E-8A3D-4E59-9C1B-3D7A5E2F0B41}
AppName=Foliant
AppVersion={#AppVersion}
AppVerName=Foliant {#AppVersion}
AppPublisher=Foliant
AppComments=Editor PDF: lettura, commenti, modifica, OCR, moduli, firma, conversione
DefaultDirName={autopf}\Foliant
DefaultGroupName=Foliant
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=Foliant-Setup-{#AppVersion}
VersionInfoVersion={#FileVersion}
VersionInfoProductName=Foliant
VersionInfoProductVersion={#FileVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription=Installazione di Foliant {#AppVersion}
VersionInfoCompany=Foliant
VersionInfoCopyright=AGPL-3.0
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Foliant.exe
UninstallDisplayName=Foliant
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile={#SourceDir}\licenze\AGPL-3.0.txt
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
it.DesktopIcon=Crea un collegamento sul &desktop
en.DesktopIcon=Create a &desktop shortcut
it.DefaultPdf=Usa Foliant come lettore PDF &predefinito
en.DefaultPdf=Use Foliant as the &default PDF reader
it.DefaultPdfRun=Scegli Foliant per i file PDF nelle impostazioni di Windows
en.DefaultPdfRun=Choose Foliant for PDF files in Windows Settings
it.Launch=Avvia Foliant
en.Launch=Launch Foliant
it.PdfDocument=Documento PDF
en.PdfDocument=PDF document
it.RemovePrefs=Vuoi eliminare anche le preferenze di Foliant (tema, lingua, firme salvate, profili dell'assistente IA e relative chiavi)?%n%nI tuoi documenti non vengono toccati.
en.RemovePrefs=Do you also want to delete Foliant's preferences (theme, language, saved signatures, AI assistant profiles and their keys)?%n%nYour documents are not touched.
it.OtherTasks=Altre opzioni:
en.OtherTasks=Other options:

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:OtherTasks}"; Flags: unchecked
Name: "defaultpdf"; Description: "{cm:DefaultPdf}"; GroupDescription: "{cm:OtherTasks}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Foliant"; Filename: "{app}\Foliant.exe"; AppUserModelID: "Foliant.Foliant"; Comment: "Editor PDF"
Name: "{autodesktop}\Foliant"; Filename: "{app}\Foliant.exe"; AppUserModelID: "Foliant.Foliant"; Tasks: desktopicon

[Registry]
; tipo di file di Foliant (ProgID): icona, comando di apertura, nessuna voce tra i recenti
Root: HKA; Subkey: "Software\Classes\Foliant.PDF"; ValueType: string; ValueData: "{cm:PdfDocument}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Foliant.PDF"; ValueType: string; ValueName: "NoRecentDocs"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Foliant.PDF"; ValueType: string; ValueName: "AppUserModelID"; ValueData: "Foliant.Foliant"
Root: HKA; Subkey: "Software\Classes\Foliant.PDF\DefaultIcon"; ValueType: string; ValueData: """{app}\Foliant.exe"",0"
Root: HKA; Subkey: "Software\Classes\Foliant.PDF\shell\open\command"; ValueType: string; ValueData: """{app}\Foliant.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "Foliant.PDF"; ValueData: ""; Flags: uninsdeletevalue
; applicazione registrata: «Apri con» e App predefinite
Root: HKA; Subkey: "Software\Classes\Applications\Foliant.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "Foliant"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\Foliant.exe"; ValueType: string; ValueName: "NoRecentDocs"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\Foliant.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\Foliant.exe\shell\open\command"; ValueType: string; ValueData: """{app}\Foliant.exe"" ""%1"""
Root: HKA; Subkey: "Software\FoliantApp\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "Foliant"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\FoliantApp\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Editor PDF"
Root: HKA; Subkey: "Software\FoliantApp\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: """{app}\Foliant.exe"",0"
Root: HKA; Subkey: "Software\FoliantApp\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "Foliant.PDF"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "Foliant"; ValueData: "Software\FoliantApp\Capabilities"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\Foliant.exe"; ValueType: string; ValueData: "{app}\Foliant.exe"; Flags: uninsdeletekey
; lettore predefinito dove Windows non ha ancora una scelta dell'utente (sistemi nuovi)
Root: HKA; Subkey: "Software\Classes\.pdf"; ValueType: string; ValueData: "Foliant.PDF"; Tasks: defaultpdf; Flags: uninsdeletevalue

[Run]
Filename: "{code:DefaultAppsUri}"; Description: "{cm:DefaultPdfRun}"; Tasks: defaultpdf; Flags: shellexec postinstall nowait skipifsilent
Filename: "{app}\Foliant.exe"; Description: "{cm:Launch}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
; preferenze e dati dell'utente: solo se chiesto (domanda in InitializeUninstall o /RIMUOVIPREFERENZE=1)
Filename: "{app}\foliant-cli.exe"; Parameters: "rimuovi-dati-utente"; Flags: runhidden waituntilterminated; Check: RemovePreferences; RunOnceId: "RimuoviDatiUtente"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  RemovePrefs: Boolean;

function DefaultAppsUri(Param: String): String;
begin
  { Windows 11 apre direttamente la pagina di Foliant; Windows 10 la pagina generale }
  if IsAdminInstallMode then
    Result := 'ms-settings:defaultapps?registeredAppMachine=Foliant'
  else
    Result := 'ms-settings:defaultapps?registeredAppUser=Foliant';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  RemovePrefs := ExpandConstant('{param:RIMUOVIPREFERENZE|0}') = '1';
  if (not RemovePrefs) and (not UninstallSilent) then
    RemovePrefs := MsgBox(CustomMessage('RemovePrefs'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

function RemovePreferences(): Boolean;
begin
  Result := RemovePrefs;
end;
