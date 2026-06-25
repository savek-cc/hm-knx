HM-KNX
===

ETS-Integration für das Hörmann/Ing.-Budde **HM-KNX** Garagentor-Gateway
(Hersteller 0x4805): die ETS-Produktdatei (knxprod) sowie die optionale
ETS6-DCA-AddIn-Oberfläche, erstellt mit der
[OpenKNX](https://github.com/OpenKNX)-Toolchain.

> Die knxprod ist **dokumentationsorientiert**: Die HM-KNX-Firmware implementiert
> keine Standard-LoadProcedure (Maske 0x0021). ETS programmiert das Gerät darüber
> nicht - die Datei stellt das Gerät lediglich mit korrekten Gruppenobjekt-Namen,
> DPTs und Flags in ETS dar, sodass Gruppenadressen visuell zugewiesen werden
> können.

Damit erscheint das Gerät in ETS mit benannten Gruppenobjekten; die optionale
DCA-Oberfläche liest und schreibt die KO-Konfiguration direkt aus ETS.

![Kommunikationsobjekte des HM-KNX in ETS mit zugewiesenen Gruppenadressen](docs/ets-gruppenobjekte.png)
*ETS-Ansicht „Kommunikationsobjekte": benannte KOs mit DPTs, Flags und Gruppenadressen (aus der knxprod).*

![DCA-Tab der HM-KNX-AddIn in ETS](docs/ets-dca.png)
*Optionaler DCA-Tab: Identify, Lesen/Schreiben der KO-Konfiguration, Neustart und ProgMode direkt aus ETS.*

## Inhalt

- `knxprod/HM-KNX.xml` - selbstständige Quell-XML der ETS-Produktdatei (Catalog,
  Hardware und ApplicationProgram in einer Datei)
- `dca/` - C#-Quellen der ETS6-DCA-AddIn (.NET Framework 4.8)
- `scripts/sideload-dca.ps1` - installiert die AddIn in ETS6
- `scripts/openssl-sha1.cnf` - OpenSSL-Konfiguration für das Signieren unter Linux

## Voraussetzungen

Neben ETS6 werden zwei Werkzeuge benötigt: ein .NET SDK (zum Kompilieren der DCA)
und der OpenKNXproducer (erzeugt und signiert die knxprod). Alle Beispiele unten
gehen von einer **PowerShell**-Konsole im Wurzelverzeichnis dieses Repos aus.

### ETS6

Liefert die DLLs, mit denen die knxprod signiert wird (`Knx.Ets.XmlSigning.dll`).
Standard-Installationspfad: `C:\Program Files (x86)\ETS6\`.

### .NET SDK (nur für den Bau der DCA)

Der OpenKNXproducer bringt seine eigene Runtime mit; ein .NET SDK wird **nur**
zum Kompilieren der DCA gebraucht. Installation z.B. per winget:

```powershell
winget install Microsoft.DotNet.SDK.8
```

Alternativ der Installer von <https://dotnet.microsoft.com/download>. Es genügt
ein beliebiges aktuelles SDK (getestet mit .NET 8). Visual Studio oder das
.NET-Framework-4.8-Targeting-Pack sind **nicht** nötig: die passenden
Referenz-Assemblies zieht der Build automatisch als NuGet-Paket
(`Microsoft.NETFramework.ReferenceAssemblies`). Dafür ist beim ersten Build eine
Internetverbindung erforderlich.

### OpenKNXproducer (externes Release)

Das Werkzeug erzeugt aus der XML die knxprod und signiert sie. Es ist externer
Code und wird als fertiges Release bezogen (getestet mit **v4.3.9**):

1. Release-ZIP von <https://github.com/OpenKNX/OpenKNXproducer/releases> laden und
   vollständig entpacken.
2. Im entpackten Ordner den Installer ausführen (Rechtsklick auf
   `Install-OpenKNX-Tools.ps1` -> "Mit PowerShell ausführen", oder in einer
   PowerShell-Konsole):

   ```powershell
   PowerShell -ExecutionPolicy Bypass -File .\Install-OpenKNX-Tools.ps1
   ```

   Der Installer kopiert eine self-contained `OpenKNXproducer.exe` nach
   `%USERPROFILE%\bin\`. Dieser Ordner liegt **nicht** automatisch im PATH; die
   Beispiele unten rufen die EXE daher mit vollem Pfad auf. Wer den Aufruf
   verkürzen will, fügt `%USERPROFILE%\bin` einmalig zur PATH-Umgebungsvariable
   hinzu.

> **PowerShell-Ausführungsrichtlinie:** Die `.ps1`-Skripte hier sind nicht
> signiert und werden daher wie gezeigt mit
> `PowerShell -ExecutionPolicy Bypass -File <skript>` gestartet.

## knxprod bauen

```powershell
& "$env:USERPROFILE\bin\OpenKNXproducer.exe" knxprod -o HM-KNX.knxprod knxprod\HM-KNX.xml
```

Unter Windows ist keine zusätzliche Konfiguration nötig; die EXE signiert über die
installierten ETS-DLLs. Die erzeugte `HM-KNX.knxprod` wird in ETS unter
*Kataloge -> Importieren* eingelesen.

Unter Linux/macOS heißt das installierte Werkzeug `OpenKNXproducer` (ohne `.exe`,
abgelegt unter `/usr/local/bin`). Dort blockiert die System-Krypto-Richtlinie
(OpenSSL 3) das von ETS verwendete RSA-SHA1-Signieren; die mitgelieferte
OpenSSL-Konfiguration aktiviert es pro Prozess:

```bash
OPENSSL_CONF=scripts/openssl-sha1.cnf \
  OpenKNXproducer knxprod -o HM-KNX.knxprod knxprod/HM-KNX.xml
```

### Hinweis zur Applikations-ID (wichtig für die DCA)

Beim Signieren ersetzt ETS die ID der Applikation durch einen Inhalts-Hash. Für die
mitgelieferte `HM-KNX.xml` lautet sie `M-00FA_A-4805-05-0D9F`; das DCA-Manifest
(`dca/AddInManifest.xml`) ist darauf gebunden. Wird die `HM-KNX.xml` geändert oder
mit einer anderen ETS-Version signiert, ändert sich diese ID - dann muss die
`ApplicationProgram Id` in `dca/AddInManifest.xml` an die ID der gebauten knxprod
angepasst werden.

## DCA bauen & installieren

Die DCA-AddIn ergänzt im ETS einen Konfigurations-Tab für das Gerät.

1. Bauen (benötigt das .NET SDK aus den Voraussetzungen):

   ```powershell
   dotnet build -c Release dca\HM-KNX.Dca.csproj
   ```

2. Installieren (Sideload). Das Skript kopiert DLL + Manifest in das
   ETS6-AddIns-Verzeichnis:

   ```powershell
   # ETS6 vorher schließen!
   PowerShell -ExecutionPolicy Bypass -File .\scripts\sideload-dca.ps1
   ```

   Erscheint der DCA-Tab nicht, ETS6 schließen,
   `%LocalAppData%\Knx\ETS6\AddInsCache` löschen und ETS6 neu starten.
